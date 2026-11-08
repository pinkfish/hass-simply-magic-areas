# Simply Magic Areas for Home Assistant

Originally forked from [jseidl/hass-magic_areas](https://github.com/jseidl/hass-magic_areas), **Simply Magic Areas** is an autonomous area intelligence platform for Home Assistant featuring 100% local on-device machine learning, radar fan discrimination, thermodynamic shower moisture evacuation, intelligent sleep & bedroom automation, and multi-light room synchronization.

![Simply Magic Areas](https://raw.githubusercontent.com/home-assistant/brands/master/custom_integrations/simply_magic_areas/icon.png)

![Build Status](https://github.com/pinkfish/hass-simply-magic-areas/actions/workflows/validation.yaml/badge.svg) ![Test Status](https://github.com/pinkfish/hass-simply-magic-areas/actions/workflows/test.yaml/badge.svg) [![Latest release](https://img.shields.io/github/v/release/pinkfish/hass-simply-magic-areas.svg)](https://github.com/pinkfish/hass-simply-magic-areas/releases) [![GitHub latest commit](https://badgen.net/github/last-commit/pinkfish/hass-simply-magic-areas)](https://GitHub.com/pinkfish/hass-simply-magic-areas/commit/) [![GitHub contributors](https://badgen.net/github/contributors/pinkfish/hass-simply-magic-areas)](https://GitHub.com/pinkfish/hass-simply-magic-areas/graphs/contributors/)

Tired of writing the same automations over and over for each of your rooms? **Simply Magic Areas** gives Home Assistant the "missing batteries" for whole-home automation—automatically grouping presence and environmental sensors per area and managing lighting, fans, climate, and media with zero cloud dependencies.

---

## ✨ What's New & Key Features

### 🌙 Intelligent Sleep Mode, Bedroom Isolation & Remote Switches
* **Automatic Night Sleep Mode & Auto-Dimming (`auto_night_sleep`)**: Automatically transitions unoccupied main-house areas into `sleep` mode after 10:00 PM (`auto_sleep_after_hour: 22`), and transitions bedrooms into `sleep` mode after the bedroom light completes a bedtime `ON -> OFF` cycle. Once an area enters `sleep` mode, any currently active lights **automatically dim** to `sleep_state_dim` (`30%`), and any light turned on (automatically or manually) turns on dimmed until morning (`07:00` / sunrise or 5 minutes of sustained morning activity in a living area).
* **Manual Sleep Mode Switches for Remotes & Wall Buttons (`switch.simply_magic_areas_sleep_mode_<area>`)**: Every area and meta-area exposes a persistent **Sleep Mode** switch (plus optional external `sleep_entity` binding) so Zigbee/Z-Wave remotes, bedside buttons, and wall switches can toggle house-wide or bedroom sleep mode with a single press.
* **Independent Main-House vs. Bedroom Occupancy & `Bedrooms` Meta-Area**: Movement in bedrooms and en-suites is isolated from `Interior` and `Global` meta-areas so tossing and turning at night never keeps the main house occupied. Group multi-room suites (e.g., `["master_bedroom", "ensuite"]`) under the `Bedrooms` meta-area with automatic unoccupied-when-lights-off tracking (`unoccupied_when_lights_off: true`).

### 💡 Multi-Light Room Synchronization, Settling & Smart-Home UX
* **Multi-Light Room Synchronization & 250ms Settling/Smoothing**: Manually switching or dimming any single light in a multi-light room automatically synchronizes all other lights in that room to the exact same `ON`/`OFF` state and brightness. A `250ms` dimmer settling window, PWM jitter deadband ($\le 8$ units), smart-bulb power-on disambiguation (`unavailable -> on`), and desired-state tracking (`_expected_state`, `_expected_brightness`) prevent feedback loops and light flashing.
* **Fast Manual-Off Unoccupied Reset (`10s`)**: Manually turning off a room's lights shortens the unoccupied clearance window to `10s`, immediately clears `manual_control` once unoccupied, and guarantees instant re-illumination when you walk back into the room.
* **Immediate Hierarchical System Control Master Switch**: Turning off `switch.simply_magic_areas_system_control_<area>` (or on a parent `Interior`/`Global`/`Bedrooms` meta-area) immediately halts all automated lighting, fan, climate, and media actions and cancels all queued retry, settle, pre-warm, and wake-up timers.
* **Welcome-Home Arrival Pre-Warming, 50% Extended Warning Dim & All-Away Sweep**: Pre-illuminates interior entry/foyer rooms on nighttime arrival (`person.*`, `proximity.*`, or exterior motion), gently dims lights to 50% during `extended` mode before shutoff, and sweeps forgotten lights/overrides off when everyone leaves home.

### 🧠 On-Device Machine Learning & Ventilation (100% Local)
* **Online Bayesian Lighting Preference Learner**: Real-time Recursive Least Squares (RLS) regression ($< 1\mu\text{s}$) learns your preferred brightness from manual dimmer adjustments across ambient lux and solar elevation.
* **mmWave Radar Fan vs. Human Filter**: Eliminates false occupancy from spinning ceiling or exhaust fan blades using distance-gate masking and multi-signal human override verification.
* **Dual-Stage Shower Classifier, Exponential Moisture Evacuation & HVAC Fan Circulation**: Models bathroom drying trajectories ($\ln(\Delta H(t)) = \ln(\Delta H_{peak}) - kt$) to shut off exhaust fans at the exact dry return point, suppresses noisy exhaust fans during sleep hours unless a shower is detected, and runs overhead ceiling fans at slow speed (`25%`) while HVAC is actively heating or cooling.

---

## 📦 Installation via HACS

1. Open **HACS** > Search for **Simply Magic Areas** (or add `https://github.com/pinkfish/hass-simply-magic-areas` under **Custom repositories** as an **Integration**).
2. Click **Download**, then restart Home Assistant.
3. Go to **Settings** > **Devices & Services** > **Add Integration** and search for **Simply Magic Areas**.

---

## 📚 Documentation & Configuration Guides

- 📘 **[Basic Area Configuration Guide](docs/basic_area_configuration.md)**: Area types, timeouts, light/fan toggles, dim levels, and Meta-Area policy inheritance.
- 💡 **[Lighting, Presence & Exterior Guide](docs/lighting_and_presence.md)**: Sleep mode & bedroom isolation, multi-light synchronization & settling, Bayesian ML brightness learner, mmWave radar fan filter, and exterior nighttime lighting.
- 💧 **[Ventilation & Shower Humidity Guide](docs/ventilation_and_humidity.md)**: Acoustic sleep fan suppression, zero-config baseline resolution, adaptive shower learning, and HVAC overhead fan circulation.
- 🛠️ **[Advanced Features, Aggregates & System Control Guide](docs/advanced_features.md)**: System control & sleep mode switches, climate groups, Area-Aware Media Player (AAMP), and sensor aggregates.
- 📐 **[System Technical Specification](SYSTEM_SPEC.md)**: Complete architectural invariants and state machine definitions.
