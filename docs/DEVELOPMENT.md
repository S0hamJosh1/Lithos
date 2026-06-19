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
| 4 | Project classifier (`/projects/import` without `profile_id`) | DONE | yes |
| 5 | BLE receiver via `bleak` (+ ble_advertising / gatt_service_present checks) | IMPL* | checks: yes · receiver: HW |
| 6 | STM32 adapter (OpenOCD/CubeProgrammer) | TODO | HW |
| 7 | ESP32 adapter (idf.py / esptool) + WiFi socket verify | TODO | HW |
| 8 | Repair-loop v2 — per-check classify + shared KB + LLM-ready RepairRequest | IMPL* | prompt: yes · fixer: LLM |
| 9 | Break-on-purpose — contract mutation testing (`/contracts/assess`) | DONE | yes |
| 10 | Closed repair loop — best-of-N + don't-game-the-metric (`run_repair_loop`) | IMPL* | orchestration: yes · provider/reverify: LLM/HW |
| 10b | `SettingsFixProvider` — real deterministic config-class fixer (no LLM) | DONE | yes |
| 11 | `NO_TIMEOUT` liveness check wired + assessable | DONE | yes |
| 12 | Contract DSL — terse text → `VerificationContract` (`/contracts/parse`) | DONE | yes |
| 13 | Capture & replay — record a run to JSONL, faithfully replay offline | DONE | yes |
| 14 | Live-path replay + workspace facade + DSL examples (Bucket A) | DONE | yes |


## Known debt
- Pre-existing ruff nits in the scaffold (unused imports in `verify.py` /
  `receivers.py` / `nrf52.py`, an unused `start_ms`, f-string-without-placeholder,
  and the `sys.path` E402s in tests). Left untouched to keep each phase's diff
  scoped and attributable; clean up in a dedicated lint pass.

## Changelog (newest first)

### Phase 14 — Bucket A (live-path replay · workspace facade · DSL examples)
- **Live-path replay:** the FILE receiver now detects a `.jsonl` capture and replays full
  `RuntimeEvent`s (faithful timestamps) through `run_verification`'s live path, not just
  `evaluate_events`. **Bug fixed in passing:** `NO_TIMEOUT` set `pass_seen` per-event, so the
  live loop's "all passed → short-circuit" fired after the first event and a later hang was
  missed — liveness now settles over the whole window (like rate).
- **Workspace facade** (`workspace.py`): one project-lifecycle surface (create / import / detect)
  over the adapter registry + classifier; `api.py` `/projects/*` now routes through it (DRY).
  Fills the `evcide.workspace` box the architecture diagram already named.
- **DSL examples** (`examples/*.evc`) with a parse regression guard so they can't rot.
- 7 tests across the three. 92/92 green, ruff clean.

### Phase 13 — capture & replay (faithful, offline)
- The replay half of the moat without a board: `CaptureSink` attaches to
  `run_verification(..., sink=)` and records the live stream to JSONL as it arrives;
  `load_events()` reads it back into faithful `RuntimeEvent`s. Capture a real run ONCE,
  then feed it into `evaluate_events` / `assess_contract` forever — offline regression +
  break-on-purpose baselines, no board needed again.
- **Faithful by design:** original `timestamp_ms` / `type` / `raw` / `parsed` preserved,
  because `within_ms` / rate / `no_timeout` depend on them. (The existing line-tailing
  `file_receiver_stream` re-stamps wall-clock and is for raw CSV/logs — a test pins that a
  late-boot `within_ms` correctly FAILS on replayed timestamps, which a re-stamp would hide.)
- A captured passing run IS a break-on-purpose baseline — `assess_contract(c, load_events(p))`.
- 4 tests (`test_capture.py`): faithful round-trip, replay→engine+assess, timing survives
  replay, sink records events-only. 85/85 green, ruff clean.

### Phase 12 — contract DSL (terse text → contract)
- The spec's "prompt → measurable expectations" (PDF 22.1), deterministic subset: one
  expectation per line so a human or the frontend authors a contract without writing JSON.
  `@id/@target/@receiver/@timeout` headers; `#` comments. Covers contains/not_contains,
  sequence, count_*, rate (min/max/expected+tol), field present/range/not_frozen/no_nan,
  no_timeout, ble_advertising, gatt_service, socket_reachable.
- **Honest failure:** unparseable input raises `DSLError` with the line number + reason —
  it never silently emits a wrong contract (a contract that means something other than you
  intended is worse than a parse error). API maps it to 400.
