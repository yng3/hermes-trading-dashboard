"""Read-only backend for the Hermes Trading Dashboard.

The dashboard intentionally reads the Kraken CLI paper state file directly.
Polling ``kraken-cli futures paper status/positions`` would reconcile the
simulator and could trigger a paper stop merely because somebody opened the
dashboard. Market prices come from the public futures ticker command, while
paper mutations remain owned exclusively by the supervised bot cycle.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shlex
import sqlite3
import stat
import unicodedata
from collections import deque
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter()

SERVICE_UNIT = "eth-paper-cycle.service"
TIMER_UNIT = "eth-paper-cycle.timer"
DEFAULT_PAPER_STATE = (
    Path.home() / ".config" / "kraken" / "paper" / "futures_state.json"
)
GENESIS_HASH = "0" * 64
MAX_JSON_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_TICKER_AGE_SECONDS = 60
MAX_CYCLE_AGE_SECONDS = 5 * 60 * 60
MAX_CLOCK_SKEW_SECONDS = 30
MINIMAL_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
MAX_SAFE_INTEGER = 2**53 - 1
FUTURES_SYMBOL_PATTERN = re.compile(r"PF_[A-Z0-9]{3,24}")
PROTECTIVE_CLIENT_ORDER_ID_PATTERN = re.compile(
    r"ethbot-stop-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
)
RFC3339_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.(?P<fraction>[0-9]{1,9}))?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
NANOSECONDS_PER_SECOND = 1_000_000_000
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _rounded(value: float, places: int = 10) -> float:
    return round(float(value), places)


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    if isinstance(value, Decimal):
        if not value.is_finite() or abs(value) > MAX_SAFE_INTEGER:
            return None
    elif isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            return None
    elif not math.isfinite(value) or abs(value) > MAX_SAFE_INTEGER:
        return None
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or abs(parsed) > MAX_SAFE_INTEGER:
        return None
    if isinstance(value, Decimal) and Decimal(str(parsed)) != value:
        return None
    return parsed


def _number(value: Any, default: float = 0.0) -> float:
    parsed = _safe_float(value)
    return parsed if parsed is not None else default


def _number_or_none(value: Any) -> float | None:
    return _safe_float(value)


def _decimal_or_none(value: Any, *, derived: bool = False) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    if not derived and _safe_float(value) is None:
        return None
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _contract_decimal_or_none(value: Any, *, derived: bool = False) -> Decimal | None:
    if isinstance(value, str):
        if not value or len(value) > 128:
            return None
        try:
            parsed = Decimal(value)
        except (InvalidOperation, ValueError):
            return None
        if not parsed.is_finite():
            return None
        if not derived and _safe_float(parsed) is None:
            return None
        return parsed
    return _decimal_or_none(value, derived=derived)


def _strict_bool(value: Any) -> bool:
    return value is True


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _non_empty_text(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(
            unicodedata.category(character).startswith("C") for character in value
        )
    )


def _valid_identity(value: Any) -> bool:
    """Validate machine identities with a runtime-independent ASCII grammar."""
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@/+_-]*", value) is not None
    )


def _valid_futures_symbol(value: Any) -> bool:
    return (
        isinstance(value, str) and FUTURES_SYMBOL_PATTERN.fullmatch(value) is not None
    )


def _valid_protective_client_order_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and PROTECTIVE_CLIENT_ORDER_ID_PATTERN.fullmatch(value) is not None
    )


def _absolute_environment_path(value: Any, *, label: str) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{label} path is malformed")
    try:
        os.fsencode(value)
        path = Path(value).expanduser()
    except (OSError, RuntimeError, TypeError, UnicodeError, ValueError) as exc:
        raise ValueError(f"{label} path is malformed") from exc
    if not path.is_absolute():
        raise ValueError(f"{label} path must be absolute")
    return path


def _numbers_equal(left: Any, right: Any) -> bool:
    left_number = _decimal_or_none(left)
    right_number = _decimal_or_none(right)
    return (
        left_number is not None
        and right_number is not None
        and left_number == right_number
    )


def _strict_json_loads(value: str | bytes) -> Any:
    def reject_constant(constant: str) -> None:
        raise ValueError(f"non-finite JSON constant is forbidden: {constant}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON key is forbidden")
            result[key] = item
        return result

    return json.loads(
        value,
        parse_float=Decimal,
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicate_keys,
    )


def _sanitise_json_value(
    value: Any,
    *,
    depth: int = 0,
    ancestors: frozenset[int] = frozenset(),
    preserve_decimals: bool = False,
) -> tuple[Any, bool]:
    if value is None or isinstance(value, (str, bool)):
        return value, True
    if isinstance(value, int):
        return (value, True) if abs(value) <= MAX_SAFE_INTEGER else (None, False)
    if isinstance(value, (float, Decimal)):
        parsed = _safe_float(value)
        if parsed is not None and preserve_decimals and isinstance(value, Decimal):
            return value, True
        return (parsed, True) if parsed is not None else (None, False)
    if not isinstance(value, (dict, list)) or depth >= MAX_JSON_DEPTH:
        return None, False
    identity = id(value)
    if identity in ancestors:
        return None, False
    child_ancestors = ancestors | {identity}
    valid = True
    if isinstance(value, list):
        items: list[Any] = []
        for item in value:
            safe_item, item_valid = _sanitise_json_value(
                item,
                depth=depth + 1,
                ancestors=child_ancestors,
                preserve_decimals=preserve_decimals,
            )
            items.append(safe_item)
            valid = valid and item_valid
        return items, valid
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            valid = False
            continue
        safe_item, item_valid = _sanitise_json_value(
            item,
            depth=depth + 1,
            ancestors=child_ancestors,
            preserve_decimals=preserve_decimals,
        )
        result[key] = safe_item
        valid = valid and item_valid
    return result, valid


def _fresh_timestamp(
    value: Any,
    *,
    now: datetime,
    max_age_seconds: float = MAX_CYCLE_AGE_SECONDS,
) -> bool:
    parsed = _parse_timestamp_exact(value)
    now_ns = _datetime_nanoseconds(now)
    max_age = _decimal_or_none(max_age_seconds, derived=True)
    if parsed is None or now_ns is None or max_age is None or max_age < 0:
        return False
    age_ns = now_ns - parsed[1]
    return (
        -MAX_CLOCK_SKEW_SECONDS * NANOSECONDS_PER_SECOND <= age_ns
        and Decimal(age_ns) <= max_age * NANOSECONDS_PER_SECOND
    )


def _stop_protects_position(position: dict[str, Any], stop_price: float) -> bool:
    if position["side"] == "long":
        return stop_price < position["entry_price"]
    return stop_price > position["entry_price"]


def _datetime_nanoseconds(value: datetime) -> int | None:
    try:
        normalised = (
            value.astimezone(UTC)
            if value.tzinfo is not None
            else value.replace(tzinfo=UTC)
        )
        delta = normalised - UNIX_EPOCH
    except (OverflowError, ValueError):
        return None
    whole_seconds = delta.days * 86_400 + delta.seconds
    return whole_seconds * NANOSECONDS_PER_SECOND + normalised.microsecond * 1_000


def _parse_timestamp_exact(value: Any) -> tuple[datetime, int] | None:
    if not isinstance(value, str):
        return None
    match = RFC3339_PATTERN.fullmatch(value)
    if match is None:
        return None
    text = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return None
        normalised = parsed.astimezone(UTC)
    except (OverflowError, ValueError):
        return None
    whole_second_ns = _datetime_nanoseconds(normalised.replace(microsecond=0))
    if whole_second_ns is None:
        return None
    fraction = match.group("fraction") or ""
    fraction_ns = int(fraction.ljust(9, "0")) if fraction else 0
    return normalised, whole_second_ns + fraction_ns


def _parse_timestamp(value: Any) -> datetime | None:
    parsed = _parse_timestamp_exact(value)
    return parsed[0] if parsed is not None else None


def _is_hex_digest(value: Any, *, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _normalise_side(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalise_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _parse_properties(output: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        properties[key] = value
    return properties


def _parse_environment(value: str) -> tuple[dict[str, str], str | None]:
    environment: dict[str, str] = {}
    try:
        entries = shlex.split(value)
    except ValueError:
        return {}, "service environment is malformed"
    for entry in entries:
        if "=" not in entry:
            return {}, "service environment is malformed"
        key, item = entry.split("=", 1)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None or key in environment:
            return {}, "service environment is malformed"
        environment[key] = item
    return environment, None


def _open_trusted_executable(path_value: str | Path) -> tuple[str, int] | None:
    try:
        path = Path(path_value).expanduser()
        os.fsencode(path)
    except (OSError, RuntimeError, TypeError, UnicodeError, ValueError):
        return None
    if not path.is_absolute():
        return None
    trusted_owners = {0, os.getuid()}
    for parent in reversed(path.parents):
        try:
            parent_metadata = os.lstat(parent)
        except (OSError, ValueError):
            return None
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid not in trusted_owners
            or parent_metadata.st_mode & 0o022
        ):
            return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except (OSError, ValueError):
        return None
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in trusted_owners
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o022
        or not metadata.st_mode & 0o111
    ):
        os.close(descriptor)
        return None
    return str(path), descriptor


def _trusted_executable(path_value: str | Path) -> str | None:
    opened = _open_trusted_executable(path_value)
    if opened is None:
        return None
    path, descriptor = opened
    os.close(descriptor)
    return path


def _resolve_system_binary(name: str) -> str | None:
    for directory in (Path("/usr/bin"), Path("/bin")):
        resolved = _trusted_executable(directory / name)
        if resolved:
            return resolved
    return None


def _subprocess_environment() -> dict[str, str]:
    environment = {
        "HOME": str(Path.home()),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": MINIMAL_PATH,
        "TZ": "UTC",
    }
    for key in ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"):
        value = os.environ.get(key)
        if value and "\n" not in value and len(value) <= 4096:
            environment[key] = value
    return environment


async def _reap_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        await process.wait()
    except (ChildProcessError, ProcessLookupError):
        pass


async def _run_text(cmd: list[str], *, timeout: float = 15.0) -> dict[str, Any]:
    """Run a trusted read command without a shell or inherited secrets."""
    try:
        if not cmd or any(
            not isinstance(argument, str) or "\0" in argument for argument in cmd
        ):
            raise ValueError("invalid subprocess argument")
        for argument in cmd:
            os.fsencode(argument)
    except (TypeError, UnicodeError, ValueError):
        return {
            "ok": False,
            "stdout": "",
            "message": "command arguments are invalid",
        }
    opened = _open_trusted_executable(cmd[0]) if cmd else None
    if opened is None:
        return {
            "ok": False,
            "stdout": "",
            "message": "command executable is not trusted",
        }
    trusted_path, descriptor = opened
    try:
        try:
            process = await asyncio.create_subprocess_exec(
                f"/proc/self/fd/{descriptor}",
                *cmd[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_subprocess_environment(),
                pass_fds=(descriptor,),
            )
        except (OSError, TypeError, UnicodeError, ValueError):
            return {
                "ok": False,
                "stdout": "",
                "message": "command could not be started",
            }
    finally:
        os.close(descriptor)
    try:
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await _reap_process(process)
        return {
            "ok": False,
            "stdout": "",
            "message": f"command timed out after {timeout:g}s",
        }
    except asyncio.CancelledError:
        await asyncio.shield(_reap_process(process))
        raise
    output = stdout.decode(errors="replace")
    if process.returncode != 0:
        name = Path(trusted_path).name
        return {
            "ok": False,
            "stdout": output,
            "message": f"{name} exited {process.returncode}",
        }
    return {"ok": True, "stdout": output, "message": ""}


async def _run_json(cmd: list[str], *, timeout: float = 15.0) -> dict[str, Any]:
    result = await _run_text(cmd, timeout=timeout)
    if not result["ok"]:
        return {"error": True, "message": result["message"]}
    try:
        parsed = _strict_json_loads(result["stdout"])
        parsed, tree_valid = _sanitise_json_value(parsed, preserve_decimals=True)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return {"error": True, "message": "command returned non-JSON output"}
    return (
        parsed
        if tree_valid and isinstance(parsed, dict)
        else {"error": True, "message": "unexpected JSON shape"}
    )


def _read_regular_file(
    path: Path, *, max_bytes: int = MAX_JSON_BYTES
) -> tuple[bytes, os.stat_result]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise FileNotFoundError(f"trusted file is unavailable: {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"trusted file is not regular: {path}")
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
            raise ValueError(f"trusted file owner or permissions are unsafe: {path}")
        if metadata.st_size > max_bytes:
            raise ValueError(f"trusted file exceeds {max_bytes} bytes: {path}")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > max_bytes:
            raise ValueError(f"trusted file exceeds {max_bytes} bytes: {path}")
        final_metadata = os.fstat(descriptor)
        fingerprint = lambda item: (
            item.st_dev,
            item.st_ino,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )
        if fingerprint(final_metadata) != fingerprint(metadata):
            raise ValueError(f"trusted file changed while being read: {path}")
        return content, final_metadata
    finally:
        os.close(descriptor)


def _read_json_file(path: Path) -> dict[str, Any]:
    last_error: Exception | None = None
    for _ in range(2):
        try:
            content, _metadata = _read_regular_file(path)
            parsed = _strict_json_loads(content)
            if not isinstance(parsed, dict):
                raise TypeError("paper state root must be an object")
            _safe, tree_valid = _sanitise_json_value(parsed)
            if not tree_valid:
                raise ValueError("paper state exceeds safe JSON limits")
            return parsed
        except (
            OSError,
            json.JSONDecodeError,
            RecursionError,
            TypeError,
            ValueError,
        ) as exc:
            last_error = exc
    raise ValueError("paper state could not be read consistently") from last_error


def _object_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _normalise_order(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _string_or_none(raw.get("id")),
        "client_order_id": _string_or_none(raw.get("client_order_id")),
        "symbol": _string_or_none(raw.get("symbol")),
        "side": _normalise_side(raw.get("side")),
        "size": _number_or_none(raw.get("size")),
        "filled_size": _number_or_none(raw.get("filled_size")),
        "order_type": _normalise_status(raw.get("order_type") or raw.get("type")),
        "price": _number_or_none(raw.get("price")),
        "stop_price": _number_or_none(raw.get("stop_price")),
        "trigger_signal": _normalise_status(raw.get("trigger_signal")),
        "reduce_only": _strict_bool(raw.get("reduce_only")),
        "leverage": _number_or_none(raw.get("leverage")),
        "status": _normalise_status(raw.get("status")),
        "created_at": _string_or_none(raw.get("created_at")),
        "updated_at": _string_or_none(raw.get("updated_at")),
    }


def _normalise_fill(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _string_or_none(raw.get("id")),
        "order_id": _string_or_none(raw.get("order_id")),
        "client_order_id": _string_or_none(raw.get("client_order_id")),
        "symbol": _string_or_none(raw.get("symbol")),
        "side": _normalise_side(raw.get("side")),
        "size": _number_or_none(raw.get("size")),
        "price": _number_or_none(raw.get("price")),
        "fee": _number_or_none(raw.get("fee")),
        "realized_pnl": _number_or_none(raw.get("realized_pnl")),
        "fill_type": _string_or_none(raw.get("fill_type")),
        "filled_at": _string_or_none(raw.get("filled_at")),
    }


def _state_object_list(
    state: dict[str, Any],
    key: str,
    errors: list[str],
) -> list[dict[str, Any]]:
    value = state.get(key)
    if not isinstance(value, list):
        errors.append(f"paper state {key} must be an array")
        return []
    objects = [item for item in value if isinstance(item, dict)]
    if len(objects) != len(value):
        errors.append(f"paper state {key} entries must be objects")
    return objects


def _valid_positive(value: Any) -> bool:
    parsed = _number_or_none(value)
    return parsed is not None and parsed > 0


def _valid_nonnegative(value: Any) -> bool:
    parsed = _number_or_none(value)
    return parsed is not None and parsed >= 0


def build_paper_snapshot(
    state: dict[str, Any],
    ticker_response: dict[str, Any],
    *,
    paper_source_valid: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a fail-closed, non-mutating paper snapshot from state + public market data."""
    validation_errors: list[str] = []
    source_state = state
    source_ticker_response = ticker_response
    source_positions_value = source_state.get("positions")
    source_positions = (
        [item for item in source_positions_value if isinstance(item, dict)]
        if isinstance(source_positions_value, list)
        else []
    )
    safe_state, state_tree_valid = _sanitise_json_value(state)
    state = safe_state if isinstance(safe_state, dict) else {}
    if not state_tree_valid:
        validation_errors.append("paper state contains unsafe JSON values or nesting")
    safe_ticker_response, ticker_tree_valid = _sanitise_json_value(ticker_response)
    ticker_response = (
        safe_ticker_response if isinstance(safe_ticker_response, dict) else {}
    )
    if not ticker_tree_valid:
        validation_errors.append("public ticker contains unsafe JSON values or nesting")
    current_time = None
    if now is not None:
        current_time = (
            now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
        )
    raw_positions = _state_object_list(state, "positions", validation_errors)
    raw_orders = _state_object_list(state, "open_orders", validation_errors)
    raw_fills = _state_object_list(state, "fills", validation_errors)
    history = _state_object_list(state, "history", validation_errors)

    starting_collateral_exact = _decimal_or_none(
        source_state.get("starting_collateral")
    )
    starting_collateral_value = _number_or_none(state.get("starting_collateral"))
    collateral_value = _number_or_none(state.get("collateral"))
    if starting_collateral_value is None or starting_collateral_value <= 0:
        validation_errors.append("paper state starting_collateral must be positive")
    if collateral_value is None or collateral_value < 0:
        validation_errors.append("paper state collateral must be nonnegative")
    currency = state.get("currency")
    if not isinstance(currency, str) or currency != "USD":
        validation_errors.append("paper state currency must be USD")
    if current_time is not None:
        for field in ("last_reconciled_at", "updated_at"):
            if not _fresh_timestamp(state.get(field), now=current_time):
                validation_errors.append(f"paper state {field} is unavailable or stale")

    raw_leverage_preferences = state.get("leverage_preferences")
    if not isinstance(raw_leverage_preferences, dict):
        validation_errors.append("paper state leverage_preferences must be an object")
        raw_leverage_preferences = {}
    leverage_preferences: dict[str, float] = {}
    for raw_symbol, raw_leverage in raw_leverage_preferences.items():
        symbol = raw_symbol if isinstance(raw_symbol, str) else ""
        leverage = _number_or_none(raw_leverage)
        if not _valid_futures_symbol(symbol) or leverage is None or leverage <= 0:
            validation_errors.append(
                f"paper state leverage preference is invalid for {symbol or 'unknown'}"
            )
            continue
        leverage_preferences[symbol] = leverage

    orders: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_orders):
        prefix = f"paper state open_orders[{index}]"
        if not _valid_identity(raw.get("id")):
            validation_errors.append(f"{prefix}.id must be a valid ASCII identity")
        if not _valid_futures_symbol(raw.get("symbol")):
            validation_errors.append(f"{prefix}.symbol is invalid")
        if _normalise_side(raw.get("side")) not in {"long", "short"}:
            validation_errors.append(f"{prefix}.side is invalid")
        if not _valid_positive(raw.get("size")):
            validation_errors.append(f"{prefix}.size must be positive")
        if not _valid_nonnegative(raw.get("filled_size")):
            validation_errors.append(f"{prefix}.filled_size must be nonnegative")
        if not isinstance(raw.get("reduce_only"), bool):
            validation_errors.append(f"{prefix}.reduce_only must be boolean")
        if not _valid_positive(raw.get("leverage")):
            validation_errors.append(f"{prefix}.leverage must be positive")
        orders.append(_normalise_order(raw))

    fills: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_fills):
        prefix = f"paper state fills[{index}]"
        if not _valid_identity(raw.get("id")):
            validation_errors.append(f"{prefix}.id must be a valid ASCII identity")
        if not _valid_identity(raw.get("order_id")):
            validation_errors.append(
                f"{prefix}.order_id must be a valid ASCII identity"
            )
        if not _valid_futures_symbol(raw.get("symbol")):
            validation_errors.append(f"{prefix}.symbol is invalid")
        if _normalise_side(raw.get("side")) not in {"long", "short"}:
            validation_errors.append(f"{prefix}.side is invalid")
        if not _valid_positive(raw.get("size")) or not _valid_positive(
            raw.get("price")
        ):
            validation_errors.append(f"{prefix} size and price must be positive")
        if not _valid_nonnegative(raw.get("fee")):
            validation_errors.append(f"{prefix}.fee must be nonnegative")
        if _parse_timestamp(raw.get("filled_at")) is None:
            validation_errors.append(f"{prefix}.filled_at timestamp is invalid")
        fills.append(_normalise_fill(raw))
    fills.sort(key=lambda item: str(item.get("filled_at") or ""), reverse=True)

    ticker = ticker_response.get("ticker")
    ticker = ticker if isinstance(ticker, dict) else {}
    ticker_symbol = (
        ticker.get("symbol") if isinstance(ticker.get("symbol"), str) else ""
    )
    source_ticker_value = source_ticker_response.get("ticker")
    source_ticker = source_ticker_value if isinstance(source_ticker_value, dict) else {}
    ticker_mark_exact = _decimal_or_none(source_ticker.get("markPrice"))
    ticker_mark = _number_or_none(ticker.get("markPrice"))
    ticker_index = _number_or_none(ticker.get("indexPrice"))
    ticker_success = ticker_response.get("result") == "success"
    ticker_timestamp_value = ticker_response.get("serverTime")
    ticker_fresh = _parse_timestamp(ticker_timestamp_value) is not None
    if current_time is not None:
        ticker_fresh = _fresh_timestamp(
            ticker_timestamp_value,
            now=current_time,
            max_age_seconds=MAX_TICKER_AGE_SECONDS,
        )

    positions: list[dict[str, Any]] = []
    position_sizes_exact: list[Decimal | None] = []
    position_inputs_valid = True
    expected_symbols: set[str] = set(leverage_preferences)
    for index, raw in enumerate(raw_positions):
        prefix = f"paper state positions[{index}]"
        symbol_value = raw.get("symbol")
        symbol: str = symbol_value if isinstance(symbol_value, str) else ""
        side = _normalise_side(raw.get("side"))
        size_value = _number_or_none(raw.get("size"))
        entry_value = _number_or_none(raw.get("entry_price"))
        leverage_value = _number_or_none(raw.get("leverage"))
        funding_value = _number_or_none(raw.get("unrealized_funding"))
        source_position = (
            source_positions[index] if index < len(source_positions) else {}
        )
        size_exact = _decimal_or_none(source_position.get("size"))
        position_sizes_exact.append(size_exact)
        row_valid = True
        if not _valid_futures_symbol(symbol):
            validation_errors.append(f"{prefix}.symbol is invalid")
            row_valid = False
        else:
            expected_symbols.add(symbol)
        if side not in {"long", "short"}:
            validation_errors.append(f"{prefix}.side is invalid")
            row_valid = False
        if size_value is None or size_value <= 0:
            validation_errors.append(f"{prefix}.size must be positive")
            row_valid = False
        if size_exact is None:
            validation_errors.append(
                f"{prefix}.size has no exact numeric representation"
            )
            row_valid = False
        if entry_value is None or entry_value <= 0:
            validation_errors.append(f"{prefix}.entry_price must be positive")
            row_valid = False
        if leverage_value is None or leverage_value <= 0:
            validation_errors.append(f"{prefix}.leverage must be positive")
            row_valid = False
        if funding_value is None:
            validation_errors.append(f"{prefix}.unrealized_funding must be finite")
            row_valid = False
        position_inputs_valid = position_inputs_valid and row_valid
        positions.append(
            {
                "symbol": symbol,
                "side": side,
                "size": size_value or 0.0,
                "entry_price": entry_value or 0.0,
                "mark_price": None,
                "index_price": None,
                "leverage": leverage_value or 0.0,
                "unrealized_funding": funding_value or 0.0,
                "unrealized_pnl": None,
                "notional_usd": None,
                "created_at": raw.get("created_at"),
                "updated_at": raw.get("updated_at"),
                "protected": False,
                "protective_order_ids": [],
            }
        )

    market_required = bool(expected_symbols)
    market_valid = (
        ticker_success
        and _valid_futures_symbol(ticker_symbol)
        and ticker_symbol in expected_symbols
        and ticker_mark is not None
        and ticker_mark > 0
        and ticker_mark_exact is not None
        and ticker_mark_exact > 0
        and ticker_index is not None
        and ticker_index > 0
        and ticker_fresh
    )
    if market_required and not market_valid:
        validation_errors.append(
            "public ticker is unavailable, stale, mismatched, or nonpositive"
        )

    total_unrealized = 0.0
    total_notional = 0.0
    total_notional_exact: Decimal | None = Decimal(0)
    if market_valid and position_inputs_valid:
        assert ticker_mark is not None
        assert ticker_index is not None
        assert ticker_mark_exact is not None
        for index, position in enumerate(positions):
            if position["symbol"] != ticker_symbol:
                validation_errors.append(
                    f"public ticker does not cover {position['symbol']}"
                )
                market_valid = False
                break
            direction = 1.0 if position["side"] == "long" else -1.0
            unrealized_pnl = (ticker_mark - position["entry_price"]) * position[
                "size"
            ] * direction + position["unrealized_funding"]
            notional = abs(ticker_mark * position["size"])
            size_exact = position_sizes_exact[index]
            exact_notional = (
                abs(ticker_mark_exact * size_exact) if size_exact is not None else None
            )
            if (
                not math.isfinite(unrealized_pnl)
                or not math.isfinite(notional)
                or notional > MAX_SAFE_INTEGER
                or exact_notional is None
                or exact_notional > MAX_SAFE_INTEGER
            ):
                validation_errors.append("paper position arithmetic overflowed")
                market_valid = False
                break
            total_unrealized += unrealized_pnl
            total_notional += notional
            assert total_notional_exact is not None
            total_notional_exact += exact_notional
            if (
                total_notional > MAX_SAFE_INTEGER
                or total_notional_exact > MAX_SAFE_INTEGER
            ):
                validation_errors.append("paper position arithmetic overflowed")
                market_valid = False
                break
            position["mark_price"] = _rounded(ticker_mark)
            position["index_price"] = _rounded(ticker_index)
            position["unrealized_pnl"] = _rounded(unrealized_pnl)
            position["notional_usd"] = _rounded(notional)
    if not market_valid:
        total_unrealized = 0.0
        total_notional = 0.0
        total_notional_exact = None
        for position in positions:
            position["mark_price"] = None
            position["index_price"] = None
            position["unrealized_pnl"] = None
            position["notional_usd"] = None

    candidate_orders: list[list[dict[str, Any]]] = []
    for position in positions:
        expected_stop_side = "short" if position["side"] == "long" else "long"
        candidates = [
            order
            for order in orders
            if order["symbol"] == position["symbol"]
            and _valid_identity(order["id"])
            and order["status"] == "open"
            and order["order_type"] == "stop"
            and order["side"] == expected_stop_side
            and order["reduce_only"] is True
            and order["trigger_signal"] == "mark"
            and order["price"] is None
            and _valid_protective_client_order_id(order["client_order_id"])
            and _numbers_equal(order["size"], position["size"])
            and _numbers_equal(order["filled_size"], 0.0)
            and _numbers_equal(order["leverage"], 1.0)
            and _valid_positive(order["stop_price"])
            and _stop_protects_position(position, _number(order["stop_price"]))
        ]
        candidate_orders.append(candidates)

    unique_candidate_ids = {
        str(candidates[0]["id"])
        for candidates in candidate_orders
        if len(candidates) == 1 and candidates[0].get("id")
    }
    exact_protection = (not positions and not orders) or (
        bool(positions)
        and len(orders) == len(positions)
        and all(len(candidates) == 1 for candidates in candidate_orders)
        and len(unique_candidate_ids) == len(positions)
    )
    for position, candidates in zip(positions, candidate_orders, strict=True):
        if exact_protection and len(candidates) == 1:
            position["protected"] = True
            position["protective_order_ids"] = [candidates[0]["id"]]

    snapshot_valid = not validation_errors and (not market_required or market_valid)
    financials_complete = (
        snapshot_valid
        and starting_collateral_value is not None
        and collateral_value is not None
    )
    equity = net_pnl = pnl_pct = fees = 0.0
    if financials_complete:
        assert starting_collateral_value is not None
        assert collateral_value is not None
        try:
            equity = collateral_value + total_unrealized
            net_pnl = equity - starting_collateral_value
            pnl_pct = (net_pnl / starting_collateral_value) * 100
            fees = math.fsum(
                fee
                for fill in fills
                if (fee := _number_or_none(fill.get("fee"))) is not None
            )
            derived = (equity, net_pnl, pnl_pct, total_unrealized, total_notional, fees)
            if not all(math.isfinite(value) for value in derived):
                raise OverflowError
        except (OverflowError, ValueError):
            validation_errors.append("paper account arithmetic overflowed")
            snapshot_valid = False
            financials_complete = False
    if financials_complete:
        equity_output: float | None = _rounded(equity)
        net_pnl_output: float | None = _rounded(net_pnl)
        pnl_pct_output: float | None = _rounded(pnl_pct)
        unrealized_output: float | None = _rounded(total_unrealized)
        exposure_output: float | None = total_notional
        fees_output: float | None = _rounded(fees)
    else:
        equity_output = None
        net_pnl_output = None
        pnl_pct_output = None
        unrealized_output = None
        exposure_output = None
        fees_output = None

    long_or_flat = snapshot_valid and all(
        position["side"] == "long" for position in positions
    )
    max_one_position = snapshot_valid and len(positions) <= 1
    exactly_one_x = (
        snapshot_valid
        and bool(leverage_preferences)
        and all(leverage == 1.0 for leverage in leverage_preferences.values())
        and all(position["leverage"] == 1.0 for position in positions)
    )
    protected = snapshot_valid and exact_protection

    return {
        "valid": snapshot_valid,
        "validation_errors": validation_errors,
        "mode": "futures_paper",
        "_contract_values": {
            "starting_collateral": str(starting_collateral_exact)
            if starting_collateral_exact is not None
            else None,
            "exposure_usd": str(total_notional_exact)
            if financials_complete and total_notional_exact is not None
            else None,
        },
        "account": {
            "currency": currency if isinstance(currency, str) else None,
            "starting_collateral": starting_collateral_value,
            "collateral": _rounded(collateral_value)
            if collateral_value is not None
            else None,
            "equity": equity_output,
            "net_pnl": net_pnl_output,
            "pnl_pct": pnl_pct_output,
            "unrealized_pnl": unrealized_output,
            "exposure_usd": exposure_output,
            "fees_paid": fees_output,
        },
        "market": {
            "symbol": ticker_symbol or (positions[0]["symbol"] if positions else None),
            "mark_price": _rounded(ticker_mark)
            if market_valid and ticker_mark is not None
            else None,
            "index_price": _rounded(ticker_index)
            if market_valid and ticker_index is not None
            else None,
            "server_time": _string_or_none(ticker_response.get("serverTime")),
        },
        "positions": positions,
        "orders": orders,
        "fills": fills,
        "history": history,
        "leverage_preferences": leverage_preferences,
        "last_reconciled_at": _string_or_none(state.get("last_reconciled_at")),
        "state_updated_at": _string_or_none(state.get("updated_at")),
        "protection": {
            "covered_positions": sum(position["protected"] for position in positions),
            "position_count": len(positions),
            "open_order_count": len(orders),
        },
        "compliance": {
            "long_or_flat": long_or_flat,
            "max_one_position": max_one_position,
            "exactly_one_x": exactly_one_x,
            "paper_only": snapshot_valid and paper_source_valid,
            "protected": protected,
        },
    }


