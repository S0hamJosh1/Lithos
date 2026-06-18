"""Contract DSL — terse text → VerificationContract (PDF Section 22.1, deterministic subset).

One expectation per line, so a human or the frontend can author a verification
contract without hand-writing JSON. Full natural language still needs an LLM; this
covers the common, unambiguous cases. Unparseable input raises `DSLError` with the
line number + reason — it never silently produces a wrong contract (a verification
contract that says something other than what you meant is worse than a parse error).

Header directives (optional, `@key value`):
    @id boot-check     @target seeed_xiao_nrf52840_sense
    @receiver serial   @timeout 10000

Expectation lines (one per check):
    contains BOOT_OK [within_ms:3000] [after_ms:500]
    not_contains PANIC
    sequence INIT,CALIB,READY [within_ms:5000]
    count_min HEARTBEAT 5    |  count_max ERROR 0   |  count_exact DONE 1
    rate min:10              |  rate max:60          |  rate expected:50 tol:10
    field accel_x not_frozen |  field accel_x present
    field accel_x range:-2..2   |  field temp no_nan
    no_timeout 1000
    ble_advertising [XIAO]   |  gatt_service 0x180D  |  socket_reachable
"""
from __future__ import annotations

from .models import (
    Expectation,
    ExpectationKind,
    ReceiverDef,
    ReceiverKind,
    VerificationContract,
)


class DSLError(ValueError):
    """A contract line that could not be parsed. Carries the line number + reason."""

    def __init__(self, line_no: int, line: str, reason: str):
        self.line_no = line_no
        self.line = line
        self.reason = reason
        super().__init__(f"line {line_no}: {reason} — {line!r}")


def _split(tokens: list[str]) -> tuple[list[str], dict[str, str]]:
    """Partition tokens into positionals and `key:value` params. A token is a param
    only when the part before ':' is a non-numeric identifier (so 'range:-2..2' is a
    param but '0x180D' / a bare value is positional)."""
    pos: list[str] = []
    kv: dict[str, str] = {}
    for t in tokens:
        if ":" in t:
            k, v = t.split(":", 1)
            if k and not k[0].isdigit() and k.replace("_", "").isalnum():
                kv[k] = v
                continue
        pos.append(t)
    return pos, kv


def _int(line_no: int, line: str, name: str, val: str) -> int:
    try:
        return int(val)
    except ValueError:
        raise DSLError(line_no, line, f"{name} must be an integer, got {val!r}") from None


def _float(line_no: int, line: str, name: str, val: str) -> float:
    try:
        return float(val)
    except ValueError:
        raise DSLError(line_no, line, f"{name} must be a number, got {val!r}") from None


def _timing(exp_kwargs: dict, kv: dict[str, str], line_no: int, line: str) -> None:
    if "within_ms" in kv:
        exp_kwargs["within_ms"] = _int(line_no, line, "within_ms", kv["within_ms"])
    if "after_ms" in kv:
        exp_kwargs["after_ms"] = _int(line_no, line, "after_ms", kv["after_ms"])


