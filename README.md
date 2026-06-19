# Embedded Vibe-Coding IDE — Backend

JARVIS-substrate backend for the Embedded Vibe-Coding IDE. Closes the embedded firmware loop: edit -> build -> flash -> observe -> verify -> diagnose -> repair -> retest.

This is the embedded runtime layer. The frontend is Void IDE.

> **Status:** vertical slice for Nordic nRF52 (Seeed XIAO nRF52840 Sense). Other Tier 1 boards (STM32, ESP32) scaffolded as adapters but not implemented yet.

> **Source-of-truth spec:** `Embedded_Vibe_Coding_IDE_Master_Documentation_v2.pdf` v2.0 (Soham, 2026-06-09).

---

## What this is, what this is not

| this IS | this is NOT |
|---|---|
| Autonomous embedded engineering workflow | A text editor (Void IDE is the cockpit) |
| Build / flash / observe / verify / repair loop | A faster Arduino IDE |
| Runtime verification against real hardware | A wrapper around `west flash` |
| Board-adapter substrate for STM32/ESP32/RP2040/nRF | A cloud build service |
| Local-first hardware operations | Multi-board distributed flashing |

The moat is **runtime verification**, not flashing. Existing tools (Arduino IDE, PlatformIO, STM32CubeIDE, ESP-IDF, OpenOCD) already answer "did it build?" None answer "did it actually work on the hardware?" — that's the product.

---

## Quick start (XIAO nRF52840 Sense demo path)

Prerequisites: Python 3.12, [nRF Connect SDK with `west`](https://docs.nordicsemi.com/bundle/ncs-latest/page/nrf/installation.html), a XIAO nRF52840 Sense connected over USB.

```bash
cd embedded-vibe-coding
pip install -e .[dev]

# detect connected boards
python scripts/demo_xiao.py detect

# run the end-to-end loop on a sample IMU streaming project
python scripts/demo_xiao.py run --project ./samples/xiao_imu_ble_demo \
                                --contract ./examples/xiao_imu_ble_stream.json
```

The demo runs the agent loop end-to-end: detect XIAO -> `west build` -> `west flash` -> open BLE receiver -> evaluate verification contract -> report PASS/FAIL with evidence and repair hints.

---

## Architecture (mapped from PDF Section 10-12)

```
Void IDE Frontend (separate repo)
  -> HTTP / WebSocket
    -> Embedded Agent API  (evcide.api)
      -> AI Agent Orchestrator     (planned; LLM provider abstraction)
        -> Workspace Manager        (evcide.workspace)
        -> Board Adapter Layer      (evcide.adapters)
          -> Build Runner            (evcide.runners.BuildRunner)
          -> Flash Runner            (evcide.runners.FlashRunner)
          -> Runtime Output Receivers (evcide.receivers)
            -> Serial / BLE / Socket / ROS / File / HIL
          -> Verification Engine     (evcide.verify)
          -> Contract DSL            (evcide.dsl — terse text -> contract)
        -> Diagnostics + Repair Loop (evcide.repair + evcide.mutation)
          -> classify -> hints -> LLM-ready prompt -> best-of-N + don't-game-the-metric
          -> break-on-purpose (contract mutation testing)
        -> WAL + Telemetry           (evcide.wal)
```

All hardware operations are local. Cloud LLM APIs are optional.

---

## JARVIS-substrate mapping

For operators familiar with the JARVIS coordination substrate, the mapping is:

| EVC-IDE concept | JARVIS-substrate equivalent |
|---|---|
| Board adapter interface | Hook adapter / substrate-port pattern |
| Verification contract (JSON expectations) | Math-enforced invariant per Augmented Mechanism Design |
| Build/flash runner | Substrate-port: same interface, different substrate |
| Runtime event stream | Append-only telemetry JSONL |
| Repair-loop classification | Universal-coverage-hook (one detector covers a class of failures) |
| Agent loop steps 1-10 (spec section 17) | Autonomous-continue + WWWD self-correction |

The substrate philosophy is the same: structure does the work, not policy. The IDE does not enforce code quality via lint policy; it enforces firmware quality via hardware verification by construction.

---

## Layout

```
src/evcide/
  models.py         data types (RuntimeEvent, BuildResult, FlashResult,
                    VerificationContract, VerificationResult, ...)
  adapters/
    base.py         BoardAdapter ABC + DetectedBoard
    nrf52.py        Nordic nRF52 family (XIAO, DK)
    stm32.py        STM32 family (stub; raises NotImplementedError)
    esp32.py        ESP32 family (stub)
    rp2040.py       RP2040/RP2350 family (stub)
    registry.py     family detection + selection
  runners.py        BuildRunner, FlashRunner — subprocess wrappers
  receivers.py      Serial + File receivers + BLE/Socket/ROS stubs
  verify.py         VerificationEngine + checks (contains, count, rate,
                    field, numeric, liveness, protocol) + offline evaluate_events
  mutation.py       break-on-purpose: contract mutation testing (assess_contract)
  repair.py         repair loop: classify -> hints -> RepairRequest (LLM prompt),
                    NullFixProvider + SettingsFixProvider, run_repair_loop
  dsl.py            terse text -> VerificationContract (parse_contract)
  capture.py        record a run to JSONL (CaptureSink) + faithful replay (load_events)
  api.py            FastAPI app + WebSocket streams
  wal.py            append-only JSONL telemetry
examples/
  xiao_imu_ble_stream.json   Soham's demo verification contract
  blink_serial.json          basic serial verification contract
scripts/
  demo_xiao.py      end-to-end XIAO nRF52840 Sense demo (needs a board / west)
  demo_loop.py      OFFLINE end-to-end loop demo (no board, no LLM) — run this first
tests/              unit tests (81 passing; verify, mutation, repair, dsl, api, demo)
```

---

## The verification + repair moat (shipped, hardware-free)

The moat — "did it actually work?" — is built end-to-end and runs with no board:

```
python scripts/demo_loop.py     # author -> verify(FAIL) -> repair -> fix -> verify(PASS) -> break-on-purpose
```

* **Verify** — `VerificationContract` of measurable expectations vs. a live or replayed
  event stream; PASS / FAIL / INCONCLUSIVE with per-check evidence + timing windows + liveness.
* **Author** — `dsl.py` turns terse text (`contains BOOT_OK within_ms:3000`) into a contract;
  `POST /contracts/parse`. Honest: bad input is a parse error, never a silent wrong contract.
* **Break-on-purpose** — `mutation.py` mutates a passing evidence stream to prove the contract
  would *catch* a real break; a surviving mutant = theater. `POST /contracts/assess`.
* **Repair** — `repair.py` classifies the failure, emits an LLM-ready fix prompt, and closes the
  loop with `run_repair_loop` (best-of-N + reject any fix that passes by weakening the contract).
  `SettingsFixProvider` fixes the mechanical config class with **no LLM**.

## Roadmap

* **DONE (this slice):** XIAO nRF52840 Sense path + the full verify/author/assess/repair loop above.
* **Needs an LLM endpoint:** a source-level `FixProvider` (the prompt + seam are ready).
* **Needs hardware:** STM32 / ESP32 / RP2040 adapters, ROS receiver, HIL, on-board re-verify.
* **Frontend integration:** Void IDE panels consume the WebSocket streams.

---

## License

TBD. Internal to Soham + Will collaboration until decided.

## Credits

* Spec authored by **Soham** (2026-06-09, v2.0).
* Backend implementation by Will + JARVIS substrate.
* Architectural lineage: JARVIS substrate patterns (substrate-port, augmented mechanism design, universal-coverage-hook).
