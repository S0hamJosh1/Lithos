# Lithos / evcide — Development Guidelines & Roadmap

> Working doc for the autonomous build-out of the EVC-IDE backend. Source-of-truth
> spec is Soham's `Embedded_Vibe_Coding_IDE_Master_Documentation_v2.pdf` (v2.0).

## Coding guidelines (the rules this build follows)

1. **Moat-first.** Every increment must strengthen *runtime verification* or its
   delivery to the frontend (Void IDE). Flashing is a solved commodity; "did it
   actually work on the hardware?" is the product. Build there first.
2. **Hardware-independent and tested.** This build runs on a dev box with **no
   XIAO board and no `west`**. Therefore every increment must be verifiable here:
   synthetic event streams, loopback TCP sockets, FastAPI `TestClient`. No
   "trust me, it works on hardware" code in the layers that *can* be tested
   without hardware (verify engine, event bus, API, socket receiver).
3. **Honest stubs only.** A not-yet-implemented path raises `NotImplementedError`
   with a clear, actionable message. It never fakes a PASS. A verification engine
   that lies is worse than useless. (Existing repo rule: no dead-deps theater.)
4. **Match the house style.** pydantic v2 models; `from __future__ import
   annotations`; full type hints; section headers `# ====== Name ======`; async
   throughout; ruff line-length 100.
5. **No regressions.** The full `pytest` suite is green after every increment.
6. **Structure does the work.** Verification quality is enforced by measurement
   against real output, not by policy/lint. Keep the contract → measurable check
   pipeline the center of gravity.
7. **Lazy senior dev.** stdlib before a dependency (`asyncio`, `socket`). Smallest
   change that fully closes the gap. No speculative generality.

## Status legend
`DONE` shipped + tested · `WIP` in progress · `TODO` planned · `HW` needs real hardware to fully validate

## Roadmap

| Phase | Item | Status | Testable here? |
|---|---|---|---|
| 0 | Fix test-harness binding bug → green baseline | DONE | yes |
| 1 | Wire timing windows (`within_ms` / `after_ms`) into checks | DONE | yes |
| 2 | Live event bus + real WebSocket streaming to frontend | DONE | yes (TestClient) |
| 3 | Socket receiver (real, loopback-testable) + `socket_reachable` | DONE | yes (loopback) |
| 4 | Project classifier (`/projects/import` without `profile_id`) | TODO | yes |
| 5 | BLE receiver via `bleak` | TODO | HW |
| 6 | STM32 adapter (OpenOCD/CubeProgrammer) | TODO | HW |
| 7 | ESP32 adapter (idf.py / esptool) + WiFi socket verify | TODO | HW |
| 8 | Repair-loop v2 (classifier → targeted LLM fix prompts) | TODO | partial |

## Known debt
- Pre-existing ruff nits in the scaffold (unused imports in `verify.py` /
  `receivers.py` / `nrf52.py`, an unused `start_ms`, f-string-without-placeholder,
  and the `sys.path` E402s in tests). Left untouched to keep each phase's diff
  scoped and attributable; clean up in a dedicated lint pass.

## Changelog (newest first)

### Phase 3 — socket receiver + socket_reachable
- Replaced the `socket_receiver_stream` stub with a real asyncio TCP line
  receiver (for WiFi/Ethernet boards e.g. ESP32). Emits a `connect` event on
  success (so a reachable-but-silent endpoint still proves reachability), one
  `line` event per newline frame, and a single `unreachable` event on failure.
- The connect is bounded by `_SOCKET_CONNECT_TIMEOUT_S` so a **blackholed host**
  (board not on the network — connect would hang forever) surfaces as
  `unreachable` instead of looking like a silent no-data timeout.
- New `socket_reachable` check wired into the engine.
- 2 loopback tests (real ephemeral-port asyncio server, no hardware, no receiver
  monkeypatch). Note: connection-refused latency is ~2s on Windows, so the
  unreachable test budgets 6s. 18/18 green.

### Phase 2 — live event bus + WebSocket streaming
- New `eventbus.py`: stdlib-asyncio pub/sub keyed by a client correlation id,
  with a bounded replay buffer so a subscriber that connects just after /verify
  starts still gets the early events (the frontend opens the WS and POSTs /verify
  as two separate calls — we must not depend on their ordering).
- `run_verification` gained an optional async `sink`; it publishes each event as
  `{"type":"event"}` and the final result as `{"type":"result"}`. The HTTP
  response still returns the result, so the WS is purely additive.
- `/verify` accepts an optional `stream_id`; `/streams/{id}` now subscribes to the
  bus instead of polling the WAL.
- **Two pre-existing bugs fixed in passing** (both were invisible because the
  scaffold had no API tests): (1) `adapters/__init__.py` never re-exported
  `get_adapter_for_profile`, so `import evcide.api` raised ImportError; (2)
  `api.py` had `from __future__ import annotations`, which stringized the
  request-body annotations — and since the body models are defined *inside*
  `create_app()`, FastAPI couldn't resolve them and treated every POST body as a
  query param (all POST endpoints returned 422). Removed the future-import.
- 7 new tests (5 bus unit + 2 full `/verify`→WS via `TestClient`). 16/16 green.

### Phase 1 — timing windows
- `within_ms` / `after_ms` were declared on `Expectation` but never read, so the
  demo contract's `contains BOOT_OK within_ms:3000` accepted a boot at any time
  inside the 12s timeout. A slow-booting board passed — a real moat hole.
- Timing is measured relative to the **first event on the stream** (≈ boot for a
  freshly-opened receiver), which is consistent for synthetic streams and real
  hardware. `after_ms` skips warm-up events before the window opens; `within_ms`
  downgrades a check that only passed late — or never — to FAIL (a required
  behavior that misses its deadline is a failure, not "inconclusive"). Rate
  checks are exempt. 4 new tests.

### Phase 0 — green baseline
- **Bug:** `verify.py` imported `get_receiver_stream` by name at module load, so
  `monkeypatch.setattr(receivers, ...)` in the tests had no effect — the engine
  used the real serial receiver against `/dev/null`, yielding zero events. 4/5
  tests failed on a clean checkout.
- **Fix:** import the module (`from . import receivers`) and call
  `receivers.get_receiver_stream(...)` at use-time so monkeypatch (and any future
  swap) actually rebinds. One-line-class fix; no behavior change on real hardware.
