# Lighting, Presence & Exterior Automation Guide

This guide details the advanced lighting, presence sensing, machine learning, and exterior automation capabilities in **Simply Magic Areas**.

---

## 📍 Accessing Advanced Lighting Settings

1. In Home Assistant, navigate to **Settings** > **Devices & Services** > **Simply Magic Areas**.
2. Click **Configure** on your area.
3. In the feature selection menu, enable **Advanced Light Groups**.
4. The options flow will display the settings detailed below.

---

## 🔍 Presence Detection & Entity Filtering

By default, Simply Magic Areas automatically aggregates all motion and presence sensors assigned to an area. The Advanced Light Groups feature allows you to customize and fine-tune this behavior:

| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Include Entities** | `include_entities` | `[]` | Explicit list of entity IDs to include in this area's presence logic, even if they reside in other areas or lack area assignments. Ideal for door contacts or cross-boundary hallway sensors. |
| **Exclude Entities** | `exclude_entities` | `[]` | Explicit list of entity IDs to ignore. Useful for suppressing noisy, flickering, or battery-dying sensors without removing them from Home Assistant. |
| **Update Interval** | `update_interval` | `1800s` (30m) | Heartbeat interval for re-evaluating area state and catching any missed entity events. |
| **Presence Platforms** | `presence_device_platforms` | `["binary_sensor", "media_player"]` | Integration domains that trigger occupancy. Options include `binary_sensor`, `media_player`, `remote`, and `camera`. |
| **Device Classes** | `presence_sensor_device_class`| `["motion", "occupancy", "presence"]` | Specific continuous binary sensor classes evaluated for presence. Door/window contact sensors (`door`, `garage_door`, `opening`, `window`) are automatically ingested as **edge triggers only** (triggering an occupancy pulse on `on -> off` or `off -> on` transitions without their static open/closed state holding the room occupied). |
| **Active On States** | `on_states` | `["on", "home", "playing", "open"]` | States evaluated as active presence for continuous sensors. Adding `playing` ensures smart TVs or media speakers hold the room occupied while watching movies. |

### 🛡️ Z-Wave Sliding Backoff Retry & Broken Device Resilience
- **Sliding Backoff Retries (`3.5s`, `7s`, `15s`)**: When turning area lights or fans ON or OFF, Simply Magic Areas verifies that available underlying physical entities reach the target state and automatically retries with a sliding backoff (`3.5s`, `7.0s`, `15.0s`) if a Z-Wave or wireless command is dropped.
- **Delayed Z-Wave Report Immunity**: Delayed hardware status and dimmer reports matching an in-flight automation command are recognized within a grace window so slow Z-Wave dimmers never falsely trigger `manual_control`.
- **Single-Device Fault Isolation**: If one light, fan, or sensor in an area is broken (`unavailable` or `unknown`), the remaining working devices and sensors continue operating normally without getting stuck or causing infinite retries.

### 🛏️ Rooms Without Presence Sensors & Smart Switch Night Dim Pre-Staging
- **Manual Switch ON/OFF with Automatic Dim-Level Control (All Rooms)**: In both sensor-equipped and sensorless rooms, turning a switch ON or OFF manually sets `manual_control: true` on the ON/OFF state (held until the room clears in sensor rooms, or permanently manual in sensorless rooms). While the light is ON, automation only controls the **dim level** across `sleep` (night), `bright` (daytime), and `standard` lighting profiles without ever turning an ON light OFF or an OFF light ON. Turning a switch ON during `sleep` mode immediately adjusts its dim level to the night dim level while keeping `manual_control: true`. Manually adjusting the dimmer brightness while the light is ON locks the custom brightness across profile transitions until the light is turned OFF or the room clears.
- **Smart Switch Default Turn-On Dim Level (All Rooms)**: In all rooms (both with and without presence sensors), Simply Magic Areas detects `number.*` configuration entities on smart dimmers/switches that control the hardware default turn-on brightness (such as Z-Wave JS Inovelli/Zooz/GE `default_level_local`, `custom_brightness`, `default_brightness`, and Zigbee/Matter `on_level`). When entering `sleep` mode, the switch's default turn-on level is pre-staged to the night dim level (scaled to `1..99` for Z-Wave or `1..100` for percentage entities) and restored to the standard `occupied` dim level when leaving `sleep` mode.

