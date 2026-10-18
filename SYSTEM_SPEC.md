# Simply Magic Areas — Technical System Specification

## 1. Executive Summary

**Simply Magic Areas** is a Home Assistant custom integration that transforms static physical areas into dynamic, reactive, stateful environments. By grouping devices, synthesizing occupancy presence from disparate sensors, tracking ambient environmental trends, and coordinating automations across platforms (lighting, climate, ventilation, covers, media players, and distress monitoring), it turns areas into autonomous smart rooms.

---

## 2. System Architecture & Topology

### 2.1 Area Hierarchy & Meta-Areas

Simply Magic Areas categorizes spaces into two primary constructs:

1. **Concrete Areas**: Directly mapped to Home Assistant Area Registry entries.
   - **Interior (`interior`)**: Standard indoor rooms (living room, bedrooms, bathrooms).
   - **Exterior (`exterior`)**: Outdoor spaces (patio, backyard, driveway).
2. **Meta-Areas**: Synthetic aggregations created at higher spatial levels.
   - **Global (`Global`)**: Aggregates all interior and exterior areas across the entire home. Hosts global capabilities such as the Area-Aware Media Player (AAMP).
   - **Interior Meta-Area (`Interior`)**: Aggregates all interior spaces.
   - **Exterior Meta-Area (`Exterior`)**: Aggregates all exterior spaces.

```mermaid
graph TD
    subgraph Meta-Areas
        Global["Global Meta-Area (global)"]
        InteriorMeta["Interior Meta-Area (interior)"]
        ExteriorMeta["Exterior Meta-Area (exterior)"]
    end

    subgraph Concrete Interior Areas
        Kitchen["Kitchen"]
        LivingRoom["Living Room"]
        Bathroom["Bathroom"]
        Bedroom["Bedroom"]
    end

    subgraph Concrete Exterior Areas
        Patio["Patio"]
        Backyard["Backyard"]
    end

    Global --> InteriorMeta
    Global --> ExteriorMeta
    InteriorMeta --> Kitchen
    InteriorMeta --> LivingRoom
    InteriorMeta --> Bathroom
    InteriorMeta --> Bedroom
    ExteriorMeta --> Patio
    ExteriorMeta --> Backyard
```

### 2.2 Lifecycle & Initialization Workflow

```mermaid
sequenceDiagram
    autonumber
    participant HA as Home Assistant Core
    participant SMA as Simply Magic Areas Init
    participant Concrete as Concrete MagicArea
    participant Platforms as Platform Handlers
    participant Meta as Meta MagicArea

    HA->>SMA: async_setup_entry(config_entry)
    alt Concrete Area
        SMA->>Concrete: MagicArea.initialize()
        Concrete->>Concrete: _load_entities()
        Concrete->>Concrete: _load_state_config()
        Concrete-->>HA: Fire EVENT_MAGICAREAS_AREA_READY
        SMA->>Platforms: async_forward_entry_setups()
        Platforms-->>HA: Register Entities (Sensor, Light, Fan, etc.)
    else Meta Area
        SMA->>Meta: MagicMetaArea.initialize()
        Meta->>Meta: Aggregate Child Area Entities & States
        SMA->>Platforms: async_forward_entry_setups() (Meta platforms)
    end
    Note over SMA,HA: When all areas initialized: Fire EVENT_MAGICAREAS_READY
```

---

## 3. Core State Machine Engine (`AreaStateSensor`)

The area state is exposed as an enum sensor (`sensor.simply_magic_areas_state_<area_slug>`) backed by `SensorDeviceClass.ENUM`. The state machine operates on a two-tier hierarchy: **Main States** (governing area presence and automation lifecycle) and **Substates** (governing lighting levels and scenes while the area is occupied).

### 3.1 Main States (Presence & Lifecycle)

The main state defines whether the room is occupied or vacant, and whether automated control is enabled. **The `occupied` state is based entirely on the presence of the presence sensors in the room.**

| State | Identifier | Description |
| :--- | :--- | :--- |
| **Clear** | `clear` | Idle base state. No presence detected by presence sensors and all timeout buffers have expired. Automated lights turn off or dim to clear level. |
| **Occupied** | `occupied` | Primary presence state. **Based entirely on active detection from presence sensors** (motion sensors, occupancy sensors, media playback, or room presence sensors). Acts as the parent container for lighting substates. |
| **Extended** | `extended` | Transitionary timeout grace period after presence sensors cease detecting presence before reverting to `clear`. Keeps area ready or dimly lit for quick re-entry. |
| **Manual** | `manual` | Override state entered when the area system control switch is turned OFF (`switch.simply_magic_areas_system_control_<area>`). Suspends all automated state transitions and device controls across the area. *(Note: Individual light manual override also transitions lights into a manual state for a configurable timeout; see Section 4.1).* |

