# AGENTS.md — Agent Guidelines for Simply Magic Areas

This file defines mandatory instructions for AI coding agents working in the `hass-magic_areas` (`simply_magic_areas`) repository. All work must adhere to [constitution.md](constitution.md) and [SYSTEM_SPEC.md](SYSTEM_SPEC.md).

---

## 1. Mandatory Specification & Documentation Synchronization (Zero-Drift Rule)

**Every code change MUST be accompanied by a synchronous update to the specification and documentation so that [SYSTEM_SPEC.md](SYSTEM_SPEC.md) always stays 100% in sync with the codebase.**

Before considering any task, feature, bugfix, or refactor complete, you **must** update:
1. **[SYSTEM_SPEC.md](SYSTEM_SPEC.md)**: Update the technical specification to cover any new or modified behavior, occupancy/state rules, lighting profiles, configuration options, default values, state attributes, entity platforms, or heuristics.
2. **User & Feature Documentation**: Update [README.md](README.md), [info.md](info.md), and the relevant guides in [docs/](docs/) (e.g., [docs/lighting_and_presence.md](docs/lighting_and_presence.md), [docs/climate_and_ventilation.md](docs/climate_and_ventilation.md), [docs/advanced_features.md](docs/advanced_features.md)) and [designs/](designs/) whenever user-facing behavior or options change.
3. **[tasks.md](tasks.md)**: Record completed work and roadmap updates in [tasks.md](tasks.md).

> **Invariant**: Code, [SYSTEM_SPEC.md](SYSTEM_SPEC.md), and documentation must never drift out of sync. Never commit or finalize code changes without updating [SYSTEM_SPEC.md](SYSTEM_SPEC.md) to reflect them.

---

## 2. Core Engineering & Home Assistant Invariants

1. **Convention Over Configuration ("It Just Works")**:
   - New features should work out of the box with sensible defaults (`default: True` where appropriate) and zero mandatory setup.
   - Keep basic area configuration simple; place numerical thresholds and fine-tuning overrides inside Advanced Settings (`advanced_light_groups` or `humidity`).
2. **DRY & Centralized Domain Logic**:
   - Place shared area/meta-area queries in [`MagicArea`](custom_components/simply_magic_areas/base/magic.py) / [`MagicMetaArea`](custom_components/simply_magic_areas/base/magic.py) and constants in [`const.py`](custom_components/simply_magic_areas/const.py).
3. **Non-Blocking Async & Timer Hygiene**:
   - Never call blocking `hass.services.call(...)` on the event loop; always use `call_service_non_blocking` (`hass.async_create_task` / `hass.create_task`).
   - Always cancel any `async_call_later` or interval timers in `async_will_remove_from_hass` / `async_on_remove` so tests never fail with lingering timers.
   - Ignore attribute-only state updates on `sensor.simply_magic_areas_state_*` when listening for occupancy state transitions (`old_state.state == new_state.state`).

---

## 3. Verification & Quality Gates

Run both commands before completing any change or creating a commit:

```bash
# 1. Linter check (must report 0 errors/warnings)
./.venv/bin/ruff check .

# 2. Full test suite (must pass 100% of tests, including new unit tests for your changes)
./.venv/bin/pytest -n auto -q
```

