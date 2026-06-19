# Lithos / evcide — Gameplan to "everything done"

> Definition of done = the full EVC-IDE backend per Soham's `Embedded_Vibe_Coding_IDE_Master_Documentation_v2.pdf` v2.0:
> author → build → flash → observe → **verify** → diagnose → **repair** → retest, across the Tier-1 boards,
> wired to the Void IDE frontend, with the AI agent loop closing on a real LLM.

## Status snapshot (2026-06-18)
- **Verification + repair moat: COMPLETE and hardware-free** — phases 0–13, **85 tests**, ruff clean, pushed.
  Verify · contract DSL · break-on-purpose mutation testing · repair loop (classify → prompt → best-of-N +
  don't-game-the-metric) · `SettingsFixProvider` (real, no-LLM) · liveness · capture/replay · offline demo.
- **nRF52 (XIAO) path: implemented**, BLE + `west` paths HW-untested (no board on the dev box).
- **Stubs:** stm32 / esp32 / rp2040 adapters, ROS receiver, source-level LLM FixProvider.

## The work, bucketed by what unblocks it

### A — Hardware-free, NO external dependency (I can finish this solo, now)
- **A1** DSL example contracts (`examples/*.evc`) + wire `/contracts/parse` into the demo.
- **A2** Faithful JSONL **replay receiver** so `run_verification`'s live path (streaming + sink + timeout)
  can replay a capture, not just `evaluate_events`. Closes capture↔live-replay parity.
- **A3** Resolve the **workspace-manager gap**: the README architecture names `evcide.workspace`, but project
  lifecycle currently lives in `adapters.create_project`. Decision: build `workspace.py` to centralize, OR
  correct the doc. (Recommend: thin `workspace.py` that delegates to the adapter — matches the spec diagram.)
- **A4** Polish: error-path test coverage, WAL/telemetry completeness, `/verify` over the FILE/replay receiver.
- **A5** Package: a PR-ready summary of phases 8–13 for Soham (repo already self-documents via README + demo).

### B — Needs an LLM endpoint (one dependency: a key/endpoint)
- **B1** LLM provider abstraction + **source-level `FixProvider`** (consumes `RepairRequest.prompt` → unified
  diff). The seam + prompt are already built; this fills the "AI Agent Orchestrator (planned)" box.
- **B2** Wire it through `run_repair_loop` end-to-end: LLM proposes → apply diff → re-verify → re-assess
  (best-of-N + don't-game-the-metric already enforce quality).
- **B3** The full agent loop (spec §17 steps 1–10) orchestration over a project.
- *Unblock:* an LLM endpoint (Anthropic/Bedrock/local). Then I can build + integration-test B end-to-end.

### C — Needs hardware (one dependency per board family)
- **C1** Validate the nRF52 path on a real **XIAO nRF52840 Sense** (BLE `bleak` + `west` build/flash).
- **C2** STM32 adapter (OpenOCD/CubeProgrammer) — needs a Nucleo.
- **C3** ESP32 adapter (idf.py/esptool) + WiFi socket verify — needs a DevKitC.
- **C4** RP2040 adapter (UF2 mass-storage) — needs a Pico.
- **C5** ROS receiver — needs a ROS env.  • **C6** HIL — future.
- *Unblock:* physical boards + toolchains, at the board (me driving, or Soham).

### D — Needs frontend coordination (Soham / Void IDE)
- **D1** Void IDE panels consume the WebSocket streams (`/streams/{id}`). The API is the contract; it's stable.
- **D2** End-to-end IDE↔backend integration test.
- *Unblock:* Soham's frontend work against the live API.

### E — Coordination / decisions (Will + Soham)
- **E1** Message Soham — share backend state + `python scripts/demo_loop.py`. *(held for Will.)*
- **E2** License decision (README = TBD).
- **E3** Secure resources: a board (→C), an LLM endpoint (→B), a ROS env (→C5).

## Critical path (fastest route to "everything done")
```
M1  Finish Bucket A            ── solo, now ──► backend feature-complete for the hardware-free + offline path
      │
M2  Will → Soham (E1)          ── unlocks ──► hardware access (C) · frontend (D) · maybe an endpoint (B/E3)
      ├──► M3a  LLM endpoint (E3) ─► Bucket B  (source-fix loop; I build + integ-test)
      ├──► M3b  boards (E3)       ─► Bucket C  (adapters, at the hardware)
      └──► M3c  frontend (D)      ─► Soham's side, my API as contract
M4  Integration + HIL + license (E2) ──► v1 "everything done"
```
**The bottleneck is M2 (Soham engagement), not code.** A is the only large body of work that needs no one
else; after that, progress is gated on hardware, an endpoint, and the frontend — all external.

## Recommended sequence
1. **Now (this/next session):** burn down Bucket A (A1→A5). Leaves the backend as complete as it can be
   without hardware/LLM/frontend, fully tested, and packaged.
2. **Will:** do E1 (message Soham) — it unblocks the entire right side of the graph.
3. **On first endpoint:** Bucket B (I can own this).
4. **On first board:** Bucket C, one family at a time (nRF52 first — already implemented, just unvalidated).
5. **Ongoing:** D with Soham; E2 license when ready.
