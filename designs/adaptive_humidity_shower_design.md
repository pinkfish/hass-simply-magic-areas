# Engineering Design Document: Adaptive Humidity Dynamics & Historical Shower Learning

## 1. Executive Summary

In residential smart homes, automatic bathroom ventilation must reliably identify shower events without requiring tedious manual calibration of rates-of-rise and delta thresholds. Real-world bathrooms vary drastically in:
1. **Sensor Placement & Physical Proximity**: A sensor inside the shower stall experiences $dRH/dt \approx +0.20\%/\text{s}$ and $\Delta RH \approx +40\%$, whereas a sensor by the entry door or in a large room may only experience $dRH/dt \approx +0.015\%/\text{s}$ and $\Delta RH \approx +7\%$.
2. **Seasonal & Diurnal Baseline Drift**: Indoor ambient humidity naturally ranges from $25\% - 35\%$ in winter heating conditions to $65\% - 75\%$ in humid summers.
3. **Sensor Hardware Skew & Polling Latency**: Battery-operated Zigbee/Z-Wave/BLE sensors report on discrete threshold increments (e.g. $\ge 1\%$ step or 60s heartbeats), while mains-powered sensors report continuous floating-point measurements.

This design document establishes the architecture for **Autonomous Historical Humidity Learning** in **Simply Magic Areas**, removing reliance on hardcoded boundaries. The system:
- Autonomously tracks the resting ambient baseline ($H_{base}$) during non-shower periods.
- Uses $H_{base}$ as an internal reference for standalone bathrooms lacking an external reference sensor.
- Automatically observes and logs shower signatures (peak rise rate and peak humidity delta).
- Dynamically adapts trigger thresholds ($\Delta H_{trigger}$ and $Trend_{trigger}$) and recovery shutoff targets ($\Delta H_{shutoff}$) tailored to each specific room.
- Provides hard floor noise-rejection guarantees and absolute high-limit failsafes.

---

## 2. In-Situ Humidity Dynamics & Modeling

### 2.1 The Environmental State Variables

Let:
- $H(t) \in [0, 100]$ be the instantaneous relative humidity reported by the room's humidity sensor at time $t$ (seconds).
- $T(t) = \frac{dH}{dt}$ be the numerical first derivative (rate of change in $\%/\text{s}$, reported by `sensor.simply_magic_areas_humidity_statistics_<area>`).
- $H_{ref}(t)$ be the external reference humidity (from a configured reference sensor, parent interior meta-area, or climate entity).
- $H_{base}(t)$ be the autonomously learned internal resting baseline humidity of the room.

```
       Relative
       Humidity (%)
          ^
      90% |                     Shower Plateau (Equilibrium)
          |                    /-------------------------\
          |                   /                           \
          |     Rapid Rise   /                             \   Drying Phase
          |     dRH/dt >> 0 /                               \  dRH/dt < 0
          |                /                                 \ (Fan MUST stay ON)
          |               /                                   \
      50% |--------------/                                     \--------
          |   Idle Baseline                                     \  Shutoff Target
          |   (H_base)                                           v (H_base + Delta_shutoff)
          +-----------------------------------------------------------------> Time
              Phase 1        Phase 2        Phase 3             Phase 4      Phase 5
              Idle           Shower Start   In-Progress         Shower Off   Dry
```

---

## 3. Autonomous Historical Learning Architecture

### 3.1 Resting Ambient Baseline Tracking ($H_{base}$)

When the area is idle (fan not forced by humidity: `humidity_on == False`, and room not in an active high-humidity state), ambient humidity fluctuations reflect slow atmospheric and HVAC changes.

The baseline is updated via an exponential moving average (EMA):
$$H_{base}^{(t+1)} = (1 - \alpha_{base}) H_{base}^{(t)} + \alpha_{base} H_{sample}$$
where $\alpha_{base} = 0.05$.

#### Standalone Bathroom Baseline Delta
If no external reference sensor ($H_{ref}$) is configured:
$$\Delta H(t) = H(t) - H_{base}(t)$$
This enables differential humidity calculations in **any** room, eliminating the previous requirement for an external reference sensor.

---

### 3.2 Shower Event Signature Extraction

During a shower event (from the moment `humidity_on` triggers until the fan completes ventilation and returns to baseline):
1. **Peak Rate-of-Rise**: The controller continuously tracks the maximum positive rate of change:
   $$(dRH/dt)_{peak} = \max_{t \in [t_{start}, t_{end}]} \left(\frac{dH}{dt}(t)\right)$$
