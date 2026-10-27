# Simply Magic Areas for Home Assistant

Originally forked from [jseidl/hass-magic_areas](https://github.com/jseidl/hass-magic_areas), **Simply Magic Areas** has evolved into a next-generation autonomous area intelligence platform for Home Assistant. It features 100% on-device local machine learning, radar fan discrimination, thermodynamic moisture evacuation modeling, and hierarchical spatial configuration inheritance.

![Simply Magic Areas](https://raw.githubusercontent.com/home-assistant/brands/master/custom_integrations/simply_magic_areas/icon.png)

![Build Status](https://github.com/pinkfish/hass-simply-magic-areas/actions/workflows/validation.yaml/badge.svg) ![Test Status](https://github.com/pinkfish/hass-simply-magic-areas/actions/workflows/test.yaml/badge.svg) [![Latest release](https://img.shields.io/github/v/release/pinkfish/hass-simply-magic-areas.svg)](https://github.com/pinkfish/hass-simply-magic-areas/releases) [![System Spec](https://img.shields.io/badge/Architecture-SYSTEM__SPEC-blue.svg)](SYSTEM_SPEC.md) [![GitHub latest commit](https://badgen.net/github/last-commit/pinkfish/hass-simply-magic-areas)](https://github.com/pinkfish/hass-simply-magic-areas/commit/) [![GitHub contributors](https://badgen.net/github/contributors/pinkfish/hass-simply-magic-areas)](https://github.com/pinkfish/hass-simply-magic-areas/graphs/contributors/)

Tired of writing the same automations over and over for every room? Tired of complex YAML templates for motion grouping, dim levels, and fan timers?

**Simply Magic Areas** gives Home Assistant the "missing batteries" for whole-home automation. It groups all presence and environmental sensors per area, maintains orthogonal presence and lighting states, continuously learns your personal brightness preferences via online Bayesian regression, discriminates false motion caused by spinning fan blades on mmWave radars, and models moisture evacuation curves to ventilate bathrooms perfectly without getting stuck running forever. Detailed mathematical formulations and architecture diagrams are documented in the [System Specification (SYSTEM_SPEC.md)](SYSTEM_SPEC.md).

---

## 🌟 Key Features

### 🧠 On-Device Machine Learning & Signal Processing (Zero-Cloud)
* **Online Bayesian Lighting Preference Learner**: Real-time Recursive Least Squares (RLS) / Bayesian Ridge Regression (<1µs execution) continuously adapts light brightness to match your manual wall-switch and dimmer adjustments across lux levels and solar elevations. Preserves daylight shutoffs, blends prior heuristics, and persists weights locally across restarts.
* **mmWave Radar Fan vs. Human False-Presence Filter**: Solves mmWave radar "ghosting" where rotating ceiling, desk, or exhaust fan blades trap rooms in permanent false occupancy. Combines actuator causal tracking, distance spatial masking ($1.8\text{m} \le \text{dist} \le 3.5\text{m}$), and **Multi-Signal Sensor Element Evaluation** (evaluates secondary radar signals like macro-motion energy $\ge 20$, multi-target coordinates outside the fan zone, and companion PIR/motion sensors to determine if a human is in the room even while the fan is spinning).
* **Dual-Stage Shower Classifier & Exponential Moisture Evacuation Model**: Combines rate-of-change ($dRH/dt$), acceleration ($d^2RH/dt^2$), thermal flux ($dT/dt$), and door contact states to reject false positive ventilation triggers (e.g. storm fronts, HVAC). Once showering stops, dynamic exponential regression fits air exchange rate $k$ ($\ln(\Delta H(t)) = \ln(\Delta H_{peak}) - kt$) to predict the exact shutoff timestamp ($t_{dry}$), backed by pre-shower baseline return shutoffs that are immune to inter-room calibration differences.

### 💡 Lighting & Presence Architecture
* **Dual-Sensor Orthogonal State Architecture**: Decouples physical occupancy (`sensor.simply_magic_areas_state_<area>`) exposing `clear`, `occupied`, `extended`, `manual` from contextual lighting scene profiles (`sensor.simply_magic_areas_lighting_profile_<area>`) exposing `standard`, `sleep`, `bright`, `accented`.
* **Hierarchical Meta-Area Configuration Inheritance**: Meta-Areas (`Global`, `Interior`, `Exterior`) serve as centralized policy hubs. Settings like sleep entities, accent scenes, timeouts, and dark floors are defined once and automatically cascade down to subordinate rooms.
* **Occupancy-Hold Manual Override**: Manual switch or dashboard adjustments hold their commanded state until an **occupancy change** occurs (`occupied/extended -> clear` or `clear -> occupied`), preventing automations from abruptly overriding human actions while the room is occupied.
* **Exterior Nighttime Lighting & Motion Extension**: Exterior areas activate automatically at sunset with a stay-on timer (default 2 hours). Motion detection, camera triggers, or resident arrivals dynamically refresh the stay-on clock from the moment motion ceases.
* **Adaptive Photometric Lux Control & Dark Floor Floor Guarantee**: Continuous daytime baseline calibration with an absolute dark floor guarantee preventing windowless rooms (pantries, closets, bathrooms) from falsely staying dark.

### 🌿 Aggregation & Automation Batteries
* **Area Sensor Aggregations**: Automatic aggregate groups (`all`, `any`, `mean`, `sum`) for motion, doors, windows, locks, leaks, temperature, humidity, and illuminance.
* **Health & Safety Sensors**: Tracks problems, smoke, gas, and water leaks per area.
* **Area-Aware Media Player**: Routes media playback commands exclusively to occupied zones.
* **Acoustic Sleep Fan Suppression**: Suppresses noisy bathroom exhaust fans during sleep hours unless overridden by high differential moisture.

---

## 🚀 Setting Up the Machine Learning Features

All machine learning and advanced multi-sensor signal processing features in Simply Magic Areas run **100% locally on your Home Assistant server** with zero cloud calls, zero external API keys, and zero telemetry.

They are **adaptive by default**—no manual tuning is required for everyday operation.

### 1. Bayesian Lighting Preference Learner
* **Where to find it**: Go to **Settings** > **Devices & Services** > **Simply Magic Areas** > Select your area > **Configure** > **Advanced Light Groups**.
* **Toggle**: Ensure **Learn Lighting Preferences** is checked (`true` by default).
* **How it learns**: Whenever lights in the area turn on, Simply Magic Areas applies an initial heuristic based on the area lighting profile, ambient lux, and solar elevation. Whenever you adjust a physical dimmer switch or the Home Assistant brightness slider, the learner captures the adjustment:
  * Updates its Bayesian posterior distribution in $< 1\mu\text{s}$.
  * As sample counts increase, model confidence grows from $0.0$ to $1.0$.
  * Subsequent turn-ons smoothly blend learned preferences with safety heuristics.
* **Entity Attributes**: Inspect `light.simply_magic_areas_light_<area>`:
  * `ml_adaptive_lighting_active`: `true`
  * `ml_preference_samples`: Number of captured interactions.
  * `ml_confidence`: Statistical confidence score ($0.0$ to $1.0$).
  * `ml_predicted_brightness`: The predicted brightness level ($1 - 255$).
* **Storage**: Weights are automatically saved to `.storage/simply_magic_areas_lighting_ml_<area_slug>`.

### 2. mmWave Radar Fan vs. Human Discrimination
* **Where to find it**: Select your area > **Configure** > **Advanced Light Groups**.
* **Toggle**: Ensure **mmWave Fan Filter** is enabled (`true` by default).
* **How it works**:
  * **Automatic Coupling**: The integration automatically monitors `fan.simply_magic_areas_fan_<area>` and physical fan entities registered in the area.
  * **When Fan is ON**: If an mmWave presence sensor (LD2410, LD2450, Aqara FP1/FP2, Tuya/ESPHome radar) reports occupancy, Simply Magic Areas inspects distance gates ($1.8\text{m} - 3.5\text{m}$) and Doppler frequency variance.
  * **Multi-Signal Evaluation**: If the radar sensor exposes secondary signals (macro-motion energy $\ge 20$, multi-target coordinates outside the fan distance, unmasked range gates 0, 1, 5–8, or companion motion sensors), Simply Magic Areas evaluates them to confirm whether a human is actually present.
  * If a human is present with the fan running, the room remains occupied (`radar_fan_override_sensors`).
  * If only fan blades are moving, the false trigger is suppressed (`filtered_fan_sensors`).
* **Entity Attributes**: Inspect `sensor.simply_magic_areas_state_<area>`:
  * `filtered_fan_sensors`: List of sensors currently silenced due to mechanical fan interference.
  * `radar_fan_override_sensors`: Radar sensors detecting the fan but keeping the area occupied because secondary signals confirm a human is in the room.

### 3. Adaptive Shower & Moisture Evacuation Modeling
* **Where to find it**: Select your area > **Configure** > **Humidity Controls**.
* **Toggle**: Ensure **Learn Shower Humidity** is enabled (`true` by default).
* **How it works**:
  * **Pre-Shower Baseline**: The model autonomously tracks the room's resting baseline humidity ($H_{base}$) during idle periods.
  * **Dual-Stage Trigger**: Activates ventilation quickly upon detecting a rapid rate-of-rise ($dRH/dt$) or sustained moisture differential.
  * **Trajectory Evacuation Shutoff**: When the shower stops and humidity starts falling ($dRH/dt \le 0$), the model samples the exponential drying slope:
    $$\ln(H(t) - H_{base}) = \ln(\Delta H_{peak}) - kt$$
    It calculates air evacuation rate $k$ and projects the dry timestamp ($t_{dry}$).
  * **Pre-Shower Baseline Shutoff**: Automatically turns off the fan as soon as the room returns to its own pre-shower baseline (preventing fans from running indefinitely due to inter-room sensor calibration differences).
* **Entity Attributes**: Inspect `fan.simply_magic_areas_fan_<area>`:
  * `shower_probability`: Multi-sensor shower classification probability ($0.0 - 1.0$).
  * `drying_rate_k`: Fitted exponential evacuation rate ($1/\text{s}$).
  * `estimated_dry_ts`: Unix timestamp when room moisture will reach target baseline.
  * `historical_humidity_baseline`: Learned resting ambient humidity for the room.

---

## 📦 Installation via HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pinkfish&repository=hass-simply-magic-areas&category=Integration)

### Step 1: Add Custom Repository to HACS
1. Open **HACS** in Home Assistant.
2. Click the three dots menu (⋮) in the top-right corner and select **Custom repositories** (or click **+ Add Repository**).
3. Enter the repository details:
   * **Repository**: `https://github.com/pinkfish/hass-simply-magic-areas`
   * **Type**: `Integration`
4. Click **Add**.

### Step 2: Download & Restart
1. Find and open **Simply Magic Areas** in HACS.
2. Click **Download** at the bottom right.
3. Restart Home Assistant (**Settings** > **System** > **Restart**).

### Step 3: Add Integration
1. Go to **Settings** > **Devices & Services** > **Add Integration**.
2. Search for **Simply Magic Areas**.
3. Choose the area to make magic! Repeat for each area or meta-area (`Global`, `Interior`, `Exterior`).

<details>
<summary><b>Alternative: Manual Installation (Advanced)</b></summary>

1. Download the [latest release](https://github.com/pinkfish/hass-simply-magic-areas/releases) ZIP.
2. Extract and copy the `custom_components/simply_magic_areas` folder into your Home Assistant `<config_dir>/custom_components/` directory.
3. Restart Home Assistant.
4. Go to **Settings** > **Devices & Services** > **Add Integration** and search for **Simply Magic Areas**.
</details>

---

## ⚙️ Configuration

1. In Home Assistant, go to **Settings** > **Devices & Services**.
2. Click **Add Integration** and search for **Simply Magic Areas** (or click Configure on existing areas).
3. Each Home Assistant area will have its own Simply Magic Areas configuration entry.
4. **Meta-Areas** (`Global`, `Interior`, `Exterior`) can be configured to broadcast shared policies (sleep entities, accent entities, timeouts) across all rooms automatically.

---

## 📐 System Specification & Technical Deep-Dives

Complete state machine diagrams, lifecycle invariants, mathematical formulations, and subsystem architectures are documented in the [System Specification (SYSTEM_SPEC.md)](SYSTEM_SPEC.md).

For detailed engineering deep-dives, explore the architecture designs in the [designs/](designs/) directory:
* [Adaptive Shower & Moisture Evacuation Modeling](designs/adaptive_humidity_shower_design.md)
* [Bathroom Humidity Fan Controller](designs/bathroom_humidity_fan_design.md)
* [Hierarchical Meta-Area Configuration Inheritance](designs/meta_area_config_inheritance_design.md)
* [Occupancy Hold & Manual Override System](designs/manual_override_occupancy_hold_design.md)
* [Multi-Sensor Signal Fusion & mmWave Fan Discrimination](designs/ml_fusion_mmwave_and_environmental_design.md)

## 🐛 Troubleshooting & Debug Logging

If you encounter unexpected behavior or want to inspect ML model decisions, enable debug logging in your `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.simply_magic_areas: debug
```

When reporting an issue, please capture the relevant lines from `/config/home-assistant.log` and file an issue on GitHub:
👉 [https://github.com/pinkfish/hass-simply-magic-areas/issues](https://github.com/pinkfish/hass-simply-magic-areas/issues)

---

## 🤝 Contributing

Contributions, feature suggestions, and bug reports are warmly welcome! Please review [CONTRIBUTING.md](CONTRIBUTING.md) for testing guidelines (`pytest`) and development standards.

---

## 🙏 Credits & Acknowledgments

**Simply Magic Areas** was originally forked from [jseidl/hass-magic_areas](https://github.com/jseidl/hass-magic_areas) created by [Gustavo Seidl](https://github.com/jseidl). We are grateful to Gustavo and all original contributors for establishing the foundations and architecture of Magic Areas upon which Simply Magic Areas continues to evolve.

