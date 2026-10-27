# Project Constitution: Simply Magic Areas

This document establishes the governing development standards, architectural invariants, and engineering principles for the `simply_magic_areas` Home Assistant integration.

---

## 1. Core Technology: Python for Home Assistant
- **Exclusively Python**: All integration code, test suites, and automation scripts must be written in idiomatic, modern Python (3.13+) adhering to Home Assistant integration architecture and development guidelines.
- **Home Assistant Standards**: Leverage Home Assistant core entities, registries, helpers, and conventions. Never bypass or re-implement framework capabilities when native primitives exist.
- **Strict Typing**: Code must be typed and pass linter/type checks cleanly (`ruff` and static analysis).

---

## 2. DRY (Don't Repeat Yourself) Principles
- **Centralized Logic**: Business logic, entity lookups, and multi-tier resolution routines (e.g., illuminance sensor hierarchies, occupancy state evaluation, active area resolution) must reside in core domain objects (such as `MagicArea` or `util.py`), not duplicated across platform implementations.
- **Unified Constants & Configurations**: Maintain single sources of truth for configuration keys, default values, entity names, and state enums. Never hardcode strings or duplicated mappings in individual platform files.
- **Shared Helpers**: Actuator platforms (light, fan, climate, cover, media player) must reuse shared helper utilities for service dispatches, state updates, and attribute calculations.

---

## 3. Radical Simplicity
- **Keep Code Simple (KISS)**: Write the simplest, most readable solution that fulfills the specification. Avoid over-engineering, unnecessary inheritance hierarchies, and premature abstractions.
- **No Dead Code**: Remove unused imports, dead methods, unreferenced constants, and obsolete legacy code immediately.
- **Maintainable Control Flows**: Keep branching straightforward and functions focused on a single responsibility with clear preconditions and guard clauses.

---

## 4. Rigorous Asynchronous & Concurrency Handling
- **Non-Blocking Service Dispatch**:
  - Never call synchronous, blocking `self.hass.services.call(...)` inside the asyncio event loop.
  - Dispatch service calls asynchronously using non-blocking patterns (e.g. `call_service_non_blocking` / `hass.services.async_call`) to prevent loop freezes, latency spikes, and deadlocks.
- **Thread-Safety & Event Loop Boundaries**:
  - Distinguish between event loop execution and worker thread execution.
  - When callbacks or operations may originate from worker threads (such as executor tasks or background state changes), safely marshal work to the event loop using `hass.loop.call_soon_threadsafe(...)` or `hass.create_task(...)`.
  - Never invoke event-loop-only methods (`async_write_ha_state`, `hass.async_create_task`) directly from outside the event loop thread.
- **Deterministic State Flushing**:
  - Schedule entity state flushes safely via `schedule_update_ha_state()` or event-loop validated writes to maintain state machine consistency.
- **Timer & Timeout Management**:
  - Always clean up and cancel pending timeouts (`async_call_later` callbacks) upon conflicting state transitions or entity unloads to prevent lingering timers, race conditions, and memory leaks.

---

## 5. Testing & Quality Gate
- **100% Test Stability**: All changes must be backed by automated unit and integration tests under `custom_components/simply_magic_areas/tests/`.
- **Zero Linter Warnings**: Every commit must pass `ruff check` with zero errors or warnings.

---

## 6. Continuous Documentation & Specification Invariant
- **Always Update the Spec & Documentation**: Any change to features, configurations, entities, state models, default values, or integration behavior must immediately be reflected across all documentation:
  - Technical specification in [SYSTEM_SPEC.md](SYSTEM_SPEC.md)
  - User documentation in [README.md](README.md) and [info.md](info.md)
  - Algorithmic design blueprints in [designs/](designs/)
  Code, specification, and documentation must never drift out of sync.
- **Zero-Drift Policy**: No feature, refactor, or configuration change is considered complete without accompanying synchronous documentation updates.
- **Synchronous Task Tracking**: Tasks, roadmaps, and progress must be maintained synchronously in [tasks.md](tasks.md).