---

## 🧠 Local Bayesian Lighting Preference Learner

Simply Magic Areas features a built-in, 100% local machine learning engine that eliminates static lux-to-brightness heuristic curves:

| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Learn Lighting Preferences** | `learn_lighting_preferences` | `true` | Enables the online Bayesian preference learner. The integration continuously monitors manual brightness slider adjustments to learn the household's exact lighting preferences across varying natural daylight conditions. |

### Architectural Highlights
- **100% Local & Lightweight**: Uses pure Python Recursive Least Squares (RLS) matrix math ($\mathcal{O}(d^2)$ execution $< 1\mu\text{s}$). Requires zero cloud connection, zero remote APIs, and zero external packages.
- **Dynamic Blending**: As you make adjustments, the algorithm blends prior baseline heuristics with learned weights. After 5 samples, personalized preferences take full precedence.
- **Non-Volatile Persistence**: Learned weights and covariance matrices are saved locally in `.storage/simply_magic_areas_lighting_ml_<area>` and survive Home Assistant restarts.

### Diagnostic Attributes
Exposed on `light.simply_magic_areas_light_<area>`:
- `ml_adaptive_lighting_active`: Boolean confirming learner status.
- `ml_preference_samples`: Number of manual user overrides recorded.
- `ml_predicted_brightness`: The exact brightness predicted by the regression model for the current ambient lux.

---

## 📡 mmWave Radar vs. Fan False-Presence Filter

Ceiling fans, oscillating floor fans, and exhaust ventilation blades reflect radio-frequency signals that cause mmWave micro-radar sensors (e.g. Aqara FP2, Everything Presence One, Tuya mmWave) to falsely detect human presence, keeping lights on indefinitely in empty rooms.

Simply Magic Areas includes an autonomous **Actuator-Coupled mmWave Fan Discriminator**:

| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **mmWave Fan Filter** | `mmwave_fan_filter` | `true` | Actuator-coupled sensor filter. When area exhaust or ceiling fans are running, mmWave radar signals are dynamically evaluated to prevent spinning blades from falsely holding the room occupied. |

### How It Works
1. **Actuator State Coupling**: When fans are OFF, radar operates at full native sensitivity.
2. **Spatial & Variance Masking**: When fans are ON, the discriminator checks if radar reflections originate in the fan zone ($1.8\text{m} \le \text{distance} \le 3.5\text{m}$) with low-variance characteristic blade rotations ($\sigma^2 < 0.02$).
3. **Secondary Signal Evaluation**: If the mmWave sensor exposes multi-target tracking, macro-motion energy, or range gate telemetry:
   - Targets outside the fan zone or macro-motion energy $\ge 20$ confirm actual human presence $\to$ room remains `occupied`.
   - Reflections confined to the fan zone without human movement are silenced $\to$ room safely clears.
4. **Universal Binary Dwell Fallback**: For basic binary radar sensors without distance attributes, persistent occupancy is filtered out when all companion motion sensors (PIR) have been clear for $\ge 30\text{s}$ while a fan is active.

### Diagnostic Attributes
Exposed on `sensor.simply_magic_areas_state_<area>`:
- `filtered_fan_sensors`: List of sensors currently silenced due to fan interference.
- `radar_fan_override_sensors`: List of radar sensors confirmed detecting human occupants despite running fans.

---

## 🌙 Exterior Nighttime Lighting Automation

For areas classified as `type: exterior` (Driveways, Patios, Porches, Gardens, and the Exterior Meta-Area), Simply Magic Areas implements an autonomous outdoor lighting automation cycle:

| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Exterior Lighting Timeout** | `exterior_lighting_timeout` | `7200s` (2 hours) | Minimum duration exterior lights stay illuminated after a trigger event before turning off. |
| **Sunset Dusk Trigger** | `exterior_sunset_trigger` | `true` | Automatically turns exterior lights ON when the sun dips below the horizon (`sun.sun` transitions to `below_horizon` or elevation $\le 0^\circ$). |
| **Resident Arrival Trigger** | `exterior_arrival_trigger` | `true` | Automatically illuminates exterior lights when any household member returns home (`person.*` transitions to `home`) during nighttime. |

### Exterior Lifecycle Invariants
- **Motion & Camera Reactivity**: Movement detected by outdoor PIR sensors, gates, or vision cameras (`camera.*`) restarts the 2-hour stay-on clock from the moment motion clears.
- **Sunrise Clearance Guarantee**: At sunrise (`sun.sun` transitions to `above_horizon`), any running exterior timer is cancelled and lights are unconditionally extinguished (`force=True`).
- **Decoupled Clearance Invariant**: Physical area clearance does not extinguish exterior lights while the 2-hour stay-on timer is running, ensuring outdoor spaces remain safely illuminated without misrepresenting empty yards as occupied on Home Assistant dashboards.
- **Restart Rehydration**: Exterior timers persist across Home Assistant restarts; remaining countdowns resume seamlessly.
- **Interior Front-Room / Entry Arrival Pre-Warming ("Welcome Home")**: When a resident arrives home (`person.*` transitions to `home`), approaches home (`proximity.*` with `dir_of_travel: towards` within `1500m` while the house interior is `clear`), or triggers an exterior arrival/motion sensor while the house interior is vacant at night, interior entry rooms (`front_room`, `foyer`, `entry`, `mudroom`, or rooms with a front/entry/garage door sensor) automatically turn on their lights for a 10-minute welcome window (`arrival_prewarm_timeout: 600s`) and hand off seamlessly once the resident steps inside.
- **Gentle 50% Warning Dim in Extended Mode (`extended_warning_dim: 50`)**: When an occupied room enters `extended` mode, active lights gently dim to 50% of their active profile brightness as a visual warning before turning completely OFF at `clear`.
- **All-Away Departure Sweep (`all_away_sweep: true`)**: When all tracked `person.*` entities leave home, any forgotten lights or lingering `manual_control` overrides in unoccupied or sensorless interior rooms are automatically turned OFF.
- **Sustained Activity Wake-Up in Living Areas (`sleep_wakeup_delay: 300s`)**: If a living area (`kitchen`, `office`, `living_room`, `study`, `dining_room`, `family_room`) remains continuously occupied for 5 minutes while `sleep` mode is active, its lights automatically promote from `sleep` dim (`30%`) to `standard` (`100%`) without disturbing bedrooms.
- **Multi-Light Room Manual Synchronization**: In rooms with multiple lights, manually turning any individual switch ON, OFF, or dimming it automatically synchronizes all other lights in that single room to the exact same ON/OFF state and dim level, verified by sliding backoff retries.
- **Fast Manual-Off Unoccupied Reset**: When a light is manually turned OFF, the space's unoccupied `clear_timeout` is shortened to at most `10s` once presence sensors clear, `manual_control` clears immediately as soon as the space is marked unoccupied (`extended` or `clear`), and the lights automatically turn back ON the moment the space switches back to `occupied`.

---

## 💡 Granular Per-Light Entity Assignment

For complex rooms with multiple fixtures, you can assign individual light entities to specific lighting profiles:

| Profile | Light Entity Selector | Dim Percentage | Description |
| :--- | :--- | :--- | :--- |
| **Occupied** | `occupied_lights` | `occupied_state_dim` | Main ambient fixtures when occupants are present. |
| **Clear** | `clear_lights` | `clear_state_dim` | Nightlight or orientation glow after clearance. |
| **Extended** | `extended_lights` | `extended_state_dim` | Transitional fixtures before turning off. |
| **Sleep** | `sleep_lights` | `sleep_lights_state_dim` | Gentle floor lights or dim baseboard illumination during sleep hours. |
| **Bright** | `bright_lights` | `bright_lights_state_dim` | Daylight-boosted task lighting. |
| **Accented** | `accented_lights` | `accented_lights_state_dim` | Decorative LED strips, artwork spots, or cove lights. |
