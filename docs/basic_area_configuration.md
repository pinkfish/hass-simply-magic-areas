# Basic Area Configuration Guide

This guide covers the initial and core configuration options for areas managed by **Simply Magic Areas**.

---

## 📍 Accessing Area Configuration

Every room or zone mapped in Home Assistant's Area Registry is managed through its own Simply Magic Areas configuration entry:

1. In Home Assistant, navigate to **Settings** > **Devices & Services**.
2. Locate the **Simply Magic Areas** integration card.
3. Find your area and click **Configure**.
4. The initial form presents the **Area Options** dialog.

---

## 🏷️ Area Classification & Presentation

| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Area Type** | `type` | `interior` | Classifies the spatial characteristics of the area: `interior` for indoor rooms, `exterior` for outdoor spaces (porches, patios, driveways, yards), or `meta` for synthetic parent areas. |
| **Area Icon** | `icon` | `mdi:texture-box` | Icon displayed across Home Assistant dashboards and diagnostic state sensors. |

### Interior vs. Exterior Behavior
- **Interior (`interior`)**: Standard indoor room. Subject to occupancy-based comfort lighting, climate setbacks, and indoor humidity tracking.
- **Exterior (`exterior`)**: Outdoor space. Automatically enables exterior dusk-to-dawn lighting control, resident arrival illumination, and camera motion tracking. Physical clearance does not turn off exterior lights during active nighttime stay-on windows.

---

## ⏱️ Occupancy & Clearance Timeouts

Timeouts dictate the lifecycle of an area as occupants move through the space:

$$\text{Clear} \xrightarrow{\text{Motion}} \text{Occupied} \xrightarrow{\text{clear\_timeout}} \text{Extended} \xrightarrow{\text{extended\_timeout}} \text{Clear}$$

| Setting | Parameter Key | Default | Description & Recommended Values |
| :--- | :--- | :--- | :--- |
| **Clear Timeout** | `clear_timeout` | `360s` (6 min) | Seconds of sensor inactivity before transitioning from `occupied` to `extended`.<br>• **Hallways, Pantries, Closets**: `30`–`60s`<br>• **Bathrooms**: `120`–`180s`<br>• **Living Rooms, Kitchens**: `300`–`450s`<br>• **Offices / Workspaces**: `600s` (or use mmWave radar) |
| **Extended Timeout** | `extended_timeout` | `360s` (6 min) | Seconds the area remains in `extended` before extinguishing lights and turning off.<br>• **Bathrooms**: `180`–`300s` (exhaust fans run during this window)<br>• **General Rooms**: `60`–`120s` |

> [!TIP]
> **Preemption Invariant:** If motion or presence is detected at any point while in `extended`, the area transitions immediately back to `occupied`, resetting all timers synchronously.

---

## 🎛️ Master Actuator Controls

Simply Magic Areas gives you complete control over whether lighting and ventilation are automated:

| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Light Control** | `light_control` | `true` | Master switch for automated lighting. When disabled (`false`), Simply Magic Areas continues tracking occupancy and sensor states but will not send commands to light entities. |
| **Fan Control** | `fan_control` | `true` | Master switch for automated ventilation. When disabled (`false`), fans are never controlled automatically by occupancy or humidity. |
| **Suppress Fan in Sleep** | `fan_suppress_in_sleep` | `false` | When enabled (`true`), post-occupancy exhaust ventilation is muted during sleep mode to prevent disturbing sleeping household members. Steam/shower extraction still activates if high moisture is detected. |

---

## 💡 Lighting Profiles & Dimming Levels (Base Setup)

Simply Magic Areas maintains separate lighting states for different household contexts:

| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Occupied Dim Level** | `occupied_state_dim` | `100%` | Target brightness percentage when the area is occupied under normal conditions. |
| **Clear Dim Level** | `clear_state_dim` | `0%` | Target brightness after the area clears. Set to `10%`–`20%` to maintain gentle nightlight illumination in hallways or staircases instead of pitch blackness. |
| **Extended Dim Level** | `extended_state_dim` | `0%` | Dim level during the transitional `extended` period before full clearance. |
| **Sleep Light Entity** | `sleep_lights_entity` | *None* | A binary sensor, input boolean, or switch indicating sleep mode for this room (e.g., `input_boolean.bedtime_active` or a bed sensor). |
| **Sleep Dim Level** | `sleep_lights_state_dim` | `10%` | Dimming level applied when motion occurs while sleep mode is active. |
| **Bright Light Entity** | `bright_lights_entity` | *None* | Entity indicating daytime or high ambient lux (e.g., illuminance threshold sensor). |
| **Bright Dim Level** | `bright_lights_state_dim` | `100%` | Dimming level applied during daytime/bright states. |
| **Accented Light Entity**| `accented_lights_entity`| *None* | Switch or boolean triggering mood/accent lighting scenes. |
| **Accented Dim Level** | `accented_lights_state_dim` | `50%` | Dimming level applied when accented mode is enabled. |

---

## ☀️ Photometric Baselines

| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Dark Lux Floor** | `dark_lux_floor` | `10 lx` | Ambient illuminance at or below which the room is considered dark regardless of daytime solar elevation. Guarantees lights turn on in windowless closets, pantries, and interior bathrooms. |
| **Learn Daylight Lux** | `learn_daylight_lux` | `true` | When enabled, Simply Magic Areas continuously observes ambient sensor history to calibrate your room's natural daytime lux baseline without manual guesswork. |
| **Daylight Lux Level** | `daylight_lux_level` | `0` (auto) | Manual override for the daytime lux threshold. When set to `0`, the autonomous learning algorithm is used. |
| **Min Brightness Level**| `min_brightness_level` | `100` | Illuminance level at which lights begin dimming as ambient light rises. |
| **Max Brightness Level**| `max_brightness_level` | `200` | Illuminance level at which ambient daylight is sufficient and lights turn off entirely. |

---

## 📡 Room Tracking (MQTT)

| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **MQTT Room Presence** | `mqqt_room` | `false` | Enables integration with BLE room-presence tracking engines (such as ESPresense or Room-Assistant). When enabled, BLE tracker entities assigned to this area participate in presence evaluation. |

---

## 🏛️ Meta-Area Hierarchical Inheritance

To avoid re-entering identical parameters across dozens of rooms, Simply Magic Areas provides three synthetic Meta-Areas:
1. **Global (`Global`)**: Parent of all areas. Broadest whole-home defaults.
2. **Interior (`Interior`)**: Parent of all interior rooms.
3. **Exterior (`Exterior`)**: Parent of all outdoor zones.

### Inherited Keys
When left unconfigured on an individual area, the following settings automatically inherit values from the parent Meta-Area:
- `clear_timeout`
- `extended_timeout`
- `sleep_lights_entity`
- `sleep_lights_state_dim`
- `accented_lights_entity`
- `accented_lights_state_dim`
- `dark_lux_floor`
- `fan_suppress_in_sleep`

> [!NOTE]
> Defining a parameter on a concrete area explicitly overrides any inherited value from its parent Meta-Area.
