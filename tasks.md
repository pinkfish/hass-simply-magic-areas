# Simply Magic Areas — Project Tasks & Roadmap

This document tracks all active, planned, and completed tasks for the **Simply Magic Areas** Home Assistant integration. Development must adhere to the engineering standards codified in [constitution.md](constitution.md) and technical requirements defined in [SYSTEM_SPEC.md](SYSTEM_SPEC.md).

---

## 📋 Open Tasks (Planned & In Progress)

### 1. Door Contact Interlocking for Bathroom Occupancy
- **Feature**: Contact sensor occupancy extension
- **Description**: If a bathroom is occupied when the door contact sensor closes, maintain the `occupied` state regardless of motion sensor dead zones (e.g., behind shower glass/curtains) until the door opens again.
- **Target Platform**: `area_state_sensor.py`, `config_flow.py`, `const.py`
- **Status**: ⏳ Open / Proposed

### 2. Multi-Speed Exhaust Fan Support
- **Feature**: Proportional ventilation speed control
- **Description**: Allow configuration of distinct percentage fan speeds for background post-occupancy `extended` ventilation (e.g. 40%) versus high-speed steam extraction (`humidity_on`, 100%).
- **Target Platform**: `fan.py`, `config_flow.py`, `const.py`
- **Status**: ⏳ Open / Proposed

---

## ✅ Completed Tasks

### Architecture & Concurrency
- [x] **Dual-Sensor Orthogonal State Architecture (Area Presence + Lighting Profile Separation)**: Completely decoupled physical occupancy states (`clear`, `occupied`, `extended`, `manual`) from ambient lighting scene states. Created companion entity [`AreaLightingProfileSensor`](custom_components/simply_magic_areas/base/area_lighting_profile_sensor.py) (`sensor.simply_magic_areas_lighting_profile_<area>`) exposing `standard`, `sleep`, `bright`, `accented`. Updated `AreaStateSensor` with `lighting_profile` attribute, and refactored `light.py`, `fan.py`, and `media_player.py` to decouple actuation power from profile modulation. Fully verified across all 62 tests.
- [x] **Project Constitution Creation**: Codified governing engineering invariants for Python, Home Assistant integration patterns, DRY design, radical simplicity, and async thread-safety in [constitution.md](constitution.md).
- [x] **Non-Blocking Asynchronous Service Calls**: Migrated synchronous blocking `services.call(...)` to thread-aware non-blocking `call_service_non_blocking` (`hass.async_create_task` / `hass.create_task`) across all actuator groups ([light.py](custom_components/simply_magic_areas/light.py), [fan.py](custom_components/simply_magic_areas/fan.py), [climate.py](custom_components/simply_magic_areas/climate.py), and [media_player.py](custom_components/simply_magic_areas/media_player.py)).
- [x] **Thread-Safety & Loop Marshaling**: Preserved event loop boundaries using `loop.call_soon_threadsafe(...)` and `schedule_update_ha_state()` to eliminate race conditions and avoid Home Assistant thread-safety runtime errors.
- [x] **DRY Multi-Tier Illuminance Consolidation**: Unified duplicated 3-tier illuminance resolution logic from `AreaStateSensor` and `LightGroup` into [`MagicArea.get_effective_illuminance()`](custom_components/simply_magic_areas/base/magic.py).
- [x] **Meta-Area Active Area Tracking**: Fixed and restored dynamic active child area tracking in [`MagicMetaArea.get_active_areas()`](custom_components/simply_magic_areas/base/magic.py) and `ATTR_ACTIVE_AREAS` state sensor attributes.
- [x] **Radical Simplicity & Zero-Config Defaults (Adaptive by Default)**: Enforced adaptive intelligence by default across both lighting (`CONF_LEARN_DAYLIGHT_LUX=True`) and ventilation (`CONF_LEARN_SHOWER_HUMIDITY=True`). Purged advanced lux calibration fields (`dark_lux_floor`, `learn_daylight_lux`, `daylight_lux_level`) from the basic area configuration menu, cleanly encapsulating them within dedicated Advanced Settings (`feature_conf_advanced_light_groups` and `feature_conf_humidity`). Guaranteed that users can turn off adaptive behaviors or customize numerical boundaries in advanced settings while everyday setup requires zero configuration. All 68 integration tests passing cleanly.
- [x] **Active Area Test Suite**: Created comprehensive unit and integration test suite in [test_active_areas.py](custom_components/simply_magic_areas/tests/test_active_areas.py) covering regular and meta area active area resolution (56/56 tests passing).

