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

## ⚙️ Configuration Guide

Each Home Assistant area is configured through its configuration entry in **Settings** > **Devices & Services** > **Simply Magic Areas** > **Configure**.

---

### 🌙 How to Turn Off Fans at Night (Bathroom Sleep Suppression)

Bathrooms often share walls with bedrooms, and noisy exhaust fans running during midnight bathroom visits can disturb sleep. Simply Magic Areas includes built-in **Acoustic Sleep Fan Suppression**:

1. **Enable Fan Sleep Suppression**:
   * Go to your bathroom area > **Configure** > **Area Options**.
   * Ensure **Suppress exhaust fan in sleep mode** is checked (`fan_suppress_in_sleep: true`).

2. **Define When It Is Night / Sleep Time**:
   You can provide sleep state through any of the following methods:
   * **Direct Sleep Entity**: In **Area Options**, set **Sleep Entity** to your sleep toggle (e.g., `input_boolean.sleep_mode`, a bed occupancy sensor, or sleep schedule switch).
   * **Link to Master Bedroom (Ensuite Linking)**: In **Advanced Light & Illuminance Options**, set **Reference Bright Area** to your bedroom (e.g., `master_bedroom`). When the bedroom enters its `sleep` profile, the bathroom automatically suppresses the exhaust fan during nocturnal visits.
   * **Inherit from Meta-Area**: Configure a sleep entity once on the **Interior** or **Global** Meta-Area, and all rooms will inherit the policy automatically.

3. **Behavior & Safety Override**:
   * **Routine Nocturnal Visits**: When sleep mode is active and someone enters the bathroom, lights follow the sleep profile (e.g. dimmed nightlight) and the exhaust fan **remains OFF** during the `extended` state.
   * **Moisture Safety Guarantee**: If someone takes a midnight shower or bath causing high differential moisture, the fan will still activate to protect against mold and drywall damage, and then shut off cleanly once moisture is evacuated.

---

### ⏱️ Setting Up Timing Information & Timeouts

Timeouts determine how quickly rooms react to motion, how long lights/fans linger after you leave, and safety limits for continuous fan operation.

#### 1. Presence & Occupancy Timeouts (**Area Options**)
| Setting | Key | Default | Description & Recommended Settings |
| :--- | :--- | :--- | :--- |
| **Clear Timeout** | `clear_timeout` | `360s` (6m) | Number of seconds with no motion before transitioning from `occupied` to `extended`. Recommended: **30–60s** for hallways/pantries, **120–180s** for bathrooms, **300–600s** for offices/living rooms. |
| **Extended Timeout** | `extended_timeout` | `360s` (6m) | Number of seconds the room stays in `extended` before turning off entirely. Exhaust fans typically run during this window. Recommended: **180–300s** for bathrooms. |
| **Update Interval** | `update_interval` | `1800s` (30m) | Periodic health-check interval to evaluate missed entity states or stuck sensors. |

#### 2. Ventilation & Humidity Timers (**Advanced Ventilation & Humidity**)
| Setting | Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Maximum Fan Time** | `humidity_max_fan_time` | `3600s` (60m) | Hard safety limit. If a humidity sensor malfunctions or sticks, the fan forcefully shuts off after this duration (`0` to disable). |
| **Flatline Wait Time** | `humidity_wait_s` | `1200s` (20m) | Duration humidity rate-of-change must remain within the deadband near zero before concluding that moisture has stabilized and turning off. |
| **Presence Gate Time** | `humidity_presence_gate_time` | `0s` (disabled) | When set (e.g., `300s`), humidity rises only trigger exhaust fans if the room was occupied within this time window, preventing false triggers from outdoor weather fronts. |

#### 3. Exterior Lighting Timers (**Advanced Light Groups**)
| Setting | Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Exterior Stay-On Duration** | `exterior_lighting_timeout` | `7200s` (2h) | How long exterior floodlights, porch, and garden lights remain active after sunset, resident arrival, or motion events before safely turning off. |

---

### 🏛️ Hierarchical Policy Inheritance (Meta-Areas)

Instead of setting the same sleep switches and timeouts for every room individually:
* Create **Meta-Areas**:
  * **Global**: Broadcasts whole-home policies across all areas.
  * **Interior**: Groups indoor rooms together.
  * **Exterior**: Manages exterior lighting, gates, and motion perimeters.
* Settings defined on a Meta-Area (such as `sleep_entity`, `clear_timeout`, or accent lighting scenes) automatically cascade down to all subordinate rooms unless explicitly overridden on a specific area.

---

### 🛠️ Advanced Feature Configuration Flows

When you click **Configure** on any area, Simply Magic Areas allows you to select features to activate. Each enabled feature provides dedicated configuration flow steps for advanced and complex setups:

