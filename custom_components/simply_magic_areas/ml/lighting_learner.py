"""Online Bayesian Preference Learner for Adaptive Lighting in Simply Magic Areas.

Operates 100% locally on Home Assistant hardware with zero external dependencies
and zero cloud connectivity. Utilizes Recursive Least Squares (RLS) / Bayesian Ridge
Regression to continuously learn user brightness preferences from manual dimmer/slider
interactions without batch training.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = "simply_magic_areas_lighting_ml_"

# Feature dimension: [bias, log(lux), sin(solar_elevation), profile_factor]
DIM = 4
DEFAULT_NOISE_VAR = 25.0


def _get_feature_vector(
    lux: float | None,
    solar_elevation: float | None,
    profile: str | None,
) -> list[float]:
    """Construct normalized feature vector x in R^4."""
    # x0: Bias
    x0 = 1.0

    # x1: Perceptual log-illuminance normalized to [0, 1]
    safe_lux = max(0.0, float(lux)) if lux is not None else 0.0
    x1 = math.log(1.0 + safe_lux) / math.log(1001.0)
    x1 = min(1.0, max(0.0, x1))

    # x2: Solar elevation angle (-90 to +90 degrees -> [-1, 1])
    if solar_elevation is not None:
        elev_deg = max(-90.0, min(90.0, float(solar_elevation)))
        x2 = math.sin(math.radians(elev_deg))
    else:
        x2 = 0.0

    # x3: Profile factor (standard=1.0, night=0.5, sleep=0.25)
    prof_lower = (str(profile) if profile else "").lower()
    if "sleep" in prof_lower:
        x3 = 0.25
    elif "night" in prof_lower:
        x3 = 0.50
    elif "bright" in prof_lower:
        x3 = 1.25
    else:
        x3 = 1.00

    return [x0, x1, x2, x3]


class OnlineBayesianLightingLearner:
    """Local Bayesian Preference Model for continuous personalized brightness adaptation."""

    def __init__(self, hass: HomeAssistant, area_slug: str) -> None:
        """Initialize the local Bayesian learner for an area."""
        self.hass = hass
        self.area_slug = area_slug
        self._store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}{area_slug}")

        # Weights: [bias, log_lux, solar_sin, profile_factor]
        self._weights: list[float] = [220.0, -160.0, 0.0, 25.0]

        # Prior covariance matrix P = 100.0 * I
        self._cov: list[list[float]] = [
            [100.0 if i == j else 0.0 for j in range(DIM)] for i in range(DIM)
        ]

        self._sample_count: int = 0
        self._last_predicted_brightness: int | None = None
        self._is_loaded: bool = False

    @property
    def sample_count(self) -> int:
        """Return the number of user manual override samples learned."""
        return self._sample_count

    @property
    def confidence(self) -> float:
        """Confidence score from 0.0 to 1.0 based on sample count."""
        return min(1.0, self._sample_count / 5.0)

    async def async_load(self) -> None:
        """Load learned parameters from Home Assistant local storage."""
        try:
            data = await self._store.async_load()
            if data and isinstance(data, dict):
                weights = data.get("weights")
                cov = data.get("cov")
                samples = data.get("sample_count", 0)

                if (
                    isinstance(weights, list)
                    and len(weights) == DIM
                    and isinstance(cov, list)
                    and len(cov) == DIM
                ):
                    self._weights = [float(w) for w in weights]
                    self._cov = [[float(val) for val in row] for row in cov]
                    self._sample_count = int(samples)
                    _LOGGER.debug(
                        "%s: Loaded ML lighting preferences (%s samples)",
                        self.area_slug,
                        self._sample_count,
                    )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "%s: Could not load ML lighting preferences from storage: %s",
                self.area_slug,
                err,
            )
        finally:
            self._is_loaded = True

    async def async_save(self) -> None:
        """Save learned parameters to local Home Assistant storage."""
        try:
            payload = {
                "weights": self._weights,
                "cov": self._cov,
                "sample_count": self._sample_count,
            }
            await self._store.async_save(payload)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "%s: Failed to save ML lighting preferences: %s",
                self.area_slug,
                err,
            )

    def learn_preference(
        self,
        lux: float | None,
        profile: str | None,
        solar_elevation: float | None,
        target_brightness: int,
    ) -> None:
        """Online recursive Bayesian update when user performs a manual brightness override."""
        if target_brightness <= 0 or target_brightness > 255:
            return

        x = _get_feature_vector(lux, solar_elevation, profile)

        # v = P * x
        v = [sum(self._cov[i][j] * x[j] for j in range(DIM)) for i in range(DIM)]

        # denom = sigma_n^2 + x^T * v
        x_dot_v = sum(x[i] * v[i] for i in range(DIM))
        denom = DEFAULT_NOISE_VAR + x_dot_v

        if denom <= 1e-6:
            return

        # Kalman / RLS gain vector k = v / denom
        k = [v[i] / denom for i in range(DIM)]

        # Prior prediction error: e = y - w^T * x
        y_prior = sum(self._weights[i] * x[i] for i in range(DIM))
        err = float(target_brightness) - y_prior

        # Update weights: w_new = w + k * err
        self._weights = [self._weights[i] + k[i] * err for i in range(DIM)]

        # Rank-1 covariance update: P_new = P - k * v^T
        new_cov: list[list[float]] = []
        for i in range(DIM):
            row: list[float] = []
            for j in range(DIM):
                val = self._cov[i][j] - k[i] * v[j]
                row.append(val)
            new_cov.append(row)

        # Ensure symmetry and positive diagonal
        for i in range(DIM):
            for j in range(i + 1, DIM):
                avg = (new_cov[i][j] + new_cov[j][i]) / 2.0
                new_cov[i][j] = avg
                new_cov[j][i] = avg
            new_cov[i][i] = max(1e-4, new_cov[i][i])

        self._cov = new_cov
        self._sample_count += 1

        _LOGGER.debug(
            "%s: ML Lighting learned sample #%d (target=%d, prior=%.1f, lux=%s, weights=%s)",
            self.area_slug,
            self._sample_count,
            target_brightness,
            y_prior,
            lux,
            [round(w, 2) for w in self._weights],
        )

        # Persist locally in background (thread-safe)
        if self.hass.loop.is_running():
            self.hass.loop.call_soon_threadsafe(
                lambda: self.hass.async_create_task(self.async_save())
            )

    def predict_brightness(
        self,
        lux: float | None,
        profile: str | None,
        solar_elevation: float | None,
        default_brightness: int,
    ) -> int:
        """Predict personalized brightness blending default heuristic with Bayesian learned curve."""
        # If default rule shut the light off due to bright sunlight, respect daylight cutoff
        if default_brightness <= 0:
            self._last_predicted_brightness = 0
            return 0

        # If zero samples recorded yet, return default baseline
        if self._sample_count == 0:
            self._last_predicted_brightness = default_brightness
            return default_brightness

        x = _get_feature_vector(lux, solar_elevation, profile)
        y_raw = sum(self._weights[i] * x[i] for i in range(DIM))
        y_clamped = max(1, min(255, round(y_raw)))

        # Smoothly blend: confidence C = min(1.0, samples / 5)
        conf = self.confidence
        blended = round((1.0 - conf) * default_brightness + conf * y_clamped)
        result = max(1, min(255, blended))

        self._last_predicted_brightness = result
        return result

    def get_attributes(self) -> dict[str, Any]:
        """Return state attributes for diagnostic and UI visibility."""
        return {
            "ml_adaptive_lighting_active": True,
            "ml_preference_samples": self._sample_count,
            "ml_confidence": round(self.confidence, 2),
            "ml_predicted_brightness": self._last_predicted_brightness,
        }