### 3.2 Occupied Substates (Lighting Context Modifiers)

Substates (`sleep`, `bright`, `accented`) are contextual modifiers that take effect **only when the area is in the `occupied` main state**. They dictate what light fixtures turn on and what dimming levels to apply:

| Substate | Identifier | Context & Lighting Behavior |
| :--- | :--- | :--- |
| **Standard Occupied** | `occupied` | Default active profile when no secondary context overrides apply. Activates standard task and ambient lighting at configured daytime/occupied brightness. |
| **Sleep** | `sleep` | Bedtime/nighttime mode is active (e.g., bed presence or night mode helper). Configures low-level pathway lighting or night-lights at minimal brightness. |
| **Bright** | `bright` | High ambient lux / daylight threshold is active. Keeps lights off or dialed down to avoid redundant daytime illumination. |
| **Accented** | `accented` | Accent entity is active (e.g. party mode, dinner scene, decorative lighting). Prioritizes accent and architectural lighting fixtures. |

### 3.3 Substate Evaluation & Interaction with Main States

Substates (`sleep`, `bright`, `accented`) control **what happens when the state is occupied in terms of what levels to set the lights at and which fixtures to activate**.

> [!IMPORTANT]
> **Substate Transition Rule for Lights:**
> - When the contextual state changes (e.g. from `bright` to `sleep` at sunset or bedtime), the state of the lights **must stay as `occupied`, `clear`, `extended`, or `manual` and not change**.
> - If the lights are currently ON (`occupied`), transitioning between substates (`sleep`, `bright`, `accented`) causes the lights to **immediately update to the new target values and fixtures**.
> - If the lights are currently OFF (`clear`, `extended`, `manual`), transitioning between substates does **not** turn lights on; they remain off until presence is detected.

#### State & Lighting Transition Matrix

| Current Main State | Current Light State | Context / Substate Change | Resulting Main State | Resulting Light State | Action Taken |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`clear`** | OFF | `bright` ➔ `sleep` (or any substate change) | `clear` (Unchanged) | OFF (Unchanged) | None. Lights remain off. No unprompted illumination. |
| **`extended`** | OFF / Dim | `bright` ➔ `sleep` (or any substate change) | `extended` (Unchanged) | Unchanged | None. Extended countdown continues unaffected. |
| **`manual`** | Any | `bright` ➔ `sleep` (or any substate change) | `manual` (Unchanged) | Unchanged | None. Automation remains completely suspended. |
| **`occupied`** | ON | `bright` ➔ `sleep` | `occupied` (Unchanged) | ON (Unchanged) | **Immediately updates** brightness to sleep level & activates sleep fixtures. |
| **`occupied`** | ON | `sleep` ➔ `bright` | `occupied` (Unchanged) | ON (or OFF if 0% lux cutoff) | **Immediately updates** brightness to daylight level (or off if 0%). |
| **`occupied`** | ON | `occupied` ➔ `accented` | `occupied` (Unchanged) | ON (Unchanged) | **Immediately updates** to accent scene/dim levels and accent fixtures. |
| **`clear`** ➔ **`occupied`** | OFF | Motion / Presence detected in `sleep` mode | `occupied` | ON | Turns ON lights configured specifically for `sleep` mode. |

### 3.4 State Transition Diagram

```mermaid
stateDiagram-v2
    [*] --> Clear

    Clear --> Occupied: Presence Detected
    Occupied --> Extended: Presence Ceases (Clear Timeout Elapsed)
    Extended --> Clear: Extended Timeout Elapsed
    Extended --> Occupied: Presence Re-detected

    state Occupied {
        [*] --> StandardOccupied
        note right of StandardOccupied: Standard lights & daytime dim level
        
        StandardOccupied --> Sleep: Sleep Entity Active
        StandardOccupied --> Bright: High Lux / Bright Entity Active
        StandardOccupied --> Accented: Accent Entity Active

        Sleep --> StandardOccupied: Sleep Entity Inactive
        Bright --> StandardOccupied: Bright Entity Inactive
        Accented --> StandardOccupied: Accent Entity Inactive
    }

    state AnyMainState {
        Clear
        Occupied
        Extended
    }

    AnyMainState --> Manual: System Control Switch turned OFF
    Manual --> Clear: System Control Switch turned ON
```

