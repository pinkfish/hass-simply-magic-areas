"""Machine learning shower classifier and moisture evacuation trajectory modeling.

100% on-device, local execution with zero cloud connectivity or external C libraries.
"""

from __future__ import annotations

import logging
import math

_LOGGER = logging.getLogger(__name__)


class ShowerClassifier:
    """Multi-sensor logistic differential classifier for shower detection.

    Differentiates localized shower humidity surges from broad weather fronts,
    cooking steam, and ambient sensor drift using multi-sensor differential signals:
    - dRH/dt (humidity rate of change)
    - d2RH/dt2 (humidity acceleration)
    - dT/dt (temperature flux; positive for warm showers, <= 0 for weather fronts)
    - delta_H_meta (localized area humidity delta relative to interior/exterior meta reference)
    - door_closed (concentrated steam physical geometry)
    """

    def __init__(self) -> None:
        """Initialize the classifier state."""
        self._last_trend: float | None = None
        self._last_trend_ts: float | None = None
        self._last_temp: float | None = None
        self._last_temp_ts: float | None = None

    def update_temperature(self, temp: float, ts: float) -> None:
        """Record temperature measurement."""
        self._last_temp = temp
        self._last_temp_ts = ts

    def compute_probability(
        self,
        trend: float,
        ts: float,
        area_humidity: float,
        ref_humidity: float | None,
        area_temp: float | None = None,
        door_closed: bool | None = None,
        historical_baseline: float | None = None,
    ) -> float:
        """Compute the calibrated probability P(shower) in [0.0, 1.0]."""
        # 1. Acceleration d2RH/dt2
        d2rh_dt2 = 0.0
        if self._last_trend is not None and self._last_trend_ts is not None:
            dt = ts - self._last_trend_ts
            if 0.5 <= dt <= 180.0:
                d2rh_dt2 = (trend - self._last_trend) / dt
        self._last_trend = trend
        self._last_trend_ts = ts

        # 2. Temperature flux dT/dt
        dt_temp = 0.0
        if area_temp is not None:
            if self._last_temp is not None and self._last_temp_ts is not None:
                dt_s = ts - self._last_temp_ts
                if 1.0 <= dt_s <= 300.0:
                    dt_temp = (area_temp - self._last_temp) / dt_s
            self._last_temp = area_temp
            self._last_temp_ts = ts

        # 3. Meta differential vs storm front rejection
        delta_meta = 0.0
        has_ref = False
        if ref_humidity is not None:
            delta_meta = area_humidity - ref_humidity
            has_ref = True
        elif historical_baseline is not None:
            delta_meta = area_humidity - historical_baseline

        # Storm front detection: if reference is available and area delta is negligible
        # and temperature is not rising, this is an external atmospheric shift
        is_storm_front = has_ref and delta_meta <= 1.0 and dt_temp <= 0.0

        # Logistic linear combination
        w0 = -2.5
        w1 = 1.8
        w2 = 0.5
        w3 = 1.5
        w4 = 0.35
        w5 = 0.8 if door_closed is True else 0.0

        z = (
            w0
            + w1 * (trend * 100.0)
            + w2 * (d2rh_dt2 * 1000.0)
            + w3 * (dt_temp * 100.0)
            + w4 * max(0.0, delta_meta)
            + w5
        )

        if is_storm_front:
            z -= 4.0

        # High absolute humidity failsafe
        if area_humidity >= 80.0:
            z += 4.0

        # Sigmoid activation
        z_clamped = max(-10.0, min(10.0, z))
        prob = 1.0 / (1.0 + math.exp(-z_clamped))
        return round(prob, 3)


class MoistureEvacuationModel:
    """Dynamic exponential drying trajectory modeling for optimal fan shutoff.

    Fits Newton's Law of Evacuation: H(t) = H_base + Delta_H_peak * exp(-k * t)
    using closed-form log-linear least squares regression:
        ln(H(t) - H_base) = ln(Delta_H_peak) - k * t
    """

    def __init__(self) -> None:
        """Initialize the trajectory model."""
        self._samples: list[tuple[float, float]] = []  # (ts, excess_humidity)
        self._peak_ts: float | None = None
        self._peak_delta: float | None = None
        self._fitted_k: float | None = None
        self._estimated_dry_ts: float | None = None

    def reset(self) -> None:
        """Reset trajectory data for a new shower cycle."""
        self._samples.clear()
        self._peak_ts = None
        self._peak_delta = None
        self._fitted_k = None
        self._estimated_dry_ts = None

    @property
    def drying_rate_k(self) -> float | None:
        """Return the fitted evacuation decay rate k (1/s)."""
        return self._fitted_k

    @property
    def estimated_dry_ts(self) -> float | None:
        """Return the estimated timestamp when target moisture will be reached."""
        return self._estimated_dry_ts

    def record_peak(self, peak_delta: float, peak_ts: float) -> None:
        """Record the peak moisture delta and timestamp."""
        self._peak_delta = peak_delta
        self._peak_ts = peak_ts
        self._samples = [(peak_ts, peak_delta)]

    def record_sample(
        self,
        current_humidity: float,
        baseline_humidity: float,
        now_ts: float,
        delta_shutoff: float,
    ) -> tuple[float | None, float | None]:
        """Record a drying sample and update closed-form exponential fit.

        Returns (fitted_k, estimated_dry_ts).
        """
        excess = max(0.05, current_humidity - baseline_humidity)

        if self._peak_ts is None:
            self._peak_ts = now_ts
            self._peak_delta = excess
            self._samples = [(now_ts, excess)]
            return None, None

        self._samples.append((now_ts, excess))
        if len(self._samples) > 20:
            self._samples = [self._samples[0]] + self._samples[-19:]

        if len(self._samples) < 3:
            return self._fitted_k, self._estimated_dry_ts

        # Time elapsed since peak
        t0 = self._peak_ts
        points: list[tuple[float, float]] = []
        for s_ts, s_excess in self._samples:
            dt = s_ts - t0
            if s_excess > 0:
                points.append((dt, math.log(max(0.01, s_excess))))

        if len(points) < 3 or (points[-1][0] - points[0][0]) < 10.0:
            return self._fitted_k, self._estimated_dry_ts

        # Closed-form simple linear regression: ln(y) = intercept - k * t
        n = len(points)
        sum_t = sum(p[0] for p in points)
        sum_z = sum(p[1] for p in points)
        mean_t = sum_t / n
        mean_z = sum_z / n

        ss_tt = sum((p[0] - mean_t) ** 2 for p in points)
        ss_tz = sum((p[0] - mean_t) * (p[1] - mean_z) for p in points)

        if ss_tt > 1e-6:
            slope = ss_tz / ss_tt  # expected to be negative as humidity declines
            k = -slope
            if k > 0.0001:  # Physical decay constraint
                self._fitted_k = round(k, 5)
                target_excess = max(0.5, delta_shutoff)
                initial_excess = max(target_excess * 1.1, self._peak_delta or points[0][1])
                ln_ratio = math.log(initial_excess / target_excess)
                t_target_s = ln_ratio / k
                self._estimated_dry_ts = round(t0 + t_target_s, 1)

        return self._fitted_k, self._estimated_dry_ts

    def is_evacuation_complete(
        self,
        current_humidity: float,
        baseline_humidity: float,
        now_ts: float,
        delta_shutoff: float,
    ) -> bool:
        """Check if exponential evacuation has reached the dry threshold."""
        if self._estimated_dry_ts is not None and now_ts >= self._estimated_dry_ts:
            excess = current_humidity - baseline_humidity
            if excess <= (delta_shutoff + 1.0):
                return True
        return False