def _release_from_ledger_path(value: str | None) -> str | None:
    if (
        not isinstance(value, str)
        or not _non_empty_text(value)
        or not value.startswith("/")
        or "\\" in value
    ):
        return None
    parts = value.split("/")
    path_parts = parts[1:]
    if (
        parts[0] != ""
        or any(
            not part or part in {".", ".."} or part != part.strip()
            for part in path_parts
        )
        or path_parts.count("releases") != 1
    ):
        return None
    index = path_parts.index("releases")
    if len(path_parts) != index + 4 or path_parts[index + 2] != "runtime":
        return None
    candidate = path_parts[index + 1]
    return candidate if _is_hex_digest(candidate, length=40) else None


def _parse_systemd_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    parts = value.strip().split()
    if len(parts) != 4:
        return None
    offsets = {
        "UTC": 0,
        "GMT": 0,
        "EST": -5,
        "EDT": -4,
        "CST": -6,
        "CDT": -5,
        "MST": -7,
        "MDT": -6,
        "PST": -8,
        "PDT": -7,
    }
    offset = offsets.get(parts[-1])
    if offset is None:
        return None
    try:
        return (
            datetime.strptime(
                " ".join(parts[:-1]),
                "%a %Y-%m-%d %H:%M:%S",
            )
            .replace(tzinfo=timezone(timedelta(hours=offset)))
            .astimezone(UTC)
        )
    except (OverflowError, ValueError):
        return None


