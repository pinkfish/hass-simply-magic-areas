# Ventilation & Shower Humidity Configuration Guide

This guide covers exhaust ventilation, acoustic sleep suppression, differential humidity tracking, and adaptive shower moisture learning in **Simply Magic Areas**.

---

## 📍 Accessing Ventilation & Humidity Settings

1. In Home Assistant, navigate to **Settings** > **Devices & Services** > **Simply Magic Areas**.
2. Click **Configure** on your bathroom or utility area.
3. In the feature selection menu, enable **Humidity** (`CONF_FEATURE_HUMIDITY`).
4. Basic fan toggles are located in the primary **Area Options** dialog.

---

## 🌙 Acoustic Fan Suppression at Night (Sleep Mode)

Exhaust fans running during midnight bathroom visits can disturb sleeping partners and children. Simply Magic Areas includes built-in **Acoustic Sleep Fan Suppression**:

| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Suppress Fan in Sleep** | `fan_suppress_in_sleep` | `false` | When enabled (`true`), the exhaust fan will **not** turn on during the post-occupancy `extended` state if sleep mode is active. |

### How to Configure Night / Sleep State
You can supply sleep state through any of the following methods:
1. **Direct Sleep Entity**: In **Area Options**, set **Sleep Entity** to your sleep toggle (e.g., `input_boolean.sleep_mode`, a bed occupancy sensor, or sleep schedule switch).
2. **Link to Master Bedroom (Ensuite Linking)**: In **Advanced Light & Illuminance Options**, set **Reference Bright Area** to your bedroom (e.g., `master_bedroom`). When the bedroom enters its `sleep` profile, the bathroom automatically suppresses the exhaust fan during nocturnal visits.
3. **Inherit from Meta-Area**: Configure a sleep entity once on the **Interior** or **Global** Meta-Area, and all rooms will inherit the policy automatically.

### Behavior & Safety Invariants
- **Routine Nocturnal Visits**: When sleep mode is active and someone enters the bathroom, lights follow the sleep profile (e.g. dimmed nightlight) and the exhaust fan **remains OFF** during the `extended` state.
- **Dynamic Acoustic Sleep Suppression**: If sleep mode activates while a fan is running in post-occupancy `extended` or humidity mode, the fan is suppressed and extinguished to protect household sleep. If sleep mode ends while the room remains in `extended` mode, the fan turns on for the remainder of the timeout.
- **Critical Emergency Moisture Failsafe**: If extreme moisture is detected ($\ge 80\%$), the fan activates unconditionally as a failsafe to protect against condensation, mold growth, or running water leaks. Moderate humidity levels honor sleep suppression.
- **Mutual Exclusion & Post-Shower Cooldown**: When a shower event concludes (via baseline return, delta shutoff, or exponential evacuation), the fan enters a 5-minute cooldown preventing immediate re-activation loops from static resting humidity differentials.
- **15-Minute Safety Lockout**: If maximum fan runtime (`humidity_max_fan_time`) is exceeded, the fan shuts off and engages a 15-minute motor safety lockout to prevent immediate restarts while preserving hardware.
- **Active Watchdog Timer**: A 60-second periodic watchdog runs continuously while the fan or humidity mode is active, guaranteeing that maximum runtimes and deadband timeouts trigger reliably even if derivative/statistics sensors flatline and stop emitting state change events.

---

## 💨 Post-Occupancy Exhaust Ventilation (Toilets vs. Bathrooms)

| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Fan Control** | `fan_control` | `true` | Master toggle for automated fan control. |
| **Extended Timeout** | `extended_timeout` | `360s` (6m) | Duration the fan runs after the room is vacated before turning off. |
| **Run Fan in Extended** | `fan_run_in_extended` | `auto` | When omitted / `auto`: **Toilets** (no shower/humidity tracking) run during the `extended` phase; **Bathrooms** (with shower/humidity tracking) do **not** run in `extended` and activate only when humidity changes. Can be explicitly overridden (`true`/`false`). |

### Bathroom vs. Toilet Behavior Distinction
- **Toilet / Powder Room (No Shower)**: When occupants leave the room and the area transitions from `occupied` to `extended`, the exhaust fan automatically turns ON for the configured `extended_timeout` duration to exhaust air and odors.
- **Bathroom (With Shower)**: Routine visits (e.g., washing hands, brushing teeth, using the mirror) do **not** engage the fan; it **remains OFF** during the `extended` phase. The exhaust fan turns ON when a shower or moisture change is detected (`humidity_on: true`), and runs continuously through extended and clear states until moisture has evacuated back to baseline.

---

## 💧 Differential Humidity & Shower Evacuation Parameters

Simply Magic Areas uses dual-trigger differential mechanics and thermodynamic rate-of-change analysis to detect showers quickly and ventilate thoroughly:

| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Reference Humidity Sensor**| `reference_humidity_sensor` | *Auto-Resolved* | Baseline humidity entity. If omitted, Simply Magic Areas resolves baseline from the Interior Meta-Area average or historical resting baseline. |
| **Delta Threshold** | `humidity_delta_threshold` | `10.0%` | Humidity percentage above baseline that triggers fan activation, guarded against nocturnal flatlines by requiring non-negative trend and recent occupancy. |
| **Rising Rate Cutoff** | `humidity_up` | `0.03 %/s` (1.8%/min) | Minimum rate of rise required to trigger rapid shower activation within 30–60 seconds. |
| **Delta Shutoff** | `humidity_delta_shutoff` | `3.0%` | Humidity delta above baseline below which the room is considered dry and the fan shuts off. |
| **Falling Rate Cutoff** | `humidity_down` | `-0.015 %/s` | Falling derivative considered active evacuation. |
| **Stationary Deadband** | `humidity_deadband` | `0.001 %/s` | Rate-of-change window considered stationary flatlining. |
| **Flatline Wait Time** | `humidity_wait_s` | `1200s` (20m) | Duration derivative must hover inside the deadband before concluding moisture has normalized. |
| **Maximum Fan Time** | `humidity_max_fan_time` | `3600s` (60m) | Hard safety cutoff with 15-minute lockout. Protects fan motors and stops conditioned air exhaust. |
| **Presence Gate Time** | `humidity_presence_gate_time` | `0s` (disabled) | Maximum seconds since last occupancy allowed to trigger fan, filtering out outdoor weather infiltration. |

### The Drying Slope Sustain Invariant
While moisture extraction is active (`humidity_on: true`), a falling derivative ($dRH/dt < 0$) indicates successful moisture evacuation. **The fan does NOT shut off while drying** as long as `humidity_delta > delta_shutoff`. The fan runs continuously throughout the negative drying slope until the moisture is actually cleared.

---

## 🤖 Adaptive Historical Shower Signature Learning

Sensor placement variations (ceiling vs. vanity vs. wall outlet) and bathroom volumes cause peak rates of rise ($(dRH/dt)_{peak}$) to vary from $0.03$ to $0.35\ \%/\text{s}$ and peak deltas ($\Delta H_{peak}$) to range from $8\%$ to $45\%$.

Simply Magic Areas includes an autonomous **Historical Shower Signature Learner**:

| Setting | Parameter Key | Default | Description |
| :--- | :--- | :--- | :--- |
| **Learn Shower Humidity** | `learn_shower_humidity` | `true` | Enables autonomous historical baseline tracking and shower signature learning. |

### How It Operates
1. **Idle Baseline Tracking ($H_{base}$)**: When the room is idle, ambient background humidity is tracked via Exponential Moving Average ($\alpha = 0.05$), eliminating the need for an external reference sensor.
2. **Empirical Signature Adaptation**: After each shower session concludes, peak rate of rise and peak humidity delta are adapted via EMA ($\alpha = 0.30$):
   $$\Delta H_{peak} \leftarrow 0.70 \cdot \Delta H_{peak} + 0.30 \cdot \Delta H_{peak, event}$$
3. **Dynamic Threshold Scaling**: Thresholds automatically adapt to the room's physical signature:
   $$\Delta H_{trigger} = \max\left(5.0\%, 0.50 \cdot \Delta H_{peak}\right)$$
   $$Trend_{trigger} = \max\left(0.015\ \%/\text{s}, 0.50 \cdot (dRH/dt)_{peak}\right)$$
   $$\Delta H_{shutoff} = \max\left(2.0\%, 0.25 \cdot \Delta H_{trigger}\right)$$
4. **Absolute Ceiling Guard**: If room humidity reaches $\ge 80\%$, the fan turns on unconditionally regardless of rate-of-change, guaranteeing anti-mold protection during extreme condensation.
5. **Exponential Drying Curve Fitting**: Uses closed-form log-linear least squares regression ($\ln(H(t) - H_{base}) = \ln(\Delta H_{peak}) - kt$) during downward drying slopes to project the exact shutoff timestamp ($t_{dry}$), eliminating stationary flatline waiting.

---

## 🖐️ Manual Fan Override (Occupancy-Hold)

When you manually turn a fan on or off (via a physical wall switch, smart button, or Home Assistant UI):
- The fan enters **Manual Override Mode** (`manual_control: true`).
- The integration holds your commanded state and suspends automated humidity adjustments.
- **Occupancy-Hold Release**: As soon as the room undergoes an **occupied state transition** (`occupied/extended -> clear` or `clear -> occupied`), manual override resets automatically and normal automation resumes.
