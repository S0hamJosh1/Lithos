# Lithos / evcide — Session Handoff

_Last updated: 2026-06-18. Backend for Soham's Embedded Vibe-Coding IDE (Void IDE is the frontend)._

> **⚠ NEXT SESSION — TOP PRIORITY:** Phases 8/9/10 shipped — repair-loop v2 (prompt) + break-on-purpose
> (mutation testing) + closed loop (`run_repair_loop`: best-of-N + don't-game-the-metric). The repair loop
> is now structurally complete; the ONLY remaining boundaries are the injected ones:
> **wire a real `FixProvider`** (LLM consumes `RepairRequest.prompt` → `FixProposal` diff) and a real
> `reverify`/`assess` (apply diff + re-run on hardware). All three seams are honest stubs today.
> Alt non-LLM increments: real STM32/ESP32 adapter (HW) or the ROS receiver.
> **Still HELD for Will:** messaging Soham, any hardware-bound validation.

> **Origin note:** Phase 9 (break-on-purpose) was derived directly from Will's
> `Desktop/vibe-coding-confidence-loop.md` — its keystone ("inject a bug, confirm a test goes red")
> is exactly the un-gameable-oracle move. Cross-substrate tie to the private value-chain work.

> **PR note (2026-06-18):** the prior handoff said "open the PR." Investigated — there is no PR to
> open: `feat/python-backend-scaffold` **is** the upstream default branch and the only branch, and
> the repo is not a fork. Our commits are already live on the default branch. No PR fabricated.

## Current state
- Repo: `github.com/S0hamJosh1/Lithos` (Soham's). Working branch = default branch = `feat/python-backend-scaffold`.
- **Pushed through the `/contracts/minimality` endpoint**. **Test suite: 96 passing** (was 40). Python 3.12, `pytest`, `ruff` clean. Commits are **atomic** (one logical change each) per Will 2026-06-18.
- `assess_minimality` (`mutation.py`) — leave-one-out: flags any expectation that catches no break uniquely (redundant / coverage-gap); the dual of break-on-purpose. The literal pairwise mutation was near-vacuous (monotone) so it was NOT built. **`/contracts/minimality` endpoint now shipped** (mirrors `/contracts/assess`: contract + passing baseline → `MinimalityReport`; WAL-logged). Lib + API + 2-path test all live.
- Shipped this session: repair v2 (prompt) · v3 break-on-purpose (`mutation.py`, `/contracts/assess`) · v4 closed loop (`run_repair_loop`, best-of-N + don't-game-the-metric) · `SettingsFixProvider` (first real fixer, config-class, no LLM) · `NO_TIMEOUT` liveness (+ short-circuit fix) · contract DSL (`dsl.py`, `/contracts/parse`) · offline demo (`scripts/demo_loop.py`) · capture/replay (`capture.py` + `.jsonl` live-path replay) · workspace facade (`workspace.py`) · DSL examples (`examples/*.evc`) · `docs/GAMEPLAN.md` · README rewrite.
- **Solo/hardware-independent surface is EXHAUSTED** (the M2 gate per `docs/GAMEPLAN.md`). Remaining = externally blocked: source-level `FixProvider` (needs an LLM endpoint; seam+prompt ready) · real STM32/ESP32/RP2040 adapters + ROS receiver + on-board re-verify (need boards) · Void IDE frontend (Soham). **Combinatorial mutation DECLINED — not ceremony-built.** Both the GAMEPLAN note and `assess_minimality`'s own docstring establish the contract model is monotone: a combined mutation is caught whenever each single mutation is, so pairs/combos add no kills that singles don't. Building it = dead code dressed as a feature. Skipped on the same grounds the pairwise mutation was. With minimality shipped, the solo hardware-independent surface is now genuinely **exhausted** — every remaining item needs a board, an LLM endpoint, or Soham. Soham still NOT messaged (Will decided he'll just see git history).
- **Gameplan:** `docs/GAMEPLAN.md` — buckets A–E + critical path; the bottleneck to "everything done" is Soham engagement, not code.
- Run: `python -m pytest -q` · `python -m ruff check src tests scripts` · `python scripts/demo_xiao.py detect`.

## This session (2026-06-18) — Phase 8 repair loop v2
- New `src/evcide/repair.py`: per-check `classify_check`, data-driven `REPAIR_KB` + `FRAMEWORK_SETTINGS`
  overlay, `build_repair_hints` (severity-ordered, deduped, no-data short-circuit), `build_repair_request`
  (deterministic LLM-ready prompt, zero model calls), `FixProvider`/`NullFixProvider`/`attempt_repair`.
- Models: `RepairRequest` + `FixProposal`; `VerificationResult.repair_request`.
- Rewired `nrf52.repair_hints` (~48-line ladder → 3-line delegate, preserves Zephyr settings); stub
  adapters now get hints for free; engine attaches `repair_request` on any non-pass run.
- `test_repair.py`: 14 tests. Commits `e373fa3` (feat) + `8ca8eda` (test+docs).

## What was done this session
1. **Fixed a red baseline** — `verify.py` imported `get_receiver_stream` by name, defeating test monkeypatching. Now `from . import receivers`; resolve at call time.
2. **Timing windows** — `within_ms`/`after_ms` were declared but ignored (slow boot passed). Now enforced relative to first event on the stream.
3. **Live event bus + WebSocket** — `eventbus.py` (stdlib asyncio pub/sub + replay buffer). `run_verification(..., sink=)` publishes events + result. `/verify` takes `stream_id`; `/streams/{id}` subscribes.
4. **Socket receiver** — real asyncio TCP line receiver + `socket_reachable` check; bounded connect so a blackholed host yields `unreachable`.
5. **Project classifier** — `classify.py` infers profile/toolchain from on-disk markers. `/projects/import` works without `profile_id`; maps `NotImplementedError` → 501.
6. **BLE receiver** — real `bleak` implementation + `ble_advertising`/`gatt_service_present` checks. **Receiver is HARDWARE-UNTESTED** (no radio/bleak here); checks tested with synthetic events.
7. **Lint pass** + **API endpoint test coverage** (boards/create/build/flash/wal).

## 3 pre-existing bugs fixed in passing (all hidden by no API tests)
- `adapters/__init__` never re-exported `get_adapter_for_profile` → `import evcide.api` raised ImportError.
- `api.py` `from __future__ import annotations` + body models defined inside `create_app()` → FastAPI treated every POST body as a query param (all POSTs 422). Removed the future-import.
- (the verify monkeypatch binding bug above.)

## Key decisions & why
- **Moat-first, hardware-independent, tested-here.** No XIAO/`west`/`bleak` on this box, so every increment is verifiable via synthetic streams, loopback sockets, or `TestClient`. Hardware-bound code (BLE receiver, west build/flash) is implemented but clearly marked untested.
- **Honest stubs** — unimplemented paths raise `NotImplementedError`/return graceful failures; never fake a PASS.
- **Scoped commits** — left pre-existing lint debt documented rather than churning unrelated files; one logical change per commit.
- **Timing relative to first event** — consistent for synthetic streams and a freshly-opened receiver (≈ boot).

## Open threads / next steps
- BLE `bleak` path + `west` build/flash need **validation on a real XIAO nRF52840 Sense**.
- STM32 / ESP32 / RP2040 adapters are still honest stubs (need the boards).
- ROS receiver still a stub.
- Repair-loop v2 (classifier → targeted LLM fix prompts) not started.
- No PR opened; Soham not messaged (held for Will).

## Map
- Guidelines + roadmap + full changelog: `docs/DEVELOPMENT.md`.
- Engine: `src/evcide/verify.py` · receivers: `src/evcide/receivers.py` · bus: `src/evcide/eventbus.py` · classifier: `src/evcide/classify.py` · API: `src/evcide/api.py` · adapters: `src/evcide/adapters/`.
- Tests: `tests/` (test_verify, test_eventbus, test_api_stream, test_socket_receiver, test_classify, test_ble, test_api_endpoints).