### Lighting & Presence Resilience
- [x] **Adaptive Lux, Historical Daylight Learning & Dark Floor Guarantee**: Formulated and implemented photometric adaptation in [designs/adaptive_lux_lighting_design.md](designs/adaptive_lux_lighting_design.md). Solved real-world lux sensor skew (300%–500% hardware variations across rooms) via autonomous daytime uncontaminated baseline learning (`CONF_LEARN_DAYLIGHT_LUX`). Enforced absolute Dark Lux Floor (`CONF_DARK_LUX_FLOOR`, default 10 lx) preventing windowless spaces (bathrooms, pantries, closets) from being falsely suppressed into darkness by exterior sun or meta-areas. Implemented proportional darkness compensation ($\text{darkness\_ratio} = \frac{L_{day} - L}{L_{day} - L_{floor}}$) with Stevens' power-law scaling ($\gamma = 0.65$) modulating turn-on brightness. Tested with dedicated integration test suite (11/11 tests passing, 66/66 total suite passing).
- [x] **Rapid State Toggle Race Condition Resilience**: Hardened `_turn_on_light` with `force=True` on occupancy transitions, added turn-off guards, and implemented automatic re-assertion if lights turn off while occupied.
- [x] **Extended State Sensor Reactivity**: Implemented immediate transition from `extended` back to `occupied` upon any presence sensor state change, clearing extended countdown timers and re-engaging lighting automations.
- [x] **Substate Transition Isolation**: Guaranteed that contextual shifts between substates (`sleep`, `bright`, `accented`) dynamically update active fixtures/dim levels if lights are ON without unprompted illuminations when lights are OFF.
- [x] **Direct Numeric Lux & Inter-Area Brightness**: Added 3-tier brightness evaluation with direct lux threshold, hysteresis band, reference area inheritance, and reference lux sensor fallback.

