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
        -> Diagnostics + Repair Loop (planned)
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
                    field, numeric, protocol)
  api.py            FastAPI app + WebSocket streams
  wal.py            append-only JSONL telemetry
examples/
  xiao_imu_ble_stream.json   Soham's demo verification contract
  blink_serial.json          basic serial verification contract
scripts/
  demo_xiao.py      end-to-end XIAO nRF52840 Sense demo
tests/              unit tests for verify engine + contracts
```

---

## Roadmap

* **MVP (this slice):** XIAO nRF52840 Sense end-to-end (detect / build / flash / BLE+serial receivers / verify / report)
* **Tier 1 expansion:** STM32 Nucleo F401RE/F446RE + ESP32 DevKitC adapters
* **Tier 2 expansion:** RP2040/Pico + nRF52840 DK
* **Receiver expansion:** WiFi/Socket (ESP32), ROS topic (robotics), HIL (future)
* **Repair loop v2:** classifier + targeted-fix prompts wired into the LLM provider abstraction
* **Frontend integration:** Void IDE panels consume the WebSocket streams

---

## License

TBD. Internal to Soham + Will collaboration until decided.

## Credits

* Spec authored by **Soham** (2026-06-09, v2.0).
* Backend implementation by Will + JARVIS substrate.
* Architectural lineage: JARVIS substrate patterns (substrate-port, augmented mechanism design, universal-coverage-hook).
