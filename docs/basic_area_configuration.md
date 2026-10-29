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
| **Area Type** | `type` | `interior` | Classifies the spatial characteristics of the area: `interior` for indoor rooms, or `exterior` for outdoor spaces (porches, patios, driveways, yards). Synthetic parent areas use `meta`. |

> [!NOTE]
> **Area Icons:** Home Assistant natively manages area icons in **Settings** > **Areas & Zones** > **Areas**. Simply Magic Areas automatically pulls and respects your configured area icon from the Area Registry without requiring redundant manual entry.

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
| **Extended Timeout** | `extended_timeout` | `360s` (6 min) | Seconds the area remains in `extended` before extinguishing lights and turning off.<br>• **Toilets / Powder Rooms**: `180`–`300s` (exhaust fans run during this window to clear odors)<br>• **Bathrooms**: `180`–`300s` (exhaust fans only run when humidity changes/shower, unless configured otherwise)<br>• **General Rooms**: `60`–`120s` |

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
| **Run Fan in Extended** | `fan_run_in_extended` | `auto` | When `auto` (default): toilets/powder rooms automatically run in `extended`; bathrooms with shower humidity tracking only activate on humidity changes. Can be explicitly overridden (`true`/`false`). |

---

## 🌙 Sleep Mode & Night Lighting

| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Sleep Light Entity** | `sleep_lights_entity` | *None* | A binary sensor, input boolean, or switch indicating sleep mode for this room (e.g., `input_boolean.bedtime_active` or a bed sensor). When active, occupancy turns on gentle sleep illumination instead of full brightness. |

---

## ⚙️ Advanced Settings Relocation

To provide an intuitive, clutter-free setup experience, specialized options are housed under the **Advanced Light Groups** feature flow (`advanced_light_groups`):

| Setting | Parameter Key | Feature Location | Description |
| :--- | :--- | :--- | :--- |
| **Bright Light Entity** | `bright_lights_entity` | Advanced Light Groups | Entity indicating daytime or high ambient lux (e.g., illuminance threshold sensor). |
| **Bright Dim Level** | `bright_state_dim` | Advanced Light Groups | Target dim percentage applied during bright/daylight state. |
| **Occupied Dim Level** | `occupied_state_dim` | Advanced Light Groups | Target dim percentage applied when room is occupied under standard conditions (default `100%`). |
| **MQTT Room Presence** | `mqqt_room` | Advanced Light Groups | Enables BLE tracking engines (ESPresense, Room-Assistant) for room-level proximity. |
| **Photometric Baselines**| `dark_lux_floor`, `learn_daylight_lux` | Advanced Light Groups | Lux floor and autonomous daylight calibration. |

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