def parse_expectation(line: str, line_no: int = 0) -> Expectation:
    """Parse one DSL line into an Expectation. Raises DSLError on bad input."""
    tokens = line.split()
    if not tokens:
        raise DSLError(line_no, line, "empty expectation")
    directive = tokens[0].lower()
    pos, kv = _split(tokens[1:])
    kw: dict = {}

    if directive in ("contains", "not_contains"):
        if not pos:
            raise DSLError(line_no, line, f"{directive} needs a pattern")
        kw["pattern"] = pos[0]
        _timing(kw, kv, line_no, line)
        kind = ExpectationKind.CONTAINS if directive == "contains" else ExpectationKind.NOT_CONTAINS
        return Expectation(kind=kind, **kw)

    if directive == "sequence":
        if not pos:
            raise DSLError(line_no, line, "sequence needs comma-separated tokens")
        kw["sequence"] = [s for s in pos[0].split(",") if s]
        _timing(kw, kv, line_no, line)
        return Expectation(kind=ExpectationKind.SEQUENCE, **kw)

    if directive in ("count_min", "count_max", "count_exact"):
        if len(pos) < 2:
            raise DSLError(line_no, line, f"{directive} needs '<pattern> <count>'")
        kw["pattern"] = pos[0]
        kw["count"] = _int(line_no, line, "count", pos[1])
        kind = {
            "count_min": ExpectationKind.COUNT_MIN,
            "count_max": ExpectationKind.COUNT_MAX,
            "count_exact": ExpectationKind.COUNT_EXACT,
        }[directive]
        return Expectation(kind=kind, **kw)

    if directive == "rate":
        if "min" in kv:
            kw["min_rate_hz"] = _float(line_no, line, "min", kv["min"])
        if "max" in kv:
            kw["max_rate_hz"] = _float(line_no, line, "max", kv["max"])
        if "expected" in kv:
            kw["expected_rate_hz"] = _float(line_no, line, "expected", kv["expected"])
            kw["rate_tolerance_pct"] = _float(line_no, line, "tol", kv.get("tol", "10"))
        if not kw:
            raise DSLError(line_no, line, "rate needs min:, max:, or expected:+tol:")
        return Expectation(kind=ExpectationKind.MESSAGE_RATE_HZ, **kw)

    if directive == "field":
        if not pos:
            raise DSLError(line_no, line, "field needs a name")
        kw["field"] = pos[0]
        # field <name> range:lo..hi  (range arrives as a kv param)
        if "range" in kv:
            lo_hi = kv["range"].split("..")
            if len(lo_hi) != 2:
                raise DSLError(line_no, line, "range must be 'lo..hi'")
            kw["value_min"] = _float(line_no, line, "range-min", lo_hi[0])
            kw["value_max"] = _float(line_no, line, "range-max", lo_hi[1])
            return Expectation(kind=ExpectationKind.FIELD_RANGE, **kw)
        if len(pos) < 2:
            raise DSLError(line_no, line, "field needs '<name> <check>' or '<name> range:lo..hi'")
        check = pos[1].lower()
        if check == "present":
            return Expectation(kind=ExpectationKind.FIELD_PRESENT, **kw)
        if check == "not_frozen":
            return Expectation(kind=ExpectationKind.FIELD_NOT_FROZEN, **kw)
        if check == "no_nan":
            return Expectation(kind=ExpectationKind.NO_NAN, **kw)
        raise DSLError(line_no, line, f"unknown field check {check!r} (present|not_frozen|no_nan|range:)")

    if directive == "no_timeout":
        if not pos:
            raise DSLError(line_no, line, "no_timeout needs a duration in ms")
        kw["duration_ms"] = _int(line_no, line, "duration", pos[0])
        return Expectation(kind=ExpectationKind.NO_TIMEOUT, **kw)

    if directive == "ble_advertising":
        if pos:
            kw["pattern"] = pos[0]
        return Expectation(kind=ExpectationKind.BLE_ADVERTISING, **kw)

    if directive == "gatt_service":
        if not pos:
            raise DSLError(line_no, line, "gatt_service needs a UUID")
        kw["service_uuid"] = pos[0]
        return Expectation(kind=ExpectationKind.GATT_SERVICE_PRESENT, **kw)

    if directive == "socket_reachable":
        return Expectation(kind=ExpectationKind.SOCKET_REACHABLE)

    raise DSLError(line_no, line, f"unknown directive {directive!r}")


_HEADER_KEYS = {"id", "target", "receiver", "timeout"}


def parse_contract(
    text: str,
    *,
    id: str = "dsl-contract",
    target: str = "",
    receiver: str = "serial",
    timeout_ms: int = 10000,
) -> VerificationContract:
    """Parse a multi-line DSL document into a VerificationContract.

    `@key value` header lines override the corresponding argument. Blank lines and
    `#` comments are ignored. At least one expectation is required.
    """
    cfg = {"id": id, "target": target, "receiver": receiver, "timeout": str(timeout_ms)}
    expectations: list[Expectation] = []

    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("@"):
            parts = line[1:].split(None, 1)
            key = parts[0].lower()
            if key not in _HEADER_KEYS:
                raise DSLError(i, raw, f"unknown header @{key} (id|target|receiver|timeout)")
            cfg[key] = parts[1].strip() if len(parts) > 1 else ""
            continue
        expectations.append(parse_expectation(line, i))

    if not expectations:
        raise DSLError(0, "", "contract has no expectations")

    try:
        rk = ReceiverKind(cfg["receiver"])
    except ValueError:
        raise DSLError(0, cfg["receiver"], f"unknown receiver kind {cfg['receiver']!r}") from None

    return VerificationContract(
        id=cfg["id"],
        target=cfg["target"],
        timeout_ms=int(cfg["timeout"]),
        receivers=[ReceiverDef(type=rk)],
        expectations=expectations,
    )