### 3.5 Presence Detection Sources

> [!NOTE]
> **Occupancy Derivation Principle:** The `occupied` main state is based **entirely on the presence of the presence sensors in the room**. Contextual entities (such as sleep switches, bright/lux entities, or accent toggles) do not influence presence detection; they only modulate lighting behavior once occupancy has been established by presence sensors.

The presence state is continuously tracked from the following configured platforms and device classes:
- **Binary Sensors**: Primary occupancy indicators matching configured device classes (`motion`, `occupancy`, `presence`).
- **Media Players**: Filtered by active playing states (`playing`) when media player presence tracking is enabled.
- **MQTT Room Sensors**: Distance/RSSI room tracking entities matching the area slug (`CONF_MQTT_ROOM_PRESENCE`).
- **Humidity Trend Monitor**: Optional derivative rate-of-change statistics (`MagicStatisticsSensor`) to detect shower/bath occupancy before absolute humidity thresholds trigger.

---

## 4. Platform Controllers & Capabilities

### 4.1 Lighting (`light.py`)
- **Entity**: `AreaLightGroup` (`light.simply_magic_areas_light_<area>`).
- **State-Driven Lighting Profiles**:
  - Each Main State (`clear`, `extended`) and Occupied Substate (`occupied`, `sleep`, `bright`, `accented`) has dedicated configuration:
    - Target dim level percentage (0–100%).
    - Explicit list of active lighting entities assigned to that substate.
- **Substate Transition Rules**:
  - **State Invariance**: When the contextual substate changes (such as from `bright` to `sleep`), the state of the lights stays as `occupied`, `clear`, `extended`, or `manual` and does not change.
  - **Lights ON (`occupied`)**: If the lights are on (`occupied`) and the contextual substate changes between `sleep`, `bright`, and `accented`, the lights **immediately update to the new values** (brightness level, color temperature, and active entity subset).
  - **Lights OFF (`clear`, `manual`, or `bright` at 0%)**: If the lights are off, substate changes (e.g. `bright` to `sleep` at night) do **not** trigger any light command. The lights remain off and the state remains `clear` or `manual` without change.
  - **New Presence Detected**: When the area transitions from `clear` or `extended` into `occupied`, the lights turn ON using the active substate's configured settings.
- **Manual Light State & Override (`CONF_MANUAL_TIMEOUT`)**:
  - **Transition to Manual State**: If someone manually adjusts a light (turns a light on or off, adjusts brightness, or changes color via a physical wall switch, smart dimmer, external integration, or Home Assistant UI), the light entity automatically transitions into a **Manual State** (exposing `manual_control: true` in its extra state attributes).
  - **Override Automatic State**: While in this manual state, the automatic lighting control is completely overridden and suspended. Automatic area state changes, substate context shifts (e.g. transitioning from `bright` to `sleep`), or clearance timeout transitions will **not** modify, turn off, or turn on the lights. This guarantees that automated routines do not "fight" human manual preferences.
  - **Configurable Override Duration**: The manual state persists for a user-configurable duration (`manual_timeout`, default: `60s`). Each subsequent manual adjustment resets the timeout back to the full configured duration.
  - **Automatic Restoration**: When the configured `manual_timeout` period elapses without further manual interactions, the manual state automatically clears (`manual_control: false`). The light returns to automatic control and smoothly realigns with the current area state and active substate profile.
- **Rapid Transition & Race Condition Resilience**:
  - **Forced Turn-On on Occupancy**: When transitioning into an occupied state from non-occupied (`clear` or `extended`), `_turn_on_light` enforces execution (`force=True`). It bypasses cached state checks (`is_on` / matching brightness) so that a pending in-flight turn-off never prevents the lights from being commanded on.
  - **Turn-Off Occupancy Guard**: `_turn_off_light` verifies that the area is not currently in an occupied state before issuing any off command. If the area has transitioned back to occupied, the turn-off is aborted immediately.
  - **Automatic Recovery from Racing Extinguishment**: If the light group transitions to `off` while the area is in an active occupied state (and not in manual override mode), the state change handler intercepts the discrepancy and immediately re-asserts `_turn_on_light(force=True)`.
  - **Presence Timer Preemption**: When a presence sensor reports `on`, all running clear and extended timers in `AreaStateSensor` are cancelled synchronously, preventing background timer callbacks from clearing the area.
  - **Unoccupied State Preservation**: In `AreaStateSensor`, when the area is `clear` or `extended`, non-occupied sensor state updates do not reset off-time counters or cause premature occupancy fall-throughs.
  - **Extended State Sensor Reactivity**: If the area is in the `extended` state, any state change in the sensors (presence sensors or room tracking sensors) immediately moves the area state back to `occupied`, cancels running extended timeouts, restarts the clear countdown buffer, and re-engages lighting automations (turning off exhaust ventilation unless prolonged by high humidity).

