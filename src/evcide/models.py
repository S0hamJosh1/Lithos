"""Data types for the EVC-IDE backend.

Maps directly to PDF Section 13 (board adapter), Section 18 (BuildResult),
Section 20.1 (RuntimeEvent), Section 21.2 (VerificationResult), and Section 22
(VerificationContract).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ============ Hardware identity ============

class BoardFamily(str, Enum):
    STM32 = "stm32"
    ESP32 = "esp32"
    RP2040 = "rp2040"
    NRF52 = "nrf52"
    NRF53 = "nrf53"
    NRF54 = "nrf54"


class ReceiverKind(str, Enum):
    SERIAL = "serial"
    BLE = "ble"
    SOCKET = "socket"
    ROS_TOPIC = "ros_topic"
    FILE = "file"
    HIL = "hil"


class DetectedBoard(BaseModel):
    """A board we can talk to right now."""
    id: str                              # stable id per session (e.g. "xiao-nrf52840-COM7")
    profile_id: str                      # the board-profile id matched (e.g. "seeed_xiao_nrf52840_sense")
    family: BoardFamily
    serial_port: str | None = None       # /dev/ttyUSB0, COM7, etc.
    serial_baud_default: int = 115200
    probe_id: str | None = None          # ST-Link / J-Link / nRF-DAPLink serial
    usb_vid_pid: tuple[int, int] | None = None
    mass_storage_volume: str | None = None  # for UF2 (RP2040)
    capabilities: list[str] = Field(default_factory=list)
    confidence: float = 1.0              # detection confidence 0..1


class BoardProfile(BaseModel):
    """Static board metadata. Lives in board profile JSON."""
    id: str
    family: BoardFamily
    display_name: str
    frameworks: list[str]
    default_framework: str
    build_tool: str
    flash_tool: str
    default_serial_baud: int = 115200
    capabilities: list[str] = Field(default_factory=list)
    verification_receivers: list[ReceiverKind] = Field(default_factory=list)
    usb_vid_pids: list[tuple[int, int]] = Field(default_factory=list)


# ============ Project / workspace ============

class ProjectConfig(BaseModel):
    profile_id: str
    name: str
    framework: str
    target_dir: str
    template: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class ProjectMetadata(BaseModel):
    name: str
    profile_id: str
    framework: str
    root: str
    detected_files: list[str] = Field(default_factory=list)


class ProjectResult(BaseModel):
    success: bool
    root: str
    metadata: ProjectMetadata | None = None
    error: str | None = None


class ProjectClassification(BaseModel):
    """Inferred board/toolchain for an imported project (PDF Section 14).

    Produced by the project classifier when /projects/import is called without an
    explicit profile_id.
    """
    profile_id: str
    family: BoardFamily
    framework: str
    confidence: float                    # 0..1
    evidence: list[str] = Field(default_factory=list)


# ============ Build / Flash ============

class Diagnostic(BaseModel):
    file: str | None = None
    line: int | None = None
    column: int | None = None
    severity: str                        # "error" | "warning" | "note"
    message: str
    code: str | None = None              # e.g. "-Wuninitialized"


class BuildResult(BaseModel):
    success: bool
    command: str
    exit_code: int
    duration_ms: int
    artifacts: list[str] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    raw_log_path: str | None = None
    summary: str = ""


class FlashResult(BaseModel):
    success: bool
    command: str
    exit_code: int
    duration_ms: int
    reconnected: bool = False            # board came back after reset
    raw_log_path: str | None = None
    summary: str = ""
    failure_class: str | None = None     # "no_device" | "perm_denied" | "bootloader_missing"
                                         # | "wrong_port" | "probe_locked" | "timeout" | "unknown"


# ============ Runtime events ============

class RuntimeEvent(BaseModel):
    """Per PDF Section 20.1. One event per line from a receiver."""
    source: ReceiverKind
    timestamp_ms: int                    # wall clock at receive
    board_id: str
    stream_id: str                       # session-scoped receiver id
    type: str                            # adapter-defined ("line", "advertisement",
                                         # "packet", "ros_msg", "row", "measurement")
    raw: str | bytes | None = None
    parsed: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OutputConfig(BaseModel):
    receiver: ReceiverKind
    serial_port: str | None = None
    serial_baud: int | None = None
    ble_filter: dict[str, Any] | None = None
    socket_host: str | None = None
    socket_port: int | None = None
    ros_topic: str | None = None
    file_path: str | None = None


class OutputSession(BaseModel):
    stream_id: str
    receiver: ReceiverKind
    board_id: str
    started_at_ms: int
    config: OutputConfig


# ============ Verification ============

class ExpectationKind(str, Enum):
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    COUNT_MIN = "count_min"
    COUNT_MAX = "count_max"
    COUNT_EXACT = "count_exact"
    SEQUENCE = "sequence"
    MESSAGE_RATE_HZ = "message_rate_hz"
    FIELD_PRESENT = "field_present"
    FIELD_RANGE = "field_range"
    FIELD_NOT_FROZEN = "field_not_frozen"
    NO_NAN = "no_nan"
    NO_TIMEOUT = "no_timeout"
    BLE_ADVERTISING = "ble_advertising"
    GATT_SERVICE_PRESENT = "gatt_service_present"
    SOCKET_REACHABLE = "socket_reachable"
    ROS_TOPIC_RATE_HZ = "ros_topic_rate_hz"


class Expectation(BaseModel):
    """One measurable check inside a contract."""
    kind: ExpectationKind
    # Catch-all params. Concrete checks declare which keys they read.
    pattern: str | None = None
    field: str | None = None
    count: int | None = None
    within_ms: int | None = None
    after_ms: int | None = None
    min_rate_hz: float | None = None
    max_rate_hz: float | None = None
    expected_rate_hz: float | None = None
    rate_tolerance_pct: float | None = None
    sequence: list[str] | None = None
    value_min: float | None = None
    value_max: float | None = None
    duration_ms: int | None = None
    service_uuid: str | None = None
    host: str | None = None
    port: int | None = None
    label: str | None = None


class ReceiverDef(BaseModel):
    type: ReceiverKind
    port: str | None = None              # serial port; "auto" for adapter to pick
    baud: int | None = None
    ble_filter: dict[str, Any] | None = None
    socket: dict[str, Any] | None = None
    ros_topic: str | None = None
    file_path: str | None = None


class VerificationContract(BaseModel):
    """Per PDF Section 22.1 — converts a prompt into measurable expectations."""
    id: str
    target: str                          # board profile id this targets
    timeout_ms: int = 10000
    receivers: list[ReceiverDef]
    expectations: list[Expectation]
    description: str | None = None


class CheckResult(BaseModel):
    expectation: Expectation
    status: str                          # "pass" | "fail" | "inconclusive"
    actual: dict[str, Any] = Field(default_factory=dict)
    evidence_event_ids: list[int] = Field(default_factory=list)
    message: str = ""


class RuntimeEvidence(BaseModel):
    """Per-receiver evidence bundle attached to a verification result."""
    stream_id: str
    receiver: ReceiverKind
    event_count: int
    duration_ms: int
    first_event_at_ms: int | None = None
    last_event_at_ms: int | None = None
    sample_events: list[RuntimeEvent] = Field(default_factory=list)


class RepairHint(BaseModel):
    classification: str                  # "no_serial_data" | "wrong_baud" |
                                         # "imu_frozen" | "rate_too_low" | "ble_not_advertising"
                                         # | "boot_msg_missing" | "build_error" | "flash_failure"
    severity: str                        # "info" | "warn" | "block"
    suggestion: str
    target_files: list[str] = Field(default_factory=list)
    target_settings: dict[str, Any] = Field(default_factory=dict)


class RepairRequest(BaseModel):
    """A deterministic, LLM-ready repair prompt assembled from a failed
    verification — the classifier→targeted-prompt half of the repair loop
    (PDF Section 23). Built with ZERO model calls; the actual fix generation is
    an injected FixProvider boundary, so this artifact is fully testable here.
    """
    contract_id: str
    overall_status: str                  # "fail" | "inconclusive"
    failure_classifications: list[str] = Field(default_factory=list)  # de-duped, severity-ordered
    failed_checks: list[CheckResult] = Field(default_factory=list)
    hints: list[RepairHint] = Field(default_factory=list)
    target_files: list[str] = Field(default_factory=list)  # union of hint target_files
    evidence_digest: str = ""            # compact, human/LLM-readable evidence summary
    prompt: str = ""                     # the rendered fix prompt handed to an LLM


class FixProposal(BaseModel):
    """What a FixProvider returns after consuming a RepairRequest. The loop's
    output. `applied=False` + empty diff is the honest "no fix" answer."""
    applied: bool = False
    diff: str | None = None              # unified diff against the project source
    explanation: str = ""
    confidence: float = 0.0              # 0..1


class RepairAttempt(BaseModel):
    """One candidate fix, re-verified and meaningfulness-checked — the output of
    the closed repair loop (best-of-N + don't-game-the-metric). A fix is accepted
    only if it makes the contract PASS *and* leaves the contract meaningful (a fix
    that passes by weakening the oracle gamed the metric and is rejected).
    """
    proposal: FixProposal
    reverify_status: str                 # "pass" | "fail" | "inconclusive"
    contract_still_meaningful: bool | None = None  # None = not assessed
    accepted: bool
    reason: str


class SurvivingMutant(BaseModel):
    """A deliberate break the contract FAILED to catch — a theater signal.

    The expectation that should have gone red stayed green when the behavior it
    guards was mutated, so it isn't actually testing what it claims to.
    """
    expectation_label: str               # which expectation should have caught it
    expectation_kind: str
    mutator: str                         # the mutation that was applied
    detail: str                          # what the mutation did to the stream


class MutationReport(BaseModel):
    """Break-on-purpose result (PDF Section 24) — the confidence-loop keystone.

    "Inject one deliberate bug and confirm a test goes red. If nothing fails, the
    test is theater." Here the 'test' is a VerificationContract and the bug is a
    mutation of the *evidence stream*, so it runs with no hardware and no rebuild.
    A contract is only trustworthy once every mutant is killed.
    """
    contract_id: str
    baseline_passed: bool                # mutation testing is only valid on a PASSING baseline
    total_mutants: int
    killed: int                          # contract correctly stopped passing
    survived: int                        # contract still passed = theater
    score: float                         # killed / total, 0..1 (mutation score)
    meaningful: bool                     # baseline_passed and survived == 0
    survivors: list[SurvivingMutant] = Field(default_factory=list)
    note: str = ""


class ExpectationCoverage(BaseModel):
    """Whether one expectation is load-bearing — i.e. it uniquely catches at least
    one deliberate break that no other expectation in the contract catches."""
    index: int
    kind: str
    label: str
    unique_kills: int                    # breaks ONLY this expectation caught
    load_bearing: bool                   # unique_kills > 0


class MinimalityReport(BaseModel):
    """Leave-one-out analysis (the dual of break-on-purpose): is every expectation
    necessary? An expectation with no unique kill is redundant or a coverage gap —
    the contract would catch the same breaks without it."""
    contract_id: str
    baseline_passed: bool
    expectations: list[ExpectationCoverage] = Field(default_factory=list)
    redundant: list[int] = Field(default_factory=list)   # indices with 0 unique kills
    minimal: bool = False                # no redundant expectations
    note: str = ""


class VerificationResult(BaseModel):
    """Per PDF Section 21.2."""
    status: str                          # "pass" | "fail" | "inconclusive"
    contract_id: str
    started_at: str                      # ISO 8601
    ended_at: str
    checks: list[CheckResult]
    evidence: list[RuntimeEvidence]
    failure_classification: str | None = None
    repair_hints: list[RepairHint] = Field(default_factory=list)
    repair_request: RepairRequest | None = None
    agent_summary: str = ""


# ============ Helpers ============

def now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
