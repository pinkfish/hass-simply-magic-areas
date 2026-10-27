# Advanced Features, Aggregates & System Control Guide

This guide covers advanced integration features: Climate Groups, the Area-Aware Media Player (AAMP), Group Creation & Sensor Aggregates, Health & Distress Monitoring, and Master System Control.

---

## 🌡️ Climate Groups (HVAC Coordination & Energy Setbacks)

The Climate Groups feature unifies multiple heating and cooling units located in the same area into a single coordinated climate entity (`climate.simply_magic_areas_climate_<area>`).

### How to Enable
1. Go to **Settings** > **Devices & Services** > **Simply Magic Areas** > Area > **Configure**.
2. Under **Select Features**, check **Climate Groups** (`climate_groups`).

### Configuration Options
| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Turn On State** | `turn_on_state` | `extended` | The area state at which climate systems turn on or resume comfort temperatures (`occupied` or `extended`). |

### Key Capabilities
- **Averaged Ambient Temperature**: Automatically computes the mean temperature across all thermometers and climate devices in the room.
- **Synchronized Controls**: Changes to target temperature, HVAC mode (`heat`, `cool`, `auto`, `off`), fan mode, or swing mode are broadcast synchronously across all child units.
- **Autonomous Occupancy Setbacks**:
  - When the room becomes `occupied`, climate systems adjust to comfort setpoints.
  - When the room clears (`clear` state) or transitions to `sleep`, climate systems drop into eco/setback mode or turn off, maximizing household energy efficiency.

---

## 📢 Area-Aware Media Player (AAMP & Targeted TTS)

The Area-Aware Media Player is a dynamic audio routing engine hosted by the **Global Meta-Area** (`media_player.simply_magic_areas_area_aware_media_player_global`).

### How to Enable
1. In Simply Magic Areas, select the **Global** Meta-Area > **Configure**.
2. Under **Select Features**, check **Area-Aware Media Player** (`area_aware_media_player`).

### Configuration Options
| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Notification Devices** | `notification_devices` | `[]` | List of media player entities (Sonos, smart speakers, smart TVs) across the home available for announcements. |
| **Notification States** | `notification_states` | `["extended"]` | Area states that permit playback. Available options: `occupied`, `extended`, `sleep`. |

### Dynamic Audio Routing Logic
- When a `play_media` or TTS service call is directed to `media_player.simply_magic_areas_area_aware_media_player_global`, Simply Magic Areas checks which area of the house is currently occupied.
- Audio is routed exclusively to the speakers located in the room where occupants are physically present.
- If multiple rooms are occupied, announcements play simultaneously in all occupied spaces.
- If no room is actively occupied, audio plays across all configured fallback devices.
- Prevents jarring announcements in empty rooms or waking up sleeping household members.

---

## 📊 Group Creation & Sensor Aggregates

The Group Creation feature bundles loose devices and creates unified statistical sensor aggregates for each area.

### How to Enable
1. Go to your area's **Configure** dialog > **Select Features**.
2. Check **Group Creation** (`group_creation`).

### Configuration Options
| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Minimum Entities** | `aggregates_min_entities` | `2` | Minimum number of entities of a particular class required before creating an aggregate group, preventing redundant entities in single-sensor rooms. |
| **Sensor Aggregation** | `aggregates` | `true` | Creates aggregated sensors for binary and numeric telemetry. |
| **Cover Groups** | `cover_groups` | `false` | Bundles motorized blinds, shades, curtains, and garage doors into a master `cover.simply_magic_areas_cover_<class>_<area>` entity. |
| **Media Player Groups** | `media_player_groups` | `false` | Bundles smart speakers and media streamers into a master `media_player.simply_magic_areas_media_player_<area>` entity. |

### Aggregate Entity Types
1. **Binary Sensor Aggregates**:
   - `motion`, `presence`, `occupancy`, `door`, `window`: Uses `any` reduction mode (turns ON if ANY child sensor trips).
   - `connectivity`, `plug`: Uses `all` reduction mode (reports ON only when ALL devices report ON).
2. **Numeric Sensor Aggregates**:
   - Temperature, Illuminance, Humidity: Uses `mean` reduction (mathematical average across sensors).
   - Power, Current: Uses `sum` reduction (total electrical load of the room).
   - Unitless Sensors: Safely aggregates unitless sensor device classes (e.g. `SensorDeviceClass.AQI`, `SensorDeviceClass.PH`) without entity rejection.
3. **Long-Term Statistics (LTS) for Energy Dashboard**:
   - Energy sensors are assigned `SensorStateClass.TOTAL`.
   - Cumulative utility meters (`gas`, `water`) are assigned `SensorStateClass.TOTAL_INCREASING`.
   - Fully compatible with native Home Assistant Energy Dashboard long-term graphs.

---

## 🚨 Health & Distress Monitoring

| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Health Monitoring** | `health` | *Available in Features* | Creates a consolidated life-safety and environmental health monitor: `binary_sensor.simply_magic_areas_problem_<area>`. |

### Monitored Classes
The health sensor continuously aggregates:
- `smoke`: Smoke detector alerts
- `gas`: Natural gas / CO detector trips
- `moisture`: Water leak and flood sensors
- `safety`: Security/safety alarm trips
- `problem`: Hardware malfunction or battery alerts

If any distress sensor trips in the area, the area problem sensor immediately transitions to `on`, allowing single-entity alert notifications in your automations.

---

## 🛑 System Control & Automation Master Switch

Simply Magic Areas automatically provides a master automation switch for every area:

**Entity ID**: `switch.simply_magic_areas_system_control_<area>`

### Functionality
- **Switch ON (Normal Operation)**: Simply Magic Areas actively tracks occupancy, evaluates daylight baselines, manages ventilation, and controls lighting.
- **Switch OFF (Manual Override Mode)**:
  - Immediately transitions the area state to `manual`.
  - Freezes automated control across all lighting, fans, and climate platforms.
  - Leaves physical control entirely in user hands without automation interference.
  - Perfect for parties, house cleaning, room painting, or maintenance.
- **Restoring Automation**: Flipping the switch back to `on` instantly resumes automated state evaluation.