#### 1. Advanced Light Groups & Custom Lighting Profiles
* **Entity Whitelist / Blacklist**:
  * **Include Entities** (`include_entities`): Pull sensors or switches located in adjacent areas or unassigned zones into this area's presence logic.
  * **Exclude Entities** (`exclude_entities`): Exclude noisy or misbehaving sensors from triggering presence without having to delete them from Home Assistant.
* **Presence Sensor Customization**:
  * **Presence Platforms** (`presence_device_platforms`): Specify which integration platforms contribute to presence (e.g. `binary_sensor`, `media_player`, `device_tracker`).
  * **Device Classes** (`presence_sensor_device_class`): Filter presence by device class (e.g. `motion`, `occupancy`, `presence`, `door`, `window`).
  * **Active Sensor States** (`on_states`): Customize which states trigger presence (defaults to `on`, `home`, `playing`, `open`).
* **Per-Profile Lighting & Dimming**:
  * Assign dedicated lights for each lighting profile: `occupied_lights`, `clear_lights`, `extended_lights`, `sleep_lights`, `bright_lights`, `accented_lights`.
  * Set granular dim levels:
    * `clear_state_dim`: Subtle nightlight or dimmed glow after clearing (e.g. `10%`, or `0` for complete shutoff).
    * `extended_state_dim`: Soft transitional dim level before turning off.
    * `accented_state_dim`: Accent mood lighting when the area's accent switch/entity is engaged.
* **Photometrics & Dark Floor Guarantee**:
  * **Dark Lux Floor** (`dark_lux_floor`): Guaranteed baseline (default: `10 lx`). Even during bright sunny days, windowless closets, pantries, and interior bathrooms always treat ambient lux at or below this floor as dark, ensuring lights always turn on automatically.
  * **Daylight Calibration** (`learn_daylight_lux`): Autonomously computes your room's natural daytime illuminance baseline from sensor history.

#### 2. Climate Groups (HVAC Energy Saving)
* **Turn On State** (`turn_on_state`): Choose whether climate devices (thermostats, ACs, mini-splits, heat pumps) activate when the area enters **Occupied** or **Extended**.
* **Energy Automation**: When all residents leave the room and it transitions to `clear`, climate groups safely power down or drop to setback mode, turning back on immediately upon re-entry.

#### 3. Area-Aware Media Player & Targeted Announcements
* **Notification Devices** (`notification_devices`): Select the smart speakers, Sonos, or media receivers situated in this area.
* **Notification States** (`notification_states`): Select which area states allow broadcast playback (e.g. `occupied`, `extended`).
* **Smart Routing**: Direct TTS alerts, doorbell chimes, and reminders exclusively to occupied rooms—preventing announcements from echoing across empty rooms or disturbing sleeping household members.

#### 4. Aggregate Sensor Groups, Covers & Media
* **Automatic Sensor Aggregates** (`aggregates`): Automatically creates unified aggregate sensors for the area:
  * Binary aggregates (`all`, `any`): e.g. `binary_sensor.simply_magic_areas_motion_<area>` (turns on if ANY motion sensor detects movement).
  * Numeric aggregates (`mean`, `sum`, `min`, `max`): e.g. `sensor.simply_magic_areas_temperature_<area>` (area average temperature).
* **Minimum Entities Threshold** (`aggregates_min_entities`, default: `2`): Prevents redundant aggregate sensors if an area only has a single entity.
* **Cover Groups** (`cover_groups`): Bundles motorized blinds, shades, and curtains into a single master control entity per area.
* **Media Player Groups** (`media_player_groups`): Groups all speakers in the area into a single unified media player entity.

#### 5. Presence Hold Switch & Party/Cleaning Overrides
* **Presence Hold Switch** (`presence_hold`): Creates `switch.simply_magic_areas_presence_hold_<area>`. Turning this switch on locks the area in `occupied` regardless of sensor timeouts.
* **Presence Hold Timeout** (`presence_hold_timeout`): Auto-expiration timer in seconds (set to `0` to keep holding indefinitely until manually toggled off). Perfect for movie nights, guest visits, or reading without moving.
* **System Control Switch**: Creates `switch.simply_magic_areas_system_control_<area>`. Flipping this switch turns off automated control for the room completely, reverting all lights and fans to manual control for cleaning, painting, or parties.

#### 6. Health & Safety Aggregates
* **Health Monitoring** (`health`): Automatically groups problem sensors, smoke alarms, gas detectors, and moisture/leak sensors into unified safety aggregates:
  * `binary_sensor.simply_magic_areas_health_problem_<area>`
  * `binary_sensor.simply_magic_areas_health_gas_<area>`
  * `binary_sensor.simply_magic_areas_health_smoke_<area>`
  * `binary_sensor.simply_magic_areas_health_leak_<area>`

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