### 4.2 Ventilation & Fans (`fan.py`)
- **Entity**: `AreaFanGroup` (`fan.simply_magic_areas_fan_<area>`).
- **Post-Occupancy Ventilation (`extended` state)**:
  - **Bathroom & Room Exhaust**: When occupants leave the room and the area transitions from `occupied` to `extended` (e.g. leaving a bathroom), the lighting turns off (or dims to clear level) and the exhaust fan **automatically turns ON**.
  - **Extended Duration**: The fan remains on for the duration of the configured `extended_timeout` period before turning off when transitioning to `clear`, unless prolonged by elevated humidity.
- **Enhanced Humidity & Climate Differential Ventilation**:
  - **Reference Baseline Comparison (`CONF_REFERENCE_HUMIDITY_SENSOR`)**:
    - Compares room humidity against a reference entity (e.g., climate entity `current_humidity`, outdoor weather humidity sensor, hallway sensor, or `Interior` Meta-Area average).
    - **Activation Delta (`CONF_HUMIDITY_DELTA_THRESHOLD`, default: `10.0%`)**: Area humidity must be at least 10% above the reference humidity baseline to trigger shower ventilation, preventing whole-house weather shifts from triggering exhaust fans.
    - **Shut-off Delta (`CONF_HUMIDITY_DELTA_SHUTOFF`, default: `3.0%`)**: The fan turns off once room humidity returns to within 3% of the reference baseline, even if ambient weather keeps absolute indoor humidity elevated.
  - **Shower Spike vs. Weather Gradient Recognition**:
    - Continuously monitors rate-of-rise via `MagicStatisticsSensor` using Home Assistant's `change_second` metric (`%/s`). Fast steam generation from a shower (exceeding `CONF_HUMIDITY_TREND_UP_CUT_OFF`, default `0.03 %/s` ≈ 108%/hr) is distinguished from gradual weather drift (+1–3%/hr).
  - **Deadband Flatline Deactivation (Fix for `!= 0.0`)**:
    - Replaces exact `0.0` derivative checks with a configurable deadband `CONF_HUMIDITY_DEADBAND` (default: `0.001 %/s` ≈ 3.6 %/hr).
    - When the derivative hovers within `[-0.001, +0.001] %/s` continuously for `CONF_HUMIDITY_ZERO_WAIT_TIME` (default: `900s` / 15 minutes), the humidity curve is confirmed stabilized and the fan turns off.
  - **Falling Trend Deactivation**:
    - The fan turns off if rate-of-change drops below `CONF_HUMIDITY_TREND_DOWN_CUT_OFF` (default: `-0.003 %/s` ≈ -10.8 %/hr), confirming rapid drying.
  - **Presence Gating (`CONF_HUMIDITY_PRESENCE_GATE_TIME`, default: `0s` / disabled)**:
    - Prevents ambient climate changes from triggering fans in empty bathrooms when no shower has occurred. When configured (> 0), humidity activation is only allowed if the room is currently occupied or was occupied within the gate window.
  - **Safety Maximum Runtime (`CONF_HUMIDITY_MAX_FAN_TIME`, default: `3600s` / 60 minutes)**:
    - A hard safety cutoff capping continuous humidity-driven fan runtime to prevent fan motor burnout or continuous conditioned air exhaustion under sustained weather events.
  - **State Precedence & Area State Synchronization**:
    - While the room is actively in `extended` state, the fan will not be turned off prematurely by humidity normalization. Similarly, humidity-driven ventilation stays active across `extended` -> `clear` transitions until all shutoff criteria or timeouts are met.
    - `AreaStateSensor` synchronizes its occupancy derivation and attributes (`humidity_on`, `humidity_delta`, `humidity_zero_ts`) with the differential threshold, deadband, and presence gating so climate humidity drift does not falsely force areas into `occupied`.
  - **Exposed Fan State Attributes**:
    - `humidity_on`: Boolean indicating whether humidity ventilation is active.
    - `humidity_delta`: Floating-point difference between room humidity and reference humidity.
    - `reference_humidity`: Current value of the reference baseline sensor.
    - `humidity_start_ts`: Epoch timestamp when current humidity ventilation session began.
    - `humidity_zero_ts`: Epoch timestamp when humidity derivative entered the stationary deadband.
    - `max_runtime_exceeded`: Safety flag set when the fan is stopped due to exceeding `humidity_max_fan_time`.