### Ventilation & Climate
- [x] **Autonomous Historical Humidity & Adaptive Shower Signature Learning**: Formulated thermodynamic and mathematical models in [designs/adaptive_humidity_shower_design.md](designs/adaptive_humidity_shower_design.md). Replaced hardcoded static boundaries with room-specific empirical learning (`CONF_LEARN_SHOWER_HUMIDITY`). Continuously tracks ambient baseline humidity ($H_{base}$) during idle periods ($|dRH/dt| < \text{trend\_up\_cutoff}$ and $RH \le H_{base} + 5\%$) using EMA ($\alpha=0.05$), enabling zero-config standalone delta derivation ($\Delta H = RH_{room} - H_{base}$) without requiring an external reference sensor. Extracts peak rise rate ($(dRH/dt)_{peak}$) and peak delta ($\Delta H_{peak}$) across shower events to dynamically scale trigger thresholds ($\Delta H_{trigger}$, $Trend_{trigger}$) and recovery shutoff target ($\Delta H_{shutoff}$). Built-in noise-floor guard rejects ambient jitter while absolute ceiling guard ($\ge 80\%$) guarantees mold protection. Validated across integration test suite with 14/14 passing tests in [test_fan.py](custom_components/simply_magic_areas/tests/test_fan.py) and 68/68 total integration suite tests.
- [x] **Bathroom Shower Humidity Dynamics & Fan Control**: Conducted thermodynamic and empirical rate-of-change analysis in [designs/bathroom_humidity_fan_design.md](designs/bathroom_humidity_fan_design.md). Solved legacy premature shutoff on negative drying slopes ($dRH/dt < 0$), eliminated AND-gate trigger deadlocks, implemented zero-config reference humidity fallback (Interior meta-area and neighboring interior areas), and established dual-trigger activation (rapid $dRH/dt$ rise OR sustained $\Delta RH$ differential) with dedicated integration test suite in [test_fan.py](custom_components/simply_magic_areas/tests/test_fan.py).
- [x] **Acoustic Fan Suppression in Sleep Mode**: Implemented `CONF_FAN_SUPPRESS_IN_SLEEP` in [fan.py](custom_components/simply_magic_areas/fan.py), [config_flow.py](custom_components/simply_magic_areas/config_flow.py), and [const.py](custom_components/simply_magic_areas/const.py). In en-suite bathrooms, loud exhaust fans are suppressed during `extended` mode when sleep mode is active (either locally or in a configured reference/sleep entity), shuts off running extended fans if sleep activates dynamically, re-engages fans if sleep ends while still extended, and safely allows elevated differential humidity (`humidity_on`) to override sleep suppression for moisture control.
- [x] **Differential Humidity Fan Control**: Replaced absolute humidity triggers with reference baseline delta comparison (`CONF_REFERENCE_HUMIDITY_SENSOR`, `CONF_HUMIDITY_DELTA_THRESHOLD`, `CONF_HUMIDITY_DELTA_SHUTOFF`).
- [x] **Stationary Derivative Deadband Flatline Shutoff**: Added continuous rate-of-change deadband detection (`CONF_HUMIDITY_DEADBAND`, `CONF_HUMIDITY_ZERO_WAIT_TIME`) to prevent fans sticking on during post-shower humidity plateaus.
- [x] **Ventilation Presence Gating**: Added configurable presence gating window (`CONF_HUMIDITY_PRESENCE_GATE_TIME`) preventing weather humidity shifts from activating fans in vacant rooms.
- [x] **Safety Maximum Runtime Cutoff**: Enforced hard runtime cap (`CONF_HUMIDITY_MAX_FAN_TIME`) to protect fan motors and prevent excessive conditioned air exhaustion.

### Persistence & Reliability
- [x] **Timer Rehydration Across Restarts**: Added state attribute persistence for countdown timestamps (`clear_timeout_ts`, `extended_timeout_ts`, `manual_timeout_ts`) with automatic timer rehydration upon Home Assistant restart.
- [x] **Linter & Code Hygiene**: Purged dead methods (`_async_update_state`), unreferenced constants, duplicate dictionary keys, and fixed legacy platform dependencies (`DEPENDENCIES = ["cover"]`). Zero warnings under `ruff check`.

### Upstream Enhancements & Bugfixes (jseidl/magic-areas)
- [x] **Unitless Sensor Aggregation (AQI)**: Enabled aggregation of unitless device classes (such as `SensorDeviceClass.AQI` and `SensorDeviceClass.PH`), resolving entity rejection and handling `unit_of_measurement=None` safely in [sensor.py](custom_components/simply_magic_areas/sensor.py).
- [x] **Device Registry Change Tracking**: Added `EVENT_DEVICE_REGISTRY_UPDATED` listener in [__init__.py](custom_components/simply_magic_areas/__init__.py) so moving a physical device to a different area triggers automatic reload of affected areas without requiring a Home Assistant restart.
- [x] **Energy & Utility Meter Long-Term Statistics**: Added `SensorStateClass.TOTAL_INCREASING` mapping for `gas` and `water` aggregates (and `TOTAL` for `energy`), enabling full native integration with the Home Assistant Energy Dashboard in [const.py](custom_components/simply_magic_areas/const.py) and [sensor.py](custom_components/simply_magic_areas/sensor.py).
- [x] **Self-Healing State Consistency & Restart Recovery**: Added `_validate_state_consistency()` guard in [base/area_state_sensor.py](custom_components/simply_magic_areas/base/area_state_sensor.py) to automatically detect and clear orphaned occupied states when all sensors are off and no timeout is pending. Tested with 4 dedicated unit tests (60/60 total tests passing).
