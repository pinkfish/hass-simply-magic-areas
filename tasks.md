# Simply Magic Areas — Project Tasks & Roadmap

This document tracks all active, planned, and completed tasks for the **Simply Magic Areas** Home Assistant integration. Development must adhere to the engineering standards codified in [constitution.md](constitution.md) and technical requirements defined in [SYSTEM_SPEC.md](SYSTEM_SPEC.md).

---

## 📋 Open Tasks (Planned & In Progress)

### 1. Acoustic Fan Suppression in Sleep Mode
- **Feature**: `CONF_FAN_SUPPRESS_IN_SLEEP`
- **Description**: In en-suite bathrooms, suppress loud exhaust fans during `extended` mode when the bedroom or en-suite area is in the `sleep` substate, unless actively forced by elevated differential humidity (`humidity_on`).
- **Target Platform**: `fan.py`, `config_flow.py`, `const.py`
- **Status**: ⏳ Open / Proposed

### 2. Door Contact Interlocking for Bathroom Occupancy
- **Feature**: Contact sensor occupancy extension
- **Description**: If a bathroom is occupied when the door contact sensor closes, maintain the `occupied` state regardless of motion sensor dead zones (e.g., behind shower glass/curtains) until the door opens again.
- **Target Platform**: `area_state_sensor.py`, `config_flow.py`, `const.py`
- **Status**: ⏳ Open / Proposed

### 3. Multi-Speed Exhaust Fan Support
- **Feature**: Proportional ventilation speed control
- **Description**: Allow configuration of distinct percentage fan speeds for background post-occupancy `extended` ventilation (e.g. 40%) versus high-speed steam extraction (`humidity_on`, 100%).
- **Target Platform**: `fan.py`, `config_flow.py`, `const.py`
- **Status**: ⏳ Open / Proposed

---

## ✅ Completed Tasks

### Architecture & Concurrency
- [x] **Project Constitution Creation**: Codified governing engineering invariants for Python, Home Assistant integration patterns, DRY design, radical simplicity, and async thread-safety in [constitution.md](constitution.md).
- [x] **Non-Blocking Asynchronous Service Calls**: Migrated synchronous blocking `services.call(...)` to thread-aware non-blocking `call_service_non_blocking` (`hass.async_create_task` / `hass.create_task`) across all actuator groups ([light.py](custom_components/simply_magic_areas/light.py), [fan.py](custom_components/simply_magic_areas/fan.py), [climate.py](custom_components/simply_magic_areas/climate.py), and [media_player.py](custom_components/simply_magic_areas/media_player.py)).
- [x] **Thread-Safety & Loop Marshaling**: Preserved event loop boundaries using `loop.call_soon_threadsafe(...)` and `schedule_update_ha_state()` to eliminate race conditions and avoid Home Assistant thread-safety runtime errors.
- [x] **DRY Multi-Tier Illuminance Consolidation**: Unified duplicated 3-tier illuminance resolution logic from `AreaStateSensor` and `LightGroup` into [`MagicArea.get_effective_illuminance()`](custom_components/simply_magic_areas/base/magic.py).
- [x] **Meta-Area Active Area Tracking**: Fixed and restored dynamic active child area tracking in [`MagicMetaArea.get_active_areas()`](custom_components/simply_magic_areas/base/magic.py) and `ATTR_ACTIVE_AREAS` state sensor attributes.
- [x] **Active Area Test Suite**: Created comprehensive unit and integration test suite in [test_active_areas.py](custom_components/simply_magic_areas/tests/test_active_areas.py) covering regular and meta area active area resolution (56/56 tests passing).

### Lighting & Presence Resilience
- [x] **Rapid State Toggle Race Condition Resilience**: Hardened `_turn_on_light` with `force=True` on occupancy transitions, added turn-off guards, and implemented automatic re-assertion if lights turn off while occupied.
- [x] **Extended State Sensor Reactivity**: Implemented immediate transition from `extended` back to `occupied` upon any presence sensor state change, clearing extended countdown timers and re-engaging lighting automations.
- [x] **Substate Transition Isolation**: Guaranteed that contextual shifts between substates (`sleep`, `bright`, `accented`) dynamically update active fixtures/dim levels if lights are ON without unprompted illuminations when lights are OFF.
- [x] **Direct Numeric Lux & Inter-Area Brightness**: Added 3-tier brightness evaluation with direct lux threshold, hysteresis band, reference area inheritance, and reference lux sensor fallback.

### Ventilation & Climate
- [x] **Differential Humidity Fan Control**: Replaced absolute humidity triggers with reference baseline delta comparison (`CONF_REFERENCE_HUMIDITY_SENSOR`, `CONF_HUMIDITY_DELTA_THRESHOLD`, `CONF_HUMIDITY_DELTA_SHUTOFF`).
- [x] **Stationary Derivative Deadband Flatline Shutoff**: Added continuous rate-of-change deadband detection (`CONF_HUMIDITY_DEADBAND`, `CONF_HUMIDITY_ZERO_WAIT_TIME`) to prevent fans sticking on during post-shower humidity plateaus.
- [x] **Ventilation Presence Gating**: Added configurable presence gating window (`CONF_HUMIDITY_PRESENCE_GATE_TIME`) preventing weather humidity shifts from activating fans in vacant rooms.
- [x] **Safety Maximum Runtime Cutoff**: Enforced hard runtime cap (`CONF_HUMIDITY_MAX_FAN_TIME`) to protect fan motors and prevent excessive conditioned air exhaustion.

### Persistence & Reliability
- [x] **Timer Rehydration Across Restarts**: Added state attribute persistence for countdown timestamps (`clear_timeout_ts`, `extended_timeout_ts`, `manual_timeout_ts`) with automatic timer rehydration upon Home Assistant restart.
- [x] **Linter & Code Hygiene**: Purged dead methods (`_async_update_state`), unreferenced constants, duplicate dictionary keys, and fixed legacy platform dependencies (`DEPENDENCIES = ["cover"]`). Zero warnings under `ruff check`.