def _open_trusted_directory_chain(path: Path) -> list[int]:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("trusted directory path must be absolute and canonical")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    trusted_owners = {0, os.getuid()}
    descriptors: list[int] = []
    try:
        for index, component in enumerate(path.parts):
            if index == 0:
                descriptor = os.open(component, flags)
            else:
                if component in {"", ".", ".."}:
                    raise ValueError("trusted directory path is not canonical")
                descriptor = os.open(component, flags, dir_fd=descriptors[-1])
            descriptors.append(descriptor)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid not in trusted_owners
                or metadata.st_mode & 0o022
            ):
                raise ValueError("trusted directory owner or permissions are unsafe")
        return descriptors
    except (OSError, ValueError):
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def _kill_switch_status(value: str | None) -> tuple[bool, str | None]:
    if not value:
        return True, "service does not expose a kill-switch path"
    try:
        path = _absolute_environment_path(value, label="kill-switch")
    except ValueError:
        return True, "kill-switch path is malformed or cannot be resolved"
    if not path.name:
        return True, "kill-switch path is malformed or cannot be resolved"
    try:
        parent_descriptors = _open_trusted_directory_chain(path.parent)
    except (OSError, ValueError):
        return True, "kill-switch parent directory cannot be safely inspected"
    try:
        parent_descriptor = parent_descriptors[-1]
        try:
            metadata = os.stat(
                path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return False, None
        except (OSError, ValueError):
            return True, "kill-switch path cannot be safely inspected"
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid not in {0, os.getuid()}
            or metadata.st_mode & 0o022
        ):
            return True, "kill-switch file owner, type, or permissions are unsafe"
        return True, None
    finally:
        for descriptor in reversed(parent_descriptors):
            os.close(descriptor)


