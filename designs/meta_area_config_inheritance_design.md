# Architecture & Design: Hierarchical Meta-Area Configuration Inheritance

## 1. Executive Summary & Problem Formulation
In modern smart homes powered by Home Assistant and **Simply Magic Areas**, a residence comprises dozens of distinct spatial entities (Living Room, Kitchen, Bedrooms, Bathrooms, Hallways, Patios, Porches). Many automation characteristics, most notably **Sleep Mode** (`sleep_entity`, `sleep_state_dim`, acoustic fan suppression), **Presence Timeouts** (`clear_timeout`, `extended_timeout`), and **Dark Floors** (`dark_lux_floor`), are intrinsically whole-home or zone-level architectural policies rather than room-isolated parameters.

Prior to this design, configuring Sleep Mode required users to redundantly configure identical helper entities across every single individual room dialog in the integration configuration flow. Modifying a household sleep switch required repetitively editing 15+ room configurations. 

This design formulates and implements **Hierarchical Meta-Area Configuration Inheritance**, a zero-config / radical simplicity architecture allowing top-level household settings to be established at the Meta-Area level (`Global`, `Interior`, `Exterior`) and pulled down to all subordinate rooms automatically, while fully supporting per-room local overrides and explicit opt-outs.

---

## 2. Spatial Hierarchy & Resolution Cascade

Simply Magic Areas organizes spatial domains into a 3-tier hierarchy:
```
                      ┌──────────────────────────────────────┐
                      │          Global Meta-Area            │
                      │            (id: "global")            │
                      │   Whole-Home Policies & Defaults     │
                      └──────────────────┬───────────────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 │                                               │
                 ▼                                               ▼
   ┌───────────────────────────┐                   ┌───────────────────────────┐
   │    Interior Meta-Area     │                   │    Exterior Meta-Area     │
   │      (id: "interior")     │                   │      (id: "exterior")     │
   │ Indoor Zone Policies      │                   │ Outdoor Zone Policies     │
   └─────────────┬─────────────┘                   └─────────────┬─────────────┘
                 │                                               │
         ┌───────┴───────┐                               ┌───────┴───────┐
         ▼               ▼                               ▼               ▼
   ┌───────────┐   ┌───────────┐                   ┌───────────┐   ┌───────────┐
   │  Kitchen  │   │  Bedroom  │                   │   Patio   │   │  Driveway │
   └───────────┘   └───────────┘                   └───────────┘   └───────────┘
```

### 2.1 Resolution Cascade Equation
For any configuration key $K \in \mathcal{K}_{\text{inheritable}}$, the effective value evaluated by an area $A$ is given by:

$$\text{EffectiveConfig}(A, K) = \begin{cases}
V_{\text{local}}(A, K) & \text{if } V_{\text{local}}(A, K) \notin \{\text{None}, \text{""}, \text{"none"}, \text{"disabled"}\} \\
\text{None} & \text{if } V_{\text{local}}(A, K) \in \{\text{"none"}, \text{"disabled"}\} \quad (\text{explicit opt-out}) \\
V_{\text{parent}}(A, K) & \text{if } \exists \text{Parent}(A) \land V_{\text{parent}}(A, K) \notin \{\text{None}, \text{""}\} \\
V_{\text{global}}(K) & \text{if } \exists \text{Global} \land V_{\text{global}}(K) \notin \{\text{None}, \text{""}\} \\
V_{\text{default}}(K) & \text{otherwise}
\end{cases}$$

Where:
- For an **Interior Room** ($A \in \text{Interior}$): $\text{Parent}(A) = \text{Interior Meta-Area}$, $\text{Global} = \text{Global Meta-Area}$.
- For an **Exterior Space** ($A \in \text{Exterior}$): $\text{Parent}(A) = \text{Exterior Meta-Area}$, $\text{Global} = \text{Global Meta-Area}$.
- For a **Zone Meta-Area** ($\text{Interior}$ or $\text{Exterior}$): $\text{Parent}(A) = \text{Global Meta-Area}$.
- For the **Global Meta-Area**: Resolves only locally or default.

---

## 3. Inheritable Keys & Configuration Set

The inheritable configuration key set $\mathcal{K}_{\text{inheritable}}$ encompasses shared spatial policies:
1. `sleep_entity`: Central sleep mode binary trigger (`input_boolean.sleep_mode`, `binary_sensor.sleep`). When set on `Global` or `Interior`, all child rooms automatically activate the `sleep` profile upon its turn-on.
2. `sleep_state_dim`: Household default dimming target for lights in sleep mode (e.g., 20% or 30%).
3. `accented_entity`: Ambient scene trigger (e.g. entertaining mode, dinner, movie).
4. `accented_state_dim`: Ambient scene dimming percentage.
5. `clear_timeout`: Post-occupancy clearance countdown duration before extinguishing loads.
6. `extended_timeout`: Extended occupancy countdown duration.
7. `dark_lux_floor`: Absolute photometric threshold protecting naturally dark spaces.
8. `fan_suppress_in_sleep`: Suppress loud ventilation during sleep hours.

---

## 4. UI & Config Flow Integration (Radical Simplicity)

To avoid introducing extraneous configuration dialogs, sub-menus, or multi-step wizards:
1. `META_AREA_SCHEMA` and `OPTIONS_AREA_META` are extended to include the shared entity and value options.
2. When configuring a Meta-Area (`(Meta) Global`, `(Meta) Interior`, or `(Meta) Exterior`), the user is presented with the entity selector for `sleep_entity` directly in the existing Area Configuration form.
3. For individual concrete rooms, the `sleep_entity` field defaults to `""` (empty). An empty field indicates that the room will dynamically pull down the setting from its parent meta-area.
4. If a room requires independent sleep schedules (e.g., Nursery with early bedtimes, or Master Suite with individual bedside controls), entering an entity ID locally provides a priority override without affecting other rooms.

---

## 5. Architectural Implementation Details

### 5.1 `InheritedConfig` Dict Proxy
In `custom_components/simply_magic_areas/base/magic.py`, `self.config` on each `MagicArea` is wrapped with `InheritedConfig(dict)`:
- Intercepts `.get(key, default)` and `__getitem__(key)`.
- If the key belongs to $\mathcal{K}_{\text{inheritable}}$ and local value is empty or unset (`None` or `""`), it delegates to `self.area.get_inherited_value(key)`.
- Supports transparent backward compatibility with all dictionary operations (`isinstance(self.config, dict)` is `True`).

### 5.2 Dynamic Startup & Reload Synchronization
- Resolves meta-area values through both `hass.data[MODULE_DATA]` (for instantiated area objects) and `hass.config_entries.async_entries(DOMAIN)` (for entries awaiting initialization during Home Assistant boot).
- When a Meta-Area's options are updated and reloaded via `async_update_options`, child areas automatically reload to rebind state listeners and refresh lighting/fan configurations without requiring an integration restart.
- Re-entrancy guards prevent circular reload loops between child areas and meta-areas.