- New endpoint `POST /contracts/parse`. The output flows straight into the engine and
  break-on-purpose (end-to-end test parses → evaluates → asserts meaningful). Full NL still
  needs an LLM; this is the unambiguous common-case subset.
- 9 tests (`test_dsl.py` + 1 API). 80/80 green, ruff clean.

### Phase 11 — NO_TIMEOUT liveness check (wired + assessable)
- `NO_TIMEOUT` was declared but had no evaluator, so a liveness expectation silently
  settled `inconclusive` — a "did the board keep running / not hang?" check that never
  actually checked. Now wired: fail if the gap between consecutive events exceeds
  `duration_ms` (silence / hang / watchdog reset); pass on a steady stream. Distinct
  from "did it boot?" — this is "did it stay alive?".
- Added the matching break-on-purpose mutator (`inject_silence_gap`) so a NO_TIMEOUT
  contract is now meaningfulness-testable. `ROS_TOPIC_RATE_HZ` remains the only
  unassessable kind (needs the ROS receiver, still a stub) — still NAMED in the
  mutation report, never silently dropped.
- 2 tests (steady-passes / silence-fails; mutant killed). 71/71 green, ruff clean.

### Phase 10 — closed repair loop (best-of-N + don't-game-the-metric)
- Closes the two remaining traps from the vibe-coding confidence loop, on the repair side:
  - **Keep N attempts alive (best-of-N).** `run_repair_loop` asks the provider for `n`
    candidate fixes and keeps the strongest *passing* one — a single greedy attempt
    dead-ends in local optima.
  - **Never optimize the score (don't-game-the-metric).** A candidate is accepted only
    if it makes the contract PASS *and* the now-passing contract is still **meaningful**
    under break-on-purpose (Phase 9 `assess`). A fix that passes by weakening the oracle
    is rejected with reason `gamed the metric`, even though it's green.
- All three boundaries are injected and may be sync or async: `provider.propose_fix`
  (LLM), `reverify` (apply fix + re-run on hardware), `assess` (re-check meaningfulness).
  The orchestration + selection + anti-gaming logic is deterministic and fully tested;
  the LLM and hardware stay honest stubs. Returns the best `RepairAttempt`, or the first
  rejected one (with its reason) so the caller learns *why* — `IMPL*`, not `DONE`.
- Models: `RepairAttempt`. 3 tests: best-of-N picks highest-confidence passing fix; a
  metric-gaming fix is rejected; all-fail returns the reason. 64/64 green, ruff clean.

### Phase 10b — `SettingsFixProvider` (first real FixProvider)
- The repair loop's providers were all stubs. This is the first **real** one, and it
  needs **no LLM and no hardware**: repair hints already carry framework `target_settings`
  (Zephyr `prj.conf` Kconfig symbols), and those fixes are *mechanical* — enable the symbol.
  The provider turns them into an actual config diff (`difflib.unified_diff`) and can `apply()`
  it (replace an existing `KEY=` line, append otherwise; idempotent — no diff when already set).
- **Honest boundary preserved:** a hint with no `target_settings` (source-level bug) yields a
  `FixProposal(confidence=0.0)` that explicitly defers to an LLM provider — it never fakes a
  source fix. Lazy-senior-dev: the cheapest class of repair shouldn't pay for a model call.
- End-to-end test proves the **whole loop closes with zero LLM and zero hardware**: a
  `no_serial_data` failure → config diff → `run_repair_loop` accepts → `prj.conf` actually fixed.
- 5 tests (4 provider + 1 closed-loop integration). 69/69 green, ruff clean.

### Phase 9 — break-on-purpose (contract mutation testing)
- The confidence-loop keystone applied to verification: *"inject a deliberate bug,
  confirm a test goes red; if nothing fails, the test is theater."* A vibe-coded
  `VerificationContract` can pass vacuously (`contains BOOT_OK` against a boot-looped
  board; a `field_range` with no bounds passes any value). This proves whether a
  contract actually catches the failures it claims to.
- New `mutation.py`: takes a baseline stream that **passes** the contract and mutates
  the *evidence stream* (not firmware — stays hardware-free): drop the token, freeze
  the field, force out-of-range, inject NaN, decimate/densify the rate, drop
  advertisements, strip the service UUID, make the socket unreachable, plus a global
  empty-stream trivial mutant. Each mutation targets one expectation; if that
  expectation still passes, it's a **surviving mutant** = theater. `assess_contract()`
  returns a `MutationReport` (killed / survived / score / `meaningful` / survivors).
- Refactored the engine for a single source of truth: extracted `_apply_event`,
  `_settle_all`, `overall_status`, and a pure offline `evaluate_events()` from
  `run_verification`; the live loop now calls the same code (no behavior change,
  short-circuit preserved). Mutation replay runs through `evaluate_events`.
- New endpoint `POST /contracts/assess` (contract + baseline stream → MutationReport).
- Honesty: unassessable expectation kinds (no evaluator, e.g. `no_timeout`) are NAMED
  in the report, never silently dropped. `caught = targeted check stops passing`
  (red OR inconclusive), so a check that degrades to inconclusive still counts.
- 7 tests (`test_mutation.py` + 1 API): meaningful contract kills every mutant,
  unbounded `field_range` flagged as theater, empty contract = pure theater,
  non-passing baseline rejected, rate decimation killed, unassessable-kind noted.
  61/61 green, ruff clean.

### Phase 8 — repair loop v2 (*fixer is an injected LLM boundary)
- New `repair.py`: the classifier→targeted-prompt half of the build→verify→repair
  loop, built so it is the **shared moat layer** rather than per-adapter boilerplate.
  - `classify_check()` generalizes the single whole-result verdict into one cause
    *per failing expectation* (boot/token/rate/field/socket/ble/...).
  - `REPAIR_KB` is one data-driven table keyed by classification; `FRAMEWORK_SETTINGS`
    is a thin per-framework overlay (Zephyr `prj.conf` keys). `build_repair_hints()`
    severity-orders and de-dupes; a whole-stream no-data cause short-circuits.
  - `build_repair_request()` assembles a deterministic, **LLM-ready `RepairRequest`**
    (failed checks + evidence digest + likely fixes + a rendered fix prompt) with
    **zero model calls**, so the whole thing is testable on this box.
- The model call is the only non-deterministic seam: `FixProvider` Protocol +
  `NullFixProvider` (refuses honestly rather than faking a patch) + `attempt_repair()`.
- **Dedup / free-for-new-adapters:** `nrf52.repair_hints` was a ~48-line hint ladder;
  it now delegates to the shared layer (`framework="zephyr"`, preserving its v1
  `prj.conf` settings). The stm32/esp32/rp2040 stubs returned `[]`; they now get the
  full generic hint set for free. The engine attaches `result.repair_request` on any
  non-pass run, with or without an adapter.
- **Honest status:** the deterministic prompt builder is done + tested; the LLM
  *fixer* that consumes the prompt is a stub seam, hence `IMPL*` not `DONE`.
- 14 tests (`test_repair.py`): per-check classification, KB hints + ordering + dedup,
  framework overlay, prompt contents, the no-fixer-wired honesty assert, and an
  end-to-end `run_verification`→`repair_request` attach. 54/54 green, ruff clean.

### Test hardening — API endpoint coverage
- Added `test_api_endpoints.py`: `/boards`, `/projects/create` (writes real files
  to a tmp dir), `/build` and `/flash` graceful no-`west` paths (skipped if `west`
  is installed), `/flash` 404-when-board-absent, `/wal/tail`. 6 tests. 40/40 green.

### Phase 5 — BLE receiver (*receiver HW-untested)
- Replaced the BLE stub with a real `bleak` implementation: scans for
  advertisements (filterable by name/address/service UUID), and — when a
  `characteristic_uuid` is given — connects and streams GATT notifications.
  Emits advertisement/connect/packet/unreachable events.
- **The bleak code path has NOT been run against a real radio** (no XIAO on this
  box, bleak not installed). It is marked HW-untested in the docstring. What *is*
  tested: the event SHAPES via the two new verify-side checks below, plus a clean
  `RuntimeError` when bleak is absent.
- New `ble_advertising` and `gatt_service_present` checks wired into the engine
  and tested with synthetic BLE events. 5 new tests. 34/34 green.

### Phase 4 — project classifier
- New `classify.py`: infers board profile + toolchain from on-disk markers
  (PlatformIO `board=`, Zephyr `prj.conf` + CMake `set(BOARD ...)`, ESP-IDF
  `sdkconfig`, Pico SDK, Arduino sketch). Evidence-based, ordered strongest-first,
  returns None rather than guessing when undeterminable.
- `/projects/import` now classifies when `profile_id` is omitted (was a hard 400),
  returns `{metadata, classification}`, and maps an unimplemented adapter's
  `NotImplementedError` to a 501 instead of a 500.
- 11 tests (9 classifier unit + 2 API). 29/29 green.

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