- **Manual Fan Override (`CONF_MANUAL_TIMEOUT`)**:
  - Manual adjustments (wall switches, UI) transition the fan group into manual override mode (`manual_control: true`), suspending automated adjustments for `manual_timeout` seconds (default: `60s`).

### 4.3 Climate (`climate.py`)
- **Entity**: `AreaClimateGroup` (`climate.simply_magic_areas_climate_<area>`).
- **Consolidated Thermostat Control**:
  - Unifies multiple HVAC devices in an area.
  - Averages ambient temperature readings (`mean` reduction).
  - Synchronizes target temperatures, HVAC modes (`heat`, `cool`, `auto`, `off`), fan modes, swing modes, and presets across child units.
- **Occupancy Setbacks**:
  - Automatically adjusts climate to comfort setpoints upon occupancy.
  - Applies eco/setback temperatures during `clear` or `sleep` states.

### 4.4 Media Players & Global Notification (`media_player.py`)
- **Area Group**: `AreaMediaPlayerGroup` (`media_player.simply_magic_areas_media_player_<area>`).
  - Aggregates speakers/smart TVs per area; syncs playback state and volume.
- **Area-Aware Media Player (AAMP)**: `AreaAwareMediaPlayer` (Global Meta-Area).
  - **Dynamic Routing**: Dispatches `play_media` calls and TTS announcements dynamically to whichever room currently has active occupancy.
  - If no area is actively occupied, falls back to pre-configured fallback audio sinks or the entire house group.

### 4.5 Binary Sensors & Health Distress Monitor (`binary_sensor.py`)
- **Aggregate Device Classes**:
  - Groups binary sensors having `CONF_AGGREGATES_MIN_ENTITIES` (e.g. `door`, `window`, `motion`).
  - Aggregates using `any` (default) or `all` mode depending on `AGGREGATE_MODE_ALL`.
- **Distress / Area Health Sensor**:
  - Creates `binary_sensor.simply_magic_areas_problem_<area>`.
  - Monitors `smoke`, `gas`, `moisture`, `safety`, and `problem` classes.
  - Fires immediate alerts if any life-safety or environmental hazard sensor is tripped.

### 4.6 Environmental & Statistical Sensors (`sensor.py`)
- **Group Aggregators**:
  - Calculates `mean` (temperature, illuminance) or `sum` (energy, power) for numeric sensors in the area.
- **Statistical Derivatives**:
  - Generates moving statistical analysis (percentiles, rolling averages) via Home Assistant's `StatisticsSensor`.
  - Powers humidity rate-of-rise trend tracking.

### 4.7 Automation Switch (`switch.py`)
- **Entity**: `AreaSystemControlSwitch` (`switch.simply_magic_areas_system_control_<area>`).
- **Function**:
  - Master kill switch for all automated behaviors within the given area.
  - When switched `off`, sets area state to `manual`. When switched `on`, immediately resumes automatic state evaluation.

---

## 5. Entity ID & Unique ID Specifications

| Domain | Entity Translation Key | Entity ID Format | Unique ID Format |
| :--- | :--- | :--- | :--- |
| `sensor` | `state` | `sensor.simply_magic_areas_state_<area_slug>` | `simply_magic_areas_state_<area_slug>` |
| `switch` | `system_control` | `switch.simply_magic_areas_system_control_<area_slug>` | `simply_magic_areas_system_control_<area_slug>` |
| `light` | `light` | `light.simply_magic_areas_light_<area_slug>` | `simply_magic_areas_light_<area_slug>` |
| `fan` | `fan` | `fan.simply_magic_areas_fan_<area_slug>` | `simply_magic_areas_fan_<area_slug>` |
| `climate` | `climate` | `climate.simply_magic_areas_climate_<area_slug>` | `simply_magic_areas_climate_<area_slug>` |
| `media_player`| `media_player` | `media_player.simply_magic_areas_media_player_<area_slug>` | `simply_magic_areas_media_player_<area_slug>` |
| `media_player`| `area_aware_media_player` | `media_player.simply_magic_areas_area_aware_media_player_<slug>`| `simply_magic_areas_area_aware_media_player_<slug>` |
| `cover` | `cover_<class>` | `cover.simply_magic_areas_cover_<class>_<area_slug>` | `simply_magic_areas_cover_<class>_<area_slug>` |
| `binary_sensor`| `<device_class>` | `binary_sensor.simply_magic_areas_<class>_<area_slug>` | `simply_magic_areas_<class>_<area_slug>` |
| `binary_sensor`| `problem` | `binary_sensor.simply_magic_areas_problem_<area_slug>` | `simply_magic_areas_problem_<area_slug>` |