def build_automation_status(
    service: dict[str, str],
    timer: dict[str, str],
    environment: dict[str, str],
    *,
    kill_switch_armed: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = datetime.now(UTC) if now is None else now
    current_time = (
        current_time.astimezone(UTC)
        if current_time.tzinfo is not None
        else current_time.replace(tzinfo=UTC)
    )
    timer_enabled = timer.get("UnitFileState") == "enabled"
    timer_active = timer.get("ActiveState") == "active"
    service_success = service.get("Result") == "success" and service.get(
        "ExecMainStatus"
    ) in {"", "0"}
    execution_enabled = environment.get("PAPER_EXECUTE") == "1"
    service_started = _parse_systemd_timestamp(service.get("ExecMainStartTimestamp"))
    service_finished = _parse_systemd_timestamp(service.get("ExecMainExitTimestamp"))
    last_trigger = _parse_systemd_timestamp(timer.get("LastTriggerUSec"))
    next_trigger = _parse_systemd_timestamp(timer.get("NextElapseUSecRealtime"))
    next_trigger_delta = (
        (next_trigger - current_time).total_seconds()
        if next_trigger is not None
        else None
    )
    timestamps_fresh = (
        service_started is not None
        and service_finished is not None
        and service_started <= service_finished
        and _fresh_timestamp(service_started.isoformat(), now=current_time)
        and _fresh_timestamp(service_finished.isoformat(), now=current_time)
        and _fresh_timestamp(
            last_trigger.isoformat() if last_trigger is not None else None,
            now=current_time,
        )
        and next_trigger_delta is not None
        and -MAX_CLOCK_SKEW_SECONDS <= next_trigger_delta <= MAX_CYCLE_AGE_SECONDS
    )
    release = _release_from_ledger_path(environment.get("LEDGER_PATH"))
    healthy = (
        timer_enabled
        and timer_active
        and service_success
        and execution_enabled
        and timestamps_fresh
        and not kill_switch_armed
        and release is not None
    )
    return {
        "healthy": healthy,
        "read_only_dashboard": True,
        "service_unit": SERVICE_UNIT,
        "timer_unit": TIMER_UNIT,
        "service_active_state": service.get("ActiveState"),
        "service_sub_state": service.get("SubState"),
        "service_result": service.get("Result"),
        "service_exit_status": service.get("ExecMainStatus"),
        "last_cycle_started_at": service.get("ExecMainStartTimestamp"),
        "last_cycle_finished_at": service.get("ExecMainExitTimestamp"),
        "timer_enabled": timer_enabled,
        "timer_active": timer_active,
        "last_trigger": timer.get("LastTriggerUSec"),
        "next_trigger": timer.get("NextElapseUSecRealtime"),
        "execution_enabled": execution_enabled,
        "kill_switch_armed": kill_switch_armed,
        "timestamps_fresh": timestamps_fresh,
        "release": release,
    }


def _stat_fingerprint(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _ledger_sidecar_fingerprint(path: Path) -> tuple[Any, ...]:
    fingerprint: list[Any] = []
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        try:
            metadata = os.lstat(sidecar)
        except FileNotFoundError:
            fingerprint.append((suffix, None))
            continue
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o022
        ):
            raise ValueError(
                f"ledger sidecar owner or permissions are unsafe: {sidecar}"
            )
        if suffix == "-wal" and metadata.st_size > 0:
            raise ValueError(f"ledger has uncheckpointed WAL data: {sidecar}")
        fingerprint.append((suffix, _stat_fingerprint(metadata)))
    return tuple(fingerprint)


def read_ledger_summary(path: Path, *, limit: int = 12) -> dict[str, Any]:
    """Read and independently verify a stable append-only bot ledger."""
    sidecars_before = _ledger_sidecar_fingerprint(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise FileNotFoundError(f"ledger is unavailable: {path}") from exc
    connection: sqlite3.Connection | None = None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o022
        ):
            raise ValueError(f"ledger owner or permissions are unsafe: {path}")
        uri = f"file:/proc/self/fd/{descriptor}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        recent: deque[dict[str, Any]] = deque(maxlen=max(1, min(limit, 50)))
        expected_previous = GENESIS_HASH
        chain_valid = True
        invalid_event_id: str | None = None
        event_count = 0
        last_event_at: str | None = None
        connection.execute("PRAGMA query_only=ON")
        quick_check_row = connection.execute("PRAGMA quick_check").fetchone()
        expected_schema = {
            "sequence": ("INTEGER", 0, 1),
            "event_id": ("TEXT", 1, 0),
            "event_type": ("TEXT", 1, 0),
            "occurred_at": ("TEXT", 1, 0),
            "payload_json": ("TEXT", 1, 0),
            "previous_hash": ("TEXT", 1, 0),
            "event_hash": ("TEXT", 1, 0),
        }
        event_schema = {
            str(row["name"]): (
                str(row["type"]).strip().upper(),
                int(row["notnull"]),
                int(row["pk"]),
            )
            for row in connection.execute("PRAGMA table_info(events)")
        }
        if event_schema != expected_schema:
            raise ValueError("ledger events table schema is invalid")
        for expected_sequence, row in enumerate(
            connection.execute("SELECT * FROM events ORDER BY sequence"),
            1,
        ):
            event_count = expected_sequence
            try:
                sequence = row["sequence"]
                event_id = row["event_id"]
                event_type = row["event_type"]
                occurred_at = row["occurred_at"]
                payload_json = row["payload_json"]
                previous_hash = row["previous_hash"]
                event_hash = row["event_hash"]
                if (
                    isinstance(sequence, bool)
                    or not isinstance(sequence, int)
                    or not all(
                        isinstance(value, str)
                        for value in (
                            event_id,
                            event_type,
                            occurred_at,
                            payload_json,
                            previous_hash,
                            event_hash,
                        )
                    )
                    or not _valid_identity(event_id)
                    or not _valid_identity(event_type)
                    or not _is_hex_digest(previous_hash, length=64)
                    or not _is_hex_digest(event_hash, length=64)
                ):
                    raise ValueError("ledger event row types are invalid")
                payload = _strict_json_loads(payload_json)
                payload, payload_valid = _sanitise_json_value(payload)
                if not payload_valid or _parse_timestamp(occurred_at) is None:
                    raise ValueError("ledger event contains unsafe data")
                material = json.dumps(
                    {
                        "event_id": event_id,
                        "event_type": event_type,
                        "occurred_at": occurred_at,
                        "payload": payload,
                        "previous_hash": expected_previous,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                expected_hash = hashlib.sha256(material.encode()).hexdigest()
                if (
                    sequence != expected_sequence
                    or previous_hash != expected_previous
                    or event_hash != expected_hash
                ):
                    chain_valid = False
                    if invalid_event_id is None:
                        invalid_event_id = event_id
                expected_previous = event_hash
                last_event_at = occurred_at
                recent.append(
                    {
                        "sequence": sequence,
                        "event_id": event_id,
                        "event_type": event_type,
                        "occurred_at": occurred_at,
                        "payload": payload,
                        "event_hash": event_hash,
                    }
                )
            except (
                IndexError,
                KeyError,
                OverflowError,
                RecursionError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ):
                chain_valid = False
                if invalid_event_id is None:
                    try:
                        invalid_event_id = str(row["event_id"])
                    except (IndexError, KeyError, TypeError):
                        invalid_event_id = "unknown"
        final_metadata = os.fstat(descriptor)
    finally:
        if connection is not None:
            connection.close()
        os.close(descriptor)
    sidecars_after = _ledger_sidecar_fingerprint(path)
    if _stat_fingerprint(final_metadata) != _stat_fingerprint(metadata):
        raise ValueError("ledger changed while being read")
    if sidecars_after != sidecars_before:
        raise ValueError("ledger sidecars changed while being read")

    return {
        "quick_check": str(quick_check_row[0]) if quick_check_row else "unknown",
        "chain_valid": chain_valid,
        "event_count": event_count,
        "head_hash": expected_previous,
        "last_event_at": last_event_at,
        "invalid_event_id": invalid_event_id,
        "recent_events": list(reversed(recent)),
    }


def ledger_is_healthy(ledger: dict[str, Any], *, now: datetime | None = None) -> bool:
    structurally_healthy = (
        ledger.get("quick_check") == "ok"
        and ledger.get("chain_valid") is True
        and _number_or_none(ledger.get("event_count")) is not None
        and _number(ledger.get("event_count")) > 0
    )
    if not structurally_healthy or now is None:
        return structurally_healthy
    current_time = (
        now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
    )
    return _fresh_timestamp(ledger.get("last_event_at"), now=current_time)


def parse_journal_cycles(
    output: str,
    *,
    limit: int = 8,
    validation_errors: list[str] | None = None,
) -> list[dict[str, Any]]:
    cycles: list[dict[str, Any]] = []

    def reject(message: str) -> None:
        if validation_errors is not None:
            validation_errors.append(f"service journal: {message}")

    for line in output.splitlines():
        try:
            record = _strict_json_loads(line)
            record, record_valid = _sanitise_json_value(record)
        except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
            if line.strip():
                reject("record is malformed")
            continue
        if not record_valid:
            reject("record contains unsafe data")
            continue
        if not isinstance(record, dict):
            continue
        message = record.get("MESSAGE")
        if not isinstance(message, str) or not message.lstrip().startswith("{"):
            continue
        try:
            payload = _strict_json_loads(message)
            payload, payload_valid = _sanitise_json_value(payload)
        except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
            reject("structured cycle payload is malformed")
            continue
        if not payload_valid:
            reject("structured cycle payload is not an object")
            continue
        if not isinstance(payload, dict):
            continue
        cycle_schema_keys = {
            "action",
            "blockers",
            "client_order_id",
            "decision",
            "notional_usd",
            "size",
        }
        if not cycle_schema_keys.intersection(payload):
            continue
        action = payload.get("action")
        blockers = payload.get("blockers")
        if not _valid_identity(action):
            reject("structured cycle action is invalid")
            continue
        if not isinstance(blockers, list) or any(
            not isinstance(blocker, str) for blocker in blockers
        ):
            reject("structured cycle blockers are invalid")
            continue
        timestamp = record.get("__REALTIME_TIMESTAMP")
        if (
            not isinstance(timestamp, str)
            or not timestamp.isascii()
            or not timestamp.isdecimal()
            or not (1 <= len(timestamp) <= 20)
        ):
            reject("structured cycle timestamp is invalid")
            continue
        try:
            occurred_at = datetime.fromtimestamp(
                int(timestamp) / 1_000_000, tz=UTC
            ).isoformat()
        except (OverflowError, TypeError, ValueError, OSError):
            reject("structured cycle timestamp is invalid")
            continue
        cycles.append({**payload, "occurred_at": occurred_at})
    return list(reversed(cycles[-max(1, min(limit, 25)) :]))


def _experiment_metadata(
    runtime_path: Path,
    *,
    ledger_path: Path | None = None,
) -> dict[str, Any]:
    experiment_root = runtime_path / "forward-experiments"
    if not experiment_root.is_dir():
        return {}
    release_commit = runtime_path.parent.name
    if not _is_hex_digest(release_commit, length=40):
        return {}
    expected_ledger = (
        ledger_path
        if ledger_path is not None
        else runtime_path / "paper-ledger.sqlite3"
    ).resolve(strict=False)
    candidates: list[tuple[int, bytes, dict[str, Any]]] = []
    for path in experiment_root.glob("*.json"):
        try:
            content, metadata = _read_regular_file(path)
            payload = _strict_json_loads(content)
            payload, payload_valid = _sanitise_json_value(payload)
            if not payload_valid:
                continue
            if not isinstance(payload, dict) or payload.get("schema_version") != 1:
                continue
            experiment = payload.get("experiment")
            git = payload.get("git")
            contract = payload.get("contract")
            ledger = payload.get("ledger")
            if not all(
                isinstance(item, dict) for item in (experiment, git, contract, ledger)
            ):
                continue
            assert isinstance(experiment, dict)
            assert isinstance(git, dict)
            assert isinstance(contract, dict)
            assert isinstance(ledger, dict)
            start_at = experiment.get("start_at")
            ledger_path = ledger.get("path")
            allowed_sides = contract.get("allowed_sides")
            if (
                not _valid_identity(experiment.get("experiment_id"))
                or _parse_timestamp(start_at) is None
                or git.get("commit") != release_commit
                or not _is_hex_digest(git.get("tree"), length=40)
                or contract.get("mode") != "futures_paper"
                or not isinstance(contract.get("symbol"), str)
                or not str(contract["symbol"]).startswith("PF_")
                or _number_or_none(contract.get("account_baseline_usd")) is None
                or _number(contract.get("account_baseline_usd")) <= 0
                or _number_or_none(contract.get("max_notional_usd")) is None
                or _number(contract.get("max_notional_usd")) <= 0
                or _number(contract.get("leverage")) != 1.0
                or allowed_sides != ["long"]
                or not isinstance(ledger_path, str)
                or Path(ledger_path).expanduser().resolve(strict=False)
                != expected_ledger
            ):
                continue
            candidates.append((metadata.st_mtime_ns, content, payload))
        except (
            OSError,
            RuntimeError,
            json.JSONDecodeError,
            RecursionError,
            TypeError,
            ValueError,
        ):
            continue
    if not candidates:
        return {}
    _, content, payload = max(candidates, key=lambda item: item[0])
    experiment = payload["experiment"]
    git = payload["git"]
    contract = payload["contract"]
    assert isinstance(experiment, dict)
    assert isinstance(git, dict)
    assert isinstance(contract, dict)
    return {
        "experiment_id": experiment.get("experiment_id"),
        "seal_file_sha256": hashlib.sha256(content).hexdigest(),
        "start_at": experiment.get("start_at"),
        "started_at": experiment.get("start_at"),
        "commit": git.get("commit"),
        "tree": git.get("tree"),
        "symbol": contract.get("symbol"),
        "account_baseline_usd": contract.get("account_baseline_usd"),
        "max_notional_usd": contract.get("max_notional_usd"),
        "leverage": contract.get("leverage"),
        "allowed_sides": contract.get("allowed_sides"),
        "ledger_path": str(expected_ledger),
        "provenance_valid": True,
    }


def _experiment_contract_errors(
    experiment: dict[str, Any],
    paper: dict[str, Any],
    ledger: dict[str, Any],
    *,
    now: datetime,
) -> list[str]:
    errors: list[str] = []
    current_time_ns = _datetime_nanoseconds(now)
    contract_symbol = _string_or_none(experiment.get("symbol"))
    market_value = paper.get("market")
    market = market_value if isinstance(market_value, dict) else {}
    preferences_value = paper.get("leverage_preferences")
    preferences = preferences_value if isinstance(preferences_value, dict) else {}
    observed_symbols = {
        symbol
        for symbol in [
            market.get("symbol"),
            *[
                position.get("symbol")
                for position in _object_list(paper.get("positions"))
            ],
            *[symbol for symbol in preferences],
        ]
        if isinstance(symbol, str) and symbol
    }
    if not _valid_futures_symbol(contract_symbol) or observed_symbols != {
        contract_symbol
    }:
        errors.append("experiment symbol does not match the paper snapshot")

    account_value = paper.get("account")
    account = account_value if isinstance(account_value, dict) else {}
    contract_values_value = paper.get("_contract_values")
    contract_values = (
        contract_values_value if isinstance(contract_values_value, dict) else {}
    )
    baseline_source = (
        contract_values.get("starting_collateral")
        if contract_values
        else account.get("starting_collateral")
    )
    baseline = _contract_decimal_or_none(baseline_source)
    sealed_baseline = _decimal_or_none(experiment.get("account_baseline_usd"))
    if not _numbers_equal(baseline, sealed_baseline):
        errors.append("experiment account baseline does not match the paper snapshot")

    exposure_source = (
        contract_values.get("exposure_usd")
        if contract_values
        else account.get("exposure_usd")
    )
    exposure = _contract_decimal_or_none(exposure_source, derived=True)
    max_notional = _decimal_or_none(experiment.get("max_notional_usd"))
    if exposure is None or max_notional is None or exposure > max_notional:
        errors.append("paper exposure exceeds experiment max_notional_usd")

    sealed_leverage = _number_or_none(experiment.get("leverage"))

    position_leverages = [
        position.get("leverage") for position in _object_list(paper.get("positions"))
    ]
    if (
        sealed_leverage is None
        or not preferences
        or any(
            not _numbers_equal(value, sealed_leverage) for value in preferences.values()
        )
        or any(
            not _numbers_equal(value, sealed_leverage) for value in position_leverages
        )
    ):
        errors.append("paper leverage does not match the experiment contract")

    allowed_sides = experiment.get("allowed_sides")
    position_sides = [
        position.get("side") for position in _object_list(paper.get("positions"))
    ]
    if not isinstance(allowed_sides, list) or any(
        side not in allowed_sides for side in position_sides
    ):
        errors.append("paper position side violates the experiment contract")

    started_at = _parse_timestamp_exact(
        experiment.get("started_at") or experiment.get("start_at")
    )
    reconciled_at = _parse_timestamp_exact(paper.get("last_reconciled_at"))
    last_event_at = _parse_timestamp_exact(ledger.get("last_event_at"))
    clock_skew_ns = MAX_CLOCK_SKEW_SECONDS * NANOSECONDS_PER_SECOND
    if (
        current_time_ns is None
        or started_at is None
        or started_at[1] > current_time_ns + clock_skew_ns
    ):
        errors.append("experiment start time is invalid or in the future")
    elif (
        reconciled_at is None
        or reconciled_at[1] + clock_skew_ns < started_at[1]
        or last_event_at is None
        or last_event_at[1] + clock_skew_ns < started_at[1]
    ):
        errors.append("paper state or ledger predates the experiment")
    return errors


def _resolve_kraken_cli(environment: dict[str, str]) -> str | None:
    configured = environment.get("KRAKEN_CLI")
    if configured:
        try:
            path = _absolute_environment_path(configured, label="kraken-cli")
        except ValueError:
            return None
        return _trusted_executable(path)
    for directory in MINIMAL_PATH.split(":"):
        resolved = _trusted_executable(Path(directory) / "kraken-cli")
        if resolved:
            return resolved
    return None


def _resolve_state_path(
    service_environment: dict[str, str],
    process_environment: Mapping[str, str] | None = None,
) -> Path:
    environment = os.environ if process_environment is None else process_environment
    configured = service_environment.get("KRAKEN_PAPER_STATE_PATH") or environment.get(
        "KRAKEN_PAPER_STATE_PATH"
    )
    return (
        _absolute_environment_path(configured, label="paper state")
        if configured
        else DEFAULT_PAPER_STATE
    )


@router.get("/paper-dashboard")
async def paper_dashboard() -> dict[str, Any]:
    """Complete read-only view of the automated futures-paper experiment."""
    generated_at = datetime.now(UTC)
    systemctl = _resolve_system_binary("systemctl")
    journalctl = _resolve_system_binary("journalctl")
    if systemctl is None:
        service_result = timer_result = {
            "ok": False,
            "stdout": "",
            "message": "trusted systemctl is unavailable",
        }
    else:
        service_command = [
            systemctl,
            "--user",
            "show",
            SERVICE_UNIT,
            "--property=ActiveState,SubState,Result,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp,Environment",
            "--no-pager",
        ]
        timer_command = [
            systemctl,
            "--user",
            "show",
            TIMER_UNIT,
            "--property=ActiveState,UnitFileState,LastTriggerUSec,NextElapseUSecRealtime",
            "--no-pager",
        ]
        service_result, timer_result = await asyncio.gather(
            _run_text(service_command),
            _run_text(timer_command),
        )
    service = _parse_properties(service_result["stdout"])
    timer = _parse_properties(timer_result["stdout"])
    environment, environment_error = _parse_environment(service.get("Environment", ""))

    errors: list[str] = []
    if environment_error:
        errors.append(environment_error)
    if not service_result["ok"]:
        errors.append(f"service status: {service_result['message']}")
    if not timer_result["ok"]:
        errors.append(f"timer status: {timer_result['message']}")

    kill_switch_value = environment.get("KILL_SWITCH_PATH")
    kill_switch_armed, kill_switch_error = _kill_switch_status(kill_switch_value)
    if kill_switch_error:
        errors.append(kill_switch_error)
    automation = build_automation_status(
        service,
        timer,
        environment,
        kill_switch_armed=kill_switch_armed,
        now=generated_at,
    )

    state_loaded = False
    try:
        state_path = _resolve_state_path(environment)
        state = await asyncio.to_thread(_read_json_file, state_path)
        state_loaded = True
    except (OSError, RuntimeError, ValueError) as exc:
        state = {}
        errors.append(f"paper state unavailable: {exc}")

    symbols = [
        position["symbol"]
        for position in _object_list(state.get("positions"))
        if _valid_futures_symbol(position.get("symbol"))
    ]
    if not symbols:
        raw_leverage_preferences = state.get("leverage_preferences")
        if isinstance(raw_leverage_preferences, dict):
            symbols = [
                symbol
                for symbol in raw_leverage_preferences
                if _valid_futures_symbol(symbol)
            ]
    kraken_cli = _resolve_kraken_cli(environment)
    ticker_response: dict[str, Any] = {}
    if kraken_cli and symbols:
        ticker_response = await _run_json(
            [kraken_cli, "futures", "ticker", symbols[0], "--output", "json"]
        )
        if ticker_response.get("error"):
            errors.append(f"public futures ticker: {ticker_response.get('message')}")
            ticker_response = {}
    elif symbols:
        errors.append("kraken-cli is unavailable for public mark prices")

    paper = build_paper_snapshot(
        state,
        ticker_response,
        paper_source_valid=state_loaded,
        now=generated_at,
    )
    errors.extend(f"paper snapshot: {error}" for error in paper["validation_errors"])
    ledger_path_value = environment.get("LEDGER_PATH")
    ledger: dict[str, Any] = {"healthy": False}
    ledger_healthy = False
    experiment: dict[str, Any] = {}
    if ledger_path_value:
        try:
            ledger_path = _absolute_environment_path(ledger_path_value, label="ledger")
            ledger = await asyncio.to_thread(read_ledger_summary, ledger_path)
            ledger_healthy = ledger_is_healthy(ledger, now=generated_at)
            ledger["healthy"] = ledger_healthy
            if not ledger_healthy:
                errors.append("paper ledger integrity or freshness validation failed")
            experiment = await asyncio.to_thread(
                _experiment_metadata,
                ledger_path.parent,
                ledger_path=ledger_path,
            )
            if not experiment:
                errors.append("experiment provenance seal is unavailable or invalid")
        except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
            errors.append(f"ledger: {exc}")
    else:
        errors.append("service does not expose a ledger path")
    if experiment:
        contract_errors = _experiment_contract_errors(
            experiment,
            paper,
            ledger,
            now=generated_at,
        )
        if automation.get("release") != experiment.get("commit"):
            contract_errors.append(
                "automation release does not match sealed experiment commit"
            )
        experiment["contract_valid"] = not contract_errors
        errors.extend(f"experiment contract: {error}" for error in contract_errors)
    paper.pop("_contract_values", None)

    journal_result = (
        await _run_text(
            [
                journalctl,
                "--user",
                "-u",
                SERVICE_UNIT,
                "-n",
                "80",
                "--no-pager",
                "-o",
                "json",
            ]
        )
        if journalctl is not None
        else {
            "ok": False,
            "stdout": "",
            "message": "trusted journalctl is unavailable",
        }
    )
    if journal_result["ok"]:
        journal_validation_errors: list[str] = []
        cycles = parse_journal_cycles(
            journal_result["stdout"],
            validation_errors=journal_validation_errors,
        )
        errors.extend(journal_validation_errors)
    else:
        cycles = []
        errors.append(f"service journal: {journal_result['message']}")

    compliance_ok = all(paper["compliance"].values())
    response = {
        "generated_at": generated_at.isoformat(),
        "read_only": True,
        "source": "paper state file + public Kraken futures ticker; no authenticated polling",
        "healthy": automation["healthy"]
        and compliance_ok
        and ledger_healthy
        and not errors,
        "automation": automation,
        "experiment": experiment,
        "paper": paper,
        "ledger": ledger,
        "cycles": cycles,
        "errors": errors,
    }
    safe_response, response_valid = _sanitise_json_value(response)
    if not isinstance(safe_response, dict):
        return {
            "generated_at": generated_at.isoformat(),
            "read_only": True,
            "healthy": False,
            "errors": ["dashboard response could not be safely serialized"],
        }
    if not response_valid:
        safe_response["healthy"] = False
        safe_errors = safe_response.get("errors")
        if not isinstance(safe_errors, list):
            safe_errors = []
            safe_response["errors"] = safe_errors
        safe_errors.append("dashboard response contained unsafe values")
    return safe_response


@router.get("/positions")
async def positions() -> dict[str, Any]:
    """Backwards-compatible, non-mutating paper position response."""
    dashboard = await paper_dashboard()
    paper = dashboard["paper"]
    return {
        "mode": paper["mode"],
        "count": len(paper["positions"]),
        "positions": paper["positions"],
        "last_reconciled_at": paper["last_reconciled_at"],
        "error": not dashboard["healthy"],
        "errors": dashboard["errors"],
    }


@router.get("/summary")
async def summary() -> dict[str, Any]:
    """Legacy summary envelope plus the complete paper dashboard."""
    dashboard = await paper_dashboard()
    paper = dashboard["paper"]
    return {
        "balance": await balance(),
        "positions": {
            "error": not dashboard["healthy"],
            "message": "; ".join(dashboard["errors"]) if dashboard["errors"] else None,
            "positions": paper["positions"],
        },
        "paper_dashboard": dashboard,
    }


@router.get("/balance", deprecated=True)
async def balance() -> dict[str, Any]:
    """Inert compatibility route; private Kraken balance access is retired."""
    return {
        "error": True,
        "retired": True,
        "message": "Live Kraken balance access was retired; this backend is paper-only.",
    }


@router.get("/trades", deprecated=True)
async def trades() -> dict[str, Any]:
    """Inert compatibility route; authenticated trade-history access is retired."""
    return {
        "error": True,
        "retired": True,
        "message": "Authenticated Kraken trade-history access was retired; this backend is paper-only.",
    }
