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
| **Device Classes** | `presence_sensor_device_class`| `["motion", "occupancy", "presence"]` | Specific binary sensor classes evaluated for presence. You can add `door`, `window`, or `vibration` to trigger occupancy on door openings. |
| **Active On States** | `on_states` | `["on", "home", "playing", "open"]` | States evaluated as active presence. Adding `playing` ensures smart TVs or media speakers hold the room occupied while watching movies. |

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