---

## 6. Configuration & Feature Schema

### 6.1 Core Config Flow Options
- `type`: Area classification (`interior`, `exterior`, `meta`).
- `clear_timeout`: Seconds of sensor inactivity before transitioning `occupied` -> `extended` (default: `60s`).
- `extended_timeout`: Seconds of sensor inactivity before transitioning `extended` -> `clear` (default: `120s`).
- `manual_timeout`: Seconds to remain in manual override mode when a light is manually adjusted before returning to automatic control (default: `60s`).
- `update_interval`: Periodic heartbeat re-evaluation interval (default: `60s`).

### 6.2 Feature Flags (`CONF_ENABLED_FEATURES`)
- `advanced_light_groups`: Enables scene-driven brightness and entity controls for lights.
- `fan_control`: Enables automatic occupancy and humidity-based fan operation.
- `climate_control`: Enables climate group entity and presence-based temperature setpoint coordination.
- `media_player_groups`: Enables area speaker synchronization.
- `area_aware_media_player`: Enables global dynamic media routing (Global Meta-Area only).
- `cover_groups`: Enables cover grouping by device class.
- `aggregation`: Enables numeric/binary sensor grouping.
- `health`: Enables distress/problem aggregation for environmental safety.
- `humidity`: Enables humidity rate-of-change statistics sensor.

### 6.3 Fan & Humidity Control Options (`CONF_FEATURE_HUMIDITY`)
- `reference_humidity_sensor`: Entity ID of baseline humidity (climate entity, outdoor weather, hallway, or interior average).
- `humidity_delta_threshold`: Minimum humidity % above reference to trigger ventilation (default: `10.0%`).
- `humidity_delta_shutoff`: Delta % above reference below which the fan turns off (default: `3.0%`).
- `humidity_deadband`: Rate-of-change deadband (%/s) considered stationary/flatlined (default: `0.001 %/s` ≈ 3.6 %/hr).
- `humidity_zero_wait_time`: Duration (seconds) the rate must remain within deadband before fan shuts off (default: `900s` / 15 min).
- `humidity_max_fan_time`: Absolute safety cutoff (seconds) for continuous humidity fan operation (default: `3600s` / 60 min).
- `humidity_presence_gate_time`: Maximum seconds since last occupied state to allow humidity activation (default: `0s` / disabled).
- `humidity_trend_up_cut_off`: Minimum rising rate-of-change (%/s) required to trigger ventilation (default: `0.03 %/s`).
- `humidity_trend_down_cut_off`: Falling rate-of-change (%/s) that triggers fan shutoff (default: `-0.003 %/s`).

---

## 7. Architectural Roadmap & Recommended Enhancements

The following design recommendations represent planned extensions to elevate area intelligence:

1. **Sleep Mode Acoustic Fan Suppression (`CONF_FAN_SUPPRESS_IN_SLEEP`)**:
   - In en-suite bathrooms, suppress loud exhaust fans during `extended` mode when the bedroom/en-suite is in the `sleep` substate, unless actively forced by high differential humidity.
2. **Door Contact Interlocking for Bathroom Occupancy**:
   - If a bathroom is occupied when the door is closed, maintain the `occupied` state regardless of motion sensor gaps (e.g. while bathing behind shower glass/curtains) until the door opens.
3. **Direct Numeric Illuminance Threshold (`CONF_LUX_THRESHOLD`)**:
   - Allow entering the `bright` daylight substate directly using an aggregated illuminance sensor lux reading (e.g. `250 lx`) without requiring external template helpers.
4. **Persistent Timer Rehydration across Restarts**:
   - Store countdown target timestamps in state attributes (`clear_timeout_ts`, `manual_timeout_ts`) so remaining timeouts resume accurately following a Home Assistant restart.
5. **Multi-Speed Exhaust Fan Support**:
   - Configure distinct speed percentages for background `extended` ventilation (e.g. 40%) versus high-speed steam clearance (`humidity_on`, 100%).