2. **Peak Humidity Delta**: The controller tracks the maximum delta above the pre-shower baseline:
   $$\Delta H_{peak} = \max_{t \in [t_{start}, t_{end}]} \left(H(t) - H_{base}(t_{start})\right)$$

When the room returns to baseline (or reaches shutoff threshold):
- If the event satisfies minimum shower validity criteria ($\Delta H_{peak} \ge 4.0\%$ and runtime between 60s and 3600s), it is recorded as a valid shower event.
- Historical metrics are updated via exponential smoothing:
  $$\text{shower\_delta\_peak}^{(k+1)} = (1 - \alpha_{shower}) \cdot \text{shower\_delta\_peak}^{(k)} + \alpha_{shower} \cdot \Delta H_{peak}$$
  $$\text{shower\_rate\_peak}^{(k+1)} = (1 - \alpha_{shower}) \cdot \text{shower\_rate\_peak}^{(k)} + \alpha_{shower} \cdot (dRH/dt)_{peak}$$
  where $\alpha_{shower} = 0.25$.
- `historical_shower_count` is incremented.

---

### 3.3 Dynamic Threshold Derivation

Instead of static magic numbers, the room's trigger parameters adaptively scale to the room's physical sensor characteristics:

| Parameter | Formula | Floor (Min) | Ceiling (Max) | Initial Default |
| :--- | :--- | :--- | :--- | :--- |
| **Trigger Rate ($Trend_{trigger}$)** | $0.40 \times \text{shower\_rate\_peak}$ | $0.010 \%/\text{s}$ | $0.040 \%/\text{s}$ | $0.020 \%/\text{s}$ |
| **Trigger Delta ($\Delta H_{trigger}$)** | $0.40 \times \text{shower\_delta\_peak}$ | $4.0\%$ | $12.0\%$ | $8.0\%$ |
| **Shutoff Delta ($\Delta H_{shutoff}$)** | $0.25 \times \Delta H_{trigger}$ | $1.5\%$ | $4.0\%$ | $3.0\%$ |

#### Interpretation:
- In a small bathroom with a sensor directly above the shower stall ($\Delta H_{peak} \approx 35\%$, $Rate_{peak} \approx 0.15\%/\text{s}$):
  - $\Delta H_{trigger} \rightarrow 12.0\%$ (prevents false triggering from opening the door after handwashing).
  - $Trend_{trigger} \rightarrow 0.040\%/\text{s}$.
  - $\Delta H_{shutoff} \rightarrow 3.0\%$.
- In a large master bathroom with a sensor 10 feet away ($\Delta H_{peak} \approx 8\%$, $Rate_{peak} \approx 0.025\%/\text{s}$):
  - $\Delta H_{trigger} \rightarrow 4.0\%$ (sensitive enough to reliably engage).
  - $Trend_{trigger} \rightarrow 0.010\%/\text{s}$.
  - $\Delta H_{shutoff} \rightarrow 1.5\%$ (shuts off cleanly when dried).

---

### 3.4 Hard Failsafes & Safety Invariants

1. **Noise Floor Protection (Zero False Triggering)**:
   Activation strictly requires $Trend \ge 0.008\%/\text{s}$ AND $\Delta H \ge 3.0\%$.
2. **Absolute Ceiling Guard (Warm Baths & Soaks)**:
   If $H(t) \ge 80.0\%$ OR $\Delta H(t) \ge 15.0\%$, activation triggers immediately regardless of rate-of-rise.
3. **Negative Slope Invariant (Zero Premature Shutoff)**:
   While `humidity_on == True`, negative slopes ($dRH/dt < 0$) are expected and must NOT cause fan deactivation until $\Delta H \le \Delta H_{shutoff}$ (or deadband plateau).
4. **Safety Maximum Runtime (`max_fan_time`)**:
   A hard timer cap (default: 60 minutes) guarantees motors and conditioned air are protected even under hardware failure.

---

## 4. State Attributes & Persistence

State attributes exposed on `AreaFanGroup` (`fan.simply_magic_areas_fan_<area>`):
- `historical_humidity_baseline`: Current learned ambient resting baseline $H_{base}$ (% RH).
- `historical_shower_count`: Number of observed shower events.
- `historical_shower_delta_peak`: Learned rolling shower peak delta (% RH).
- `historical_shower_rate_peak`: Learned rolling shower peak rate of rise (%/s).
- `learned_humidity_delta_threshold`: Active dynamic delta trigger (% RH).
- `learned_humidity_trend_up_cutoff`: Active dynamic rate-of-rise trigger (%/s).
- `learned_humidity_delta_shutoff`: Active dynamic shutoff delta target (% RH).

All historical metrics persist across Home Assistant reboots and integration reloads via state restoration.
