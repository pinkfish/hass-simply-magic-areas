# Manual Override Occupancy Hold Design

## 1. Problem Statement & Motivation

In previous versions of Simply Magic Areas:
1. **Aggressive Turn-On Re-assertion**: If a light group was turned off while the area was in an occupied state, `_update_group_state` assumed this was a race condition from a delayed turn-off command and immediately re-asserted `_turn_on_light(conf, force=True)`, directly defeating the user's manual action to turn off the light.
2. **Premature Manual Control Reset**: In `light.py`, line 554 called `self._reset_control()` whenever `self.area.state != AreaState.AREA_STATE_CLEAR`, erasing manual override whenever the room was occupied.
3. **Rigid 60-Second Countdown**: If an external manual adjustment was made while clear (or on fans), a 60-second timer (`CONF_MANUAL_TIMEOUT`, default: 60s) would expire and force the fixture back to automated control, regardless of whether someone was still in the room.

The user requirement states:
> *"the manual override doesn't seem to work. If the light or fan is turned on and off and not by the automation in the area, the system should respect that and hold it in that state until an occupied state change occurs. Keep manual as a switch that control if manual is on or off."*

---

## 2. Architecture & Design Principles

### 2.1 External Action Detection
Whenever a light or fan entity experiences a state change event (`_update_group_state`):
- If `self.last_update_from_entity` is `True`:
  The change was triggered by our own internal automation (`_turn_on_light`, `_turn_off_light`, `_turn_on_fan`, `_turn_off_fan`).
  Reset `self.last_update_from_entity = False` and update internal state attribute.
- If `self.last_update_from_entity` is `False`:
  The change was initiated externally (e.g. physical wall switch, smart dimmer, Home Assistant dashboard, or voice command).
  Engage manual override:
  `self._set_controlled_by_this_entity(False)` (`ATTR_MANUAL_CONTROL = True`).
  Cancel any active countdown timers.
  Update internal state (`_attr_is_on = new_state.state == STATE_ON`).
  Do not re-assert turn on or turn off.

### 2.2 Occupancy State Change Definition
The system holds the manually commanded state until an **occupied state change** occurs.
The area states are partitioned into:
- **Occupied States**:
  - `AreaState.AREA_STATE_OCCUPIED`
  - `AreaState.AREA_STATE_EXTENDED`
  - `AreaState.AREA_STATE_BRIGHT`
  - `AreaState.AREA_STATE_SLEEP`
  - `AreaState.AREA_STATE_ACCENTED`
- **Clear State**:
  - `AreaState.AREA_STATE_CLEAR`
- **Manual State**:
  - `AreaState.AREA_STATE_MANUAL`

An **Occupied State Change** occurs when:
$$\text{was\_occupied} \neq \text{now\_occupied} \quad \lor \quad \text{from\_state} == \text{AreaState.AREA_STATE_MANUAL}$$

Where:
$$\text{was\_occupied} = (\text{from\_state} \in \text{Occupied States})$$
$$\text{now\_occupied} = (\text{to\_state} \in \text{Occupied States})$$

#### Key Transition Rules:
1. **Clear $\to$ Occupied**:
   `was_occupied` (False) $\neq$ `now_occupied` (True) $\implies$ **Occupied State Change**.
   Manual hold is cleared via `self._reset_control()`. Automated lighting/fan control activates according to the active profile.
2. **Occupied / Extended $\to$ Clear**:
   `was_occupied` (True) $\neq$ `now_occupied` (False) $\implies$ **Occupied State Change**.
   Manual hold is cleared via `self._reset_control()`. Automated lights and fans turn off according to clear state logic.
3. **Occupied $\leftrightarrow$ Extended** (or Profile Change within Occupied):
   `was_occupied` (True) $==$ `now_occupied` (True) $\implies$ **No Occupied State Change**.
   If the entity is in manual override, automation commands, lighting profile updates, illuminance changes, and humidity changes are ignored, preserving the user's manual state.
4. **Manual Switch Toggle (AreaSystemControlSwitch)**:
   - When toggled **OFF**: The area enters `AreaState.AREA_STATE_MANUAL`. All automated processing is suspended.
   - When toggled **ON**: The area transitions from `AreaState.AREA_STATE_MANUAL` back to presence detection. Because $\text{from\_state} == \text{manual}$, any manual holds on individual fixtures are reset, returning the entire area to clean automated operation.

---

## 3. Component Updates

### 3.1 `custom_components/simply_magic_areas/light.py`
1. **Remove Racing Turn-On Re-assertion**:
   Delete lines 528–546 that re-asserted turn on when `new_state.state == STATE_OFF` and `self.area.state in occupied_states`.
2. **Remove Premature Reset on Occupied**:
   Delete `if self.area.state != AreaState.AREA_STATE_CLEAR: self._reset_control()`.
3. **Remove Rigid 60-Second Countdown on Manual Override**:
   External changes immediately set `_set_controlled_by_this_entity(False)`.
4. **Occupancy Hold Logic in `_area_state_change`**:
   Evaluate `occupancy_changed`. If `occupancy_changed` is `True`, clear manual override via `self._reset_control()`. If `occupancy_changed` is `False` and manual override is active, abort automated adjustments and maintain the manual state.
5. **Guard Profile Changes**:
   In `_profile_change`, if `not self._is_controlled_by_this_entity()`, abort profile adjustments to protect the manual hold.

### 3.2 `custom_components/simply_magic_areas/fan.py`
1. **Manual Override Hold in `_update_group_state`**:
   External changes set `_set_controlled_by_this_entity(False)` without a 60-second expiration.
2. **Occupancy Hold Logic in `_area_state_change`**:
   Evaluate `occupancy_changed`. Clear manual hold only on true occupancy transitions (`was_occupied != now_occupied`). Hold state across `occupied <-> extended`.
3. **Guard Humidity & Sleep Adjustments**:
   In `_humidity_sensor_change` and `_sleep_state_change`, if `not self._is_controlled_by_this_entity()`, ignore automated adjustments to preserve manual hold.

### 3.3 `custom_components/simply_magic_areas/switch.py`
Maintain `AreaSystemControlSwitch` (`switch.simply_magic_areas_system_control_<area>`) as the master area manual override switch.
- OFF = Area is manual (`AreaState.AREA_STATE_MANUAL`).
- ON = Area is automated.
