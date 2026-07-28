import asyncio
import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest import mock

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "backend" / "dashboard" / "plugin_api.py"
)
SPEC = importlib.util.spec_from_file_location(
    "trading_dashboard_plugin_api", MODULE_PATH
)
assert SPEC is not None
plugin_api = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(plugin_api)


def test_build_paper_snapshot_calculates_pnl_and_validates_protection():
    state = {
        "starting_collateral": 400.0,
        "collateral": 399.95,
        "currency": "USD",
        "last_reconciled_at": "2026-07-27T20:00:00+00:00",
        "positions": [
            {
                "symbol": "PF_ETHUSD",
                "side": "Long",
                "size": 0.1,
                "entry_price": 1900.0,
                "leverage": 1.0,
                "unrealized_funding": 0.0,
            }
        ],
        "open_orders": [
            {
                "id": "FP-00003",
                "symbol": "PF_ETHUSD",
                "side": "Short",
                "size": 0.1,
                "filled_size": 0.0,
                "order_type": "Stop",
                "stop_price": 1710.0,
                "trigger_signal": "Mark",
                "client_order_id": "ethbot-stop-20260727T120000Z",
                "reduce_only": True,
                "leverage": 1.0,
                "status": "Open",
            }
        ],
        "fills": [],
        "history": [],
        "leverage_preferences": {"PF_ETHUSD": 1.0},
    }
    ticker = {
        "result": "success",
        "serverTime": "2026-07-27T20:01:00Z",
        "ticker": {"symbol": "PF_ETHUSD", "markPrice": 1950.0, "indexPrice": 1949.5},
    }

    result = plugin_api.build_paper_snapshot(state, ticker)

    assert result["account"]["equity"] == 404.95
    assert result["account"]["net_pnl"] == 4.95
    assert result["positions"][0]["unrealized_pnl"] == 5.0
    assert result["positions"][0]["notional_usd"] == 195.0
    assert result["orders"][0]["stop_price"] == 1710.0
    assert result["compliance"] == {
        "long_or_flat": True,
        "max_one_position": True,
        "exactly_one_x": True,
        "paper_only": True,
        "protected": True,
    }
    assert result["protection"]["covered_positions"] == 1


def test_build_paper_snapshot_flags_short_leveraged_unprotected_position():
    state = {
        "starting_collateral": 400.0,
        "collateral": 400.0,
        "currency": "USD",
        "positions": [
            {
                "symbol": "PF_ETHUSD",
                "side": "Short",
                "size": 0.1,
                "entry_price": 1900.0,
                "leverage": 2.0,
                "unrealized_funding": 0.0,
            }
        ],
        "open_orders": [],
        "fills": [],
        "history": [],
        "leverage_preferences": {"PF_ETHUSD": 2.0},
    }
    ticker = {
        "result": "success",
        "ticker": {"symbol": "PF_ETHUSD", "markPrice": 1950.0, "indexPrice": 1949.5},
    }

    result = plugin_api.build_paper_snapshot(state, ticker)

    assert result["compliance"]["long_or_flat"] is False
    assert result["compliance"]["exactly_one_x"] is False
    assert result["compliance"]["protected"] is False


def test_build_paper_snapshot_treats_malformed_collections_as_empty():
    state = {
        "starting_collateral": 400.0,
        "collateral": 400.0,
        "positions": None,
        "open_orders": "not-a-list",
        "fills": {},
        "history": 42,
        "leverage_preferences": [],
    }

    result = plugin_api.build_paper_snapshot(state, {})

    assert result["positions"] == []
    assert result["orders"] == []
    assert result["fills"] == []
    assert result["history"] == []
    assert result["leverage_preferences"] == {}
    assert result["valid"] is False
    assert result["validation_errors"]
    assert result["compliance"]["paper_only"] is False
    assert result["compliance"]["protected"] is False


def test_build_paper_snapshot_does_not_fabricate_totals_without_a_mark():
    state = {
        "starting_collateral": 400.0,
        "collateral": 399.95,
        "currency": "USD",
        "positions": [
            {
                "symbol": "PF_ETHUSD",
                "side": "Long",
                "size": 0.1,
                "entry_price": 1900.0,
                "leverage": 1.0,
                "unrealized_funding": 0.0,
            }
        ],
        "open_orders": [],
        "fills": [],
        "history": [],
        "leverage_preferences": {"PF_ETHUSD": 1.0},
    }

    result = plugin_api.build_paper_snapshot(state, {})

    assert result["account"]["equity"] is None
    assert result["account"]["net_pnl"] is None
    assert result["account"]["pnl_pct"] is None
    assert result["account"]["unrealized_pnl"] is None
    assert result["account"]["exposure_usd"] is None
    assert result["valid"] is False
    assert any("ticker" in error for error in result["validation_errors"])


def test_build_paper_snapshot_rejects_mismatched_nonpositive_ticker():
    state = {
        "starting_collateral": 400.0,
        "collateral": 400.0,
        "currency": "USD",
        "positions": [
            {
                "symbol": "PF_ETHUSD",
                "side": "Long",
                "size": 0.1,
                "entry_price": 1900.0,
                "leverage": 1.0,
                "unrealized_funding": 0.0,
            }
        ],
        "open_orders": [],
        "fills": [],
        "history": [],
        "leverage_preferences": {"PF_ETHUSD": 1.0},
    }
    ticker = {
        "result": "success",
        "ticker": {"symbol": "PF_XBTUSD", "markPrice": 0, "indexPrice": -1},
    }

    result = plugin_api.build_paper_snapshot(state, ticker)

    assert result["market"]["mark_price"] is None
    assert result["market"]["index_price"] is None
    assert result["valid"] is False


def test_build_paper_snapshot_requires_exact_protective_order_contract():
    valid_order = {
        "id": "FP-stop",
        "client_order_id": "ethbot-stop-20260727T120000Z",
        "symbol": "PF_ETHUSD",
        "side": "Short",
        "size": 0.1,
        "filled_size": 0.0,
        "order_type": "Stop",
        "price": None,
        "stop_price": 1710.0,
        "trigger_signal": "Mark",
        "reduce_only": True,
        "leverage": 1.0,
        "status": "Open",
    }
    base_state = {
        "starting_collateral": 400.0,
        "collateral": 400.0,
        "currency": "USD",
        "positions": [
            {
                "symbol": "PF_ETHUSD",
                "side": "Long",
                "size": 0.1,
                "entry_price": 1900.0,
                "leverage": 1.0,
                "unrealized_funding": 0.0,
            }
        ],
        "fills": [],
        "history": [],
        "leverage_preferences": {"PF_ETHUSD": 1.0},
    }
    ticker = {
        "result": "success",
        "ticker": {"symbol": "PF_ETHUSD", "markPrice": 1950.0, "indexPrice": 1949.5},
    }
    invalid_variants = (
        {"id": " \t "},
        {"reduce_only": "false"},
        {"trigger_signal": "Index"},
        {"filled_size": 0.01},
        {"filled_size": 5e-13},
        {"leverage": 9.0},
        {"leverage": 1.0000000005},
        {"size": 0.10000000005},
        {"price": 1901.0},
        {"client_order_id": "untrusted-client"},
        {"client_order_id": "ethbot-stop-"},
        {"stop_price": 1950.0},
    )

    for invalid_fields in invalid_variants:
        state = {**base_state, "open_orders": [{**valid_order, **invalid_fields}]}
        result = plugin_api.build_paper_snapshot(state, ticker)

        assert result["valid"] is False, invalid_fields
        assert result["positions"][0]["protected"] is False, invalid_fields
        assert result["positions"][0]["protective_order_ids"] == [], invalid_fields
        assert result["compliance"]["protected"] is False, invalid_fields


def test_build_paper_snapshot_rejects_unsafe_integer_protective_sizes():
    position_size = 2**53
    order_size = position_size + 1
    state = {
        "starting_collateral": 400.0,
        "collateral": 400.0,
        "currency": "USD",
        "positions": [
            {
                "symbol": "PF_ETHUSD",
                "side": "Long",
                "size": position_size,
                "entry_price": 1900.0,
                "leverage": 1.0,
                "unrealized_funding": 0.0,
            }
        ],
        "open_orders": [
            {
                "id": "FP-stop",
                "client_order_id": "ethbot-stop-unsafe-size",
                "symbol": "PF_ETHUSD",
                "side": "Short",
                "size": order_size,
                "filled_size": 0.0,
                "order_type": "Stop",
                "price": None,
                "stop_price": 1710.0,
                "trigger_signal": "Mark",
                "reduce_only": True,
                "leverage": 1.0,
                "status": "Open",
            }
        ],
        "fills": [],
        "history": [],
        "leverage_preferences": {"PF_ETHUSD": 1.0},
    }
    ticker = {
        "result": "success",
        "ticker": {
            "symbol": "PF_ETHUSD",
            "markPrice": 1950.0,
            "indexPrice": 1949.5,
        },
    }

    result = plugin_api.build_paper_snapshot(state, ticker)

    assert result["valid"] is False
    assert result["positions"][0]["protected"] is False
    assert result["compliance"]["protected"] is False


def test_build_paper_snapshot_rejects_duplicate_or_unrelated_open_orders():
    protecting_order = {
        "id": "FP-stop",
        "client_order_id": "ethbot-stop-20260727T120000Z",
        "symbol": "PF_ETHUSD",
        "side": "Short",
        "size": 0.1,
        "filled_size": 0.0,
        "order_type": "Stop",
        "price": None,
        "stop_price": 1710.0,
        "trigger_signal": "Mark",
        "reduce_only": True,
        "leverage": 1.0,
        "status": "Open",
    }
    state = {
        "starting_collateral": 400.0,
        "collateral": 400.0,
        "currency": "USD",
        "positions": [
            {
                "symbol": "PF_ETHUSD",
                "side": "Long",
                "size": 0.1,
                "entry_price": 1900.0,
                "leverage": 1.0,
                "unrealized_funding": 0.0,
            }
        ],
        "open_orders": [
            protecting_order,
            {**protecting_order, "id": "FP-duplicate"},
            {**protecting_order, "id": "FP-xbt", "symbol": "PF_XBTUSD"},
        ],
        "fills": [],
        "history": [],
        "leverage_preferences": {"PF_ETHUSD": 1.0},
    }
    ticker = {
        "result": "success",
        "ticker": {"symbol": "PF_ETHUSD", "markPrice": 1950.0, "indexPrice": 1949.5},
    }

    result = plugin_api.build_paper_snapshot(state, ticker)

    assert result["positions"][0]["protected"] is False
    assert result["compliance"]["protected"] is False
    assert result["protection"]["open_order_count"] == 3


def test_build_automation_status_treats_successful_oneshot_as_healthy():
    release = "a" * 40
    service = {
        "ActiveState": "inactive",
        "SubState": "dead",
        "Result": "success",
        "ExecMainStatus": "0",
        "ExecMainStartTimestamp": "Mon 2026-07-27 16:05:00 EDT",
        "ExecMainExitTimestamp": "Mon 2026-07-27 16:05:02 EDT",
    }
    timer = {
        "ActiveState": "active",
        "UnitFileState": "enabled",
        "LastTriggerUSec": "Mon 2026-07-27 16:05:00 EDT",
        "NextElapseUSecRealtime": "Mon 2026-07-27 20:05:00 EDT",
    }
    environment = {
        "PAPER_EXECUTE": "1",
        "LEDGER_PATH": f"/home/test/releases/{release}/runtime/paper-ledger.sqlite3",
        "KILL_SWITCH_PATH": f"/home/test/releases/{release}/runtime/KILL_SWITCH",
    }

    result = plugin_api.build_automation_status(
        service,
        timer,
        environment,
        kill_switch_armed=False,
        now=datetime(2026, 7, 27, 20, 6, tzinfo=UTC),
    )

    assert result["healthy"] is True
    assert result["execution_enabled"] is True
    assert result["timer_enabled"] is True
    assert result["timer_active"] is True
    assert result["release"] == release

    uppercase = plugin_api.build_automation_status(
        service,
        timer,
        {
            **environment,
            "LEDGER_PATH": (
                f"/home/test/releases/{release.upper()}/runtime/paper-ledger.sqlite3"
            ),
        },
        kill_switch_armed=False,
        now=datetime(2026, 7, 27, 20, 6, tzinfo=UTC),
    )
    assert uppercase["healthy"] is False
    assert uppercase["release"] is None

    for alias in (
        f"/home/./test/releases/{release}/runtime/paper-ledger.sqlite3",
        f"/home//test/releases/{release}/runtime/paper-ledger.sqlite3",
        f"/home/test/releases/{release}/runtime/paper\u0085ledger.sqlite3",
        f"/home/test/releases/{release}/runtime/paper\u200bledger.sqlite3",
        f"/home/test/releases/{release}/runtime/paper\ud800ledger.sqlite3",
        f"/home/test/releases/{release}/runtime/paper\ue000ledger.sqlite3",
        f"/home/test/releases/{release}/runtime/paper\u0378ledger.sqlite3",
    ):
        aliased = plugin_api.build_automation_status(
            service,
            timer,
            {**environment, "LEDGER_PATH": alias},
            kill_switch_armed=False,
            now=datetime(2026, 7, 27, 20, 6, tzinfo=UTC),
        )
        assert aliased["healthy"] is False, alias
        assert aliased["release"] is None, alias


def test_release_from_ledger_path_requires_one_canonical_runtime_suffix():
    release = "a" * 40
    valid = f"/home/test/releases/{release}/runtime/paper-ledger.sqlite3"
    valid_unicode = f"/home/test/releases/{release}/runtime/paper-λ-ledger.sqlite3"

    assert plugin_api._release_from_ledger_path(valid) == release
    assert plugin_api._release_from_ledger_path(valid_unicode) == release
    for invalid in (
        f"relative/releases/{release}/runtime/paper-ledger.sqlite3",
        f"/home/test/releases/{release}/nested/runtime/paper-ledger.sqlite3",
        f"/home/test/releases/{release}/runtime/nested/paper-ledger.sqlite3",
        f"/home/test/releases/{release}/runtime",
        f"/home/./test/releases/{release}/runtime/paper-ledger.sqlite3",
        f"/home//test/releases/{release}/runtime/paper-ledger.sqlite3",
        f"/home/test/releases/{release}/runtime/./paper-ledger.sqlite3",
        f"/home/test/releases/{release}/runtime/paper-ledger.sqlite3\n",
        f"/home/test/releases/{release}/runtime/paper-ledger.sqlite3\x00",
        f"/home/test/releases/{release}/runtime/paper\u0085ledger.sqlite3",
        f"/home/test/releases/{release}/runtime/paper\u200bledger.sqlite3",
        f"/home/test/releases/{release}/runtime/paper\ud800ledger.sqlite3",
        f"/home/test/releases/{release}/runtime/paper\ue000ledger.sqlite3",
        f"/home/test/releases/{release}/runtime/paper\u0378ledger.sqlite3",
        f"/home/test/releases/{release}/runtime/   ",
        (
            f"/home/test/releases/{release}/releases/"
            f"{'b' * 40}/runtime/paper-ledger.sqlite3"
        ),
    ):
        assert plugin_api._release_from_ledger_path(invalid) is None


def test_non_empty_text_rejects_whitespace_and_unicode_control_identities():
    for invalid in (
        "",
        " \t\r\n",
        "\ufeff",
        "\u200b",
        "\x1c",
        "event\x00id",
        "event\x7fid",
        "event\u0085id",
    ):
        assert plugin_api._non_empty_text(invalid) is False, repr(invalid)

    for valid in ("FP-00003", "eth-pf-20260727-v1", " event-id "):
        assert plugin_api._non_empty_text(valid) is True, repr(valid)


def test_machine_identity_grammar_is_ascii_and_runtime_independent():
    for invalid in (
        "",
        " event-id ",
        "event id",
        "event\n",
        "event<id>",
        "event-λ",
        f"event-{chr(0x1E4D0)}",
        "event\x00id",
        "event\u200bid",
    ):
        assert plugin_api._valid_identity(invalid) is False, repr(invalid)

    for valid in ("FP-00003", "eth-pf-20260727-v1", "event:type_1.0/test"):
        assert plugin_api._valid_identity(valid) is True, repr(valid)


def test_parse_environment_rejects_partial_or_ambiguous_serialization():
    parsed, error = plugin_api._parse_environment(
        'PAPER_EXECUTE=1 LEDGER_PATH="/home/test/paper ledger.sqlite3" EMPTY='
    )
    assert error is None
    assert parsed == {
        "PAPER_EXECUTE": "1",
        "LEDGER_PATH": "/home/test/paper ledger.sqlite3",
        "EMPTY": "",
    }

    for malformed in (
        'PAPER_EXECUTE=1 LEDGER_PATH=/safe/path "',
        "PAPER_EXECUTE=1 stray-token",
        "PAPER_EXECUTE=1 PAPER_EXECUTE=1",
        "PAPER_EXECUTE=1 BAD-KEY=value",
    ):
        parsed, error = plugin_api._parse_environment(malformed)
        assert parsed == {}, repr(malformed)
        assert error == "service environment is malformed", repr(malformed)


def test_build_automation_status_fails_closed_when_kill_switch_is_armed():
    result = plugin_api.build_automation_status(
        {"Result": "success", "ExecMainStatus": "0"},
        {"ActiveState": "active", "UnitFileState": "enabled"},
        {"PAPER_EXECUTE": "1"},
        kill_switch_armed=True,
    )

    assert result["healthy"] is False
    assert result["kill_switch_armed"] is True


def test_read_ledger_summary_verifies_hash_chain_and_returns_recent_events():
    temporary_directory = tempfile.TemporaryDirectory()
    ledger_path = Path(temporary_directory.name) / "paper-ledger.sqlite3"
    connection = sqlite3.connect(ledger_path)
    connection.execute(
        """
        CREATE TABLE events (
            sequence INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL
        )
        """
    )
    previous_hash = "0" * 64
    for sequence, event_type in ((1, "strategy_decision"), (2, "account_snapshot")):
        event_id = f"event-{sequence}"
        occurred_at = f"2026-07-27T2{sequence}:00:00+00:00"
        payload = {"sequence": sequence}
        material = json.dumps(
            {
                "event_id": event_id,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "payload": payload,
                "previous_hash": previous_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        event_hash = hashlib.sha256(material.encode()).hexdigest()
        connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                sequence,
                event_id,
                event_type,
                occurred_at,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                previous_hash,
                event_hash,
            ),
        )
        previous_hash = event_hash
    connection.commit()
    connection.close()

    result = plugin_api.read_ledger_summary(ledger_path, limit=1)

    assert result["chain_valid"] is True
    assert result["quick_check"] == "ok"
    assert result["event_count"] == 2
    assert result["head_hash"] == previous_hash
    assert [event["event_type"] for event in result["recent_events"]] == [
        "account_snapshot"
    ]
    assert "path" not in result

    connection = sqlite3.connect(ledger_path)
    connection.execute(
        "UPDATE events SET payload_json = ? WHERE event_id = 'event-2'",
        (json.dumps({"equity": 999}, sort_keys=True, separators=(",", ":")),),
    )
    connection.commit()
    connection.close()

    tampered = plugin_api.read_ledger_summary(ledger_path, limit=1)
    assert tampered["chain_valid"] is False
    assert tampered["invalid_event_id"] == "event-2"
    temporary_directory.cleanup()


def test_read_ledger_summary_rejects_malformed_event_identities():
    for event_id, event_type in (
        (" \t ", "account_snapshot"),
        ("event-1", " \n "),
        (f"event-{chr(0x1E4D0)}", "account_snapshot"),
        ("event-1", "account-λ"),
    ):
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger_path = Path(temporary_directory) / "paper-ledger.sqlite3"
            connection = sqlite3.connect(ledger_path)
            connection.execute(
                """
                CREATE TABLE events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                )
                """
            )
            occurred_at = "2026-07-27T20:00:00Z"
            payload: dict[str, Any] = {}
            material = json.dumps(
                {
                    "event_id": event_id,
                    "event_type": event_type,
                    "occurred_at": occurred_at,
                    "payload": payload,
                    "previous_hash": plugin_api.GENESIS_HASH,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            event_hash = hashlib.sha256(material.encode()).hexdigest()
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    1,
                    event_id,
                    event_type,
                    occurred_at,
                    json.dumps(payload, separators=(",", ":")),
                    plugin_api.GENESIS_HASH,
                    event_hash,
                ),
            )
            connection.commit()
            connection.close()

            result = plugin_api.read_ledger_summary(ledger_path)

            assert result["chain_valid"] is False, (repr(event_id), repr(event_type))
            assert not plugin_api.ledger_is_healthy(result)


def test_ledger_integrity_requires_a_valid_nonempty_database():
    assert plugin_api.ledger_is_healthy(
        {"quick_check": "ok", "chain_valid": True, "event_count": 1}
    )
    assert not plugin_api.ledger_is_healthy(
        {"quick_check": "corrupt", "chain_valid": True, "event_count": 1}
    )
    assert not plugin_api.ledger_is_healthy(
        {"quick_check": "ok", "chain_valid": False, "event_count": 1}
    )
    assert not plugin_api.ledger_is_healthy(
        {"quick_check": "ok", "chain_valid": True, "event_count": 0}
    )
    assert not plugin_api.ledger_is_healthy({})


def test_experiment_metadata_includes_content_seal_hash():
    with tempfile.TemporaryDirectory() as temporary_directory:
        commit = "a" * 40
        runtime_path = Path(temporary_directory) / commit / "runtime"
        experiment_path = runtime_path / "forward-experiments" / "experiment.json"
        experiment_path.parent.mkdir(parents=True)
        content = json.dumps(
            {
                "schema_version": 1,
                "experiment": {
                    "experiment_id": "eth-pf-test-v1",
                    "start_at": "2026-07-27T18:28:00Z",
                },
                "git": {"commit": commit, "tree": "b" * 40},
                "contract": {
                    "mode": "futures_paper",
                    "symbol": "PF_ETHUSD",
                    "account_baseline_usd": 400.0,
                    "max_notional_usd": 75.0,
                    "leverage": 1.0,
                    "allowed_sides": ["long"],
                },
                "ledger": {"path": str(runtime_path / "paper-ledger.sqlite3")},
            },
            sort_keys=True,
        )
        experiment_path.write_text(content, encoding="utf-8")

        result = plugin_api._experiment_metadata(runtime_path)

        assert result["experiment_id"] == "eth-pf-test-v1"
        assert (
            result["seal_file_sha256"] == hashlib.sha256(content.encode()).hexdigest()
        )
        assert result["tree"] == "b" * 40
        assert result["started_at"] == "2026-07-27T18:28:00Z"


def test_experiment_metadata_rejects_whitespace_experiment_identity():
    with tempfile.TemporaryDirectory() as temporary_directory:
        commit = "a" * 40
        runtime_path = Path(temporary_directory) / commit / "runtime"
        experiment_path = runtime_path / "forward-experiments" / "experiment.json"
        experiment_path.parent.mkdir(parents=True)
        experiment_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "experiment": {
                        "experiment_id": " \t ",
                        "start_at": "2026-07-27T18:28:00Z",
                    },
                    "git": {"commit": commit, "tree": "b" * 40},
                    "contract": {
                        "mode": "futures_paper",
                        "symbol": "PF_ETHUSD",
                        "account_baseline_usd": 400.0,
                        "max_notional_usd": 75.0,
                        "leverage": 1.0,
                        "allowed_sides": ["long"],
                    },
                    "ledger": {"path": str(runtime_path / "paper-ledger.sqlite3")},
                }
            ),
            encoding="utf-8",
        )

        assert plugin_api._experiment_metadata(runtime_path) == {}


def test_hex_digests_and_experiment_provenance_require_canonical_lowercase():
    assert plugin_api._is_hex_digest("a" * 40, length=40)
    assert not plugin_api._is_hex_digest("A" * 40, length=40)

    for release_commit, tree in (("A" * 40, "b" * 40), ("a" * 40, "B" * 40)):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary_directory:
            runtime_path = Path(temporary_directory) / release_commit / "runtime"
            experiment_path = runtime_path / "forward-experiments" / "experiment.json"
            experiment_path.parent.mkdir(parents=True)
            experiment_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "experiment": {
                            "experiment_id": "eth-pf-test-v1",
                            "start_at": "2026-07-27T18:28:00Z",
                        },
                        "git": {"commit": release_commit, "tree": tree},
                        "contract": {
                            "mode": "futures_paper",
                            "symbol": "PF_ETHUSD",
                            "account_baseline_usd": 400.0,
                            "max_notional_usd": 75.0,
                            "leverage": 1.0,
                            "allowed_sides": ["long"],
                        },
                        "ledger": {"path": str(runtime_path / "paper-ledger.sqlite3")},
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            assert plugin_api._experiment_metadata(runtime_path) == {}


def test_experiment_metadata_ignores_malformed_or_mismatched_seals():
    with tempfile.TemporaryDirectory() as temporary_directory:
        commit = "a" * 40
        runtime_path = Path(temporary_directory) / commit / "runtime"
        seals = runtime_path / "forward-experiments"
        seals.mkdir(parents=True)
        (seals / "scalar.json").write_text("[]", encoding="utf-8")
        mismatched = {
            "schema_version": 1,
            "experiment": {
                "experiment_id": "wrong",
                "start_at": "2026-07-27T18:28:00Z",
            },
            "git": {"commit": "c" * 40, "tree": "b" * 40},
            "contract": {
                "mode": "futures_paper",
                "symbol": "PF_ETHUSD",
                "account_baseline_usd": 400.0,
                "max_notional_usd": 75.0,
                "leverage": 1.0,
                "allowed_sides": ["long"],
            },
            "ledger": {"path": str(runtime_path / "paper-ledger.sqlite3")},
        }
        (seals / "mismatched.json").write_text(json.dumps(mismatched), encoding="utf-8")

        assert plugin_api._experiment_metadata(runtime_path) == {}


def test_parse_journal_cycles_keeps_only_structured_cycle_results():
    lines = "\n".join(
        [
            json.dumps(
                {
                    "__REALTIME_TIMESTAMP": "1785182700000000",
                    "MESSAGE": "Starting ETH/Kraken fail-closed futures-paper cycle...",
                }
            ),
            json.dumps(
                {
                    "__REALTIME_TIMESTAMP": "1785182702000000",
                    "MESSAGE": json.dumps(
                        {
                            "action": "position_observed",
                            "blockers": [],
                            "size": 0.038,
                            "notional_usd": 74.0,
                        }
                    ),
                }
            ),
        ]
    )

    result = plugin_api.parse_journal_cycles(lines)

    assert len(result) == 1
    assert result[0]["action"] == "position_observed"
    assert result[0]["size"] == 0.038
    assert result[0]["occurred_at"].endswith("+00:00")


def test_parse_journal_cycles_does_not_trust_payload_timestamp():
    line = json.dumps(
        {
            "__REALTIME_TIMESTAMP": "1785182702000000",
            "MESSAGE": json.dumps(
                {
                    "action": "position_observed",
                    "blockers": [],
                    "occurred_at": "spoofed",
                }
            ),
        }
    )

    result = plugin_api.parse_journal_cycles(line)

    assert len(result) == 1
    assert result[0]["occurred_at"] != "spoofed"
    assert result[0]["occurred_at"].endswith("+00:00")


def test_parse_journal_cycles_ignores_scalar_records_and_overflowing_timestamps():
    lines = [
        json.dumps([]),
        json.dumps(7),
        json.dumps({"__REALTIME_TIMESTAMP": "9" * 5000, "MESSAGE": "{}"}),
        json.dumps({"__REALTIME_TIMESTAMP": "1785182700000000", "MESSAGE": '"scalar"'}),
        json.dumps(
            {
                "__REALTIME_TIMESTAMP": "1785182700000000",
                "MESSAGE": json.dumps(
                    {"event": "cycle_result", "action": "hold", "blockers": []}
                ),
            }
        ),
    ]

    errors: list[str] = []
    result = plugin_api.parse_journal_cycles("\n".join(lines), validation_errors=errors)

    assert len(result) == 1
    assert result[0]["action"] == "hold"
    assert errors == []

    for invalid_action in ("\ufeff", "\u200b", "hold\x00"):
        invalid_errors: list[str] = []
        invalid_result = plugin_api.parse_journal_cycles(
            json.dumps(
                {
                    "MESSAGE": json.dumps({"action": invalid_action, "blockers": []}),
                    "__REALTIME_TIMESTAMP": "1785182700000000",
                }
            ),
            validation_errors=invalid_errors,
        )
        assert invalid_result == []
        assert any("action" in error for error in invalid_errors), repr(invalid_action)


def test_parse_journal_cycles_requires_ascii_microsecond_timestamps():
    ascii_timestamp = "1785182700000000"
    translations = (
        str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"),
        str.maketrans("0123456789", "０１２３４５６７８９"),
    )
    for translation in translations:
        errors: list[str] = []
        result = plugin_api.parse_journal_cycles(
            json.dumps(
                {
                    "MESSAGE": json.dumps({"action": "hold", "blockers": []}),
                    "__REALTIME_TIMESTAMP": ascii_timestamp.translate(translation),
                },
                ensure_ascii=False,
            ),
            validation_errors=errors,
        )
        assert result == []
        assert any("timestamp" in error for error in errors)


def test_parse_journal_cycles_reports_malformed_structured_records():
    realtime = str(int(datetime(2026, 7, 28, tzinfo=UTC).timestamp() * 1_000_000))
    records = [
        {
            "MESSAGE": json.dumps({"action": "hold", "blockers": ["spread"]}),
            "__REALTIME_TIMESTAMP": realtime,
        },
        {
            "MESSAGE": json.dumps({"action": "", "blockers": ["spread"]}),
            "__REALTIME_TIMESTAMP": realtime,
        },
        {
            "MESSAGE": json.dumps({"action": "hold", "blockers": {"bad": True}}),
            "__REALTIME_TIMESTAMP": realtime,
        },
        {
            "MESSAGE": json.dumps({"action": "hold", "blockers": []}),
            "__REALTIME_TIMESTAMP": "not-microseconds",
        },
        {
            "MESSAGE": json.dumps(
                {
                    "error": "synthetic service failure",
                    "error_type": "RuntimeError",
                    "status": "failed_closed",
                }
            ),
            "__REALTIME_TIMESTAMP": realtime,
        },
        {"MESSAGE": "ordinary non-structured service output"},
    ]
    errors: list[str] = []

    result = plugin_api.parse_journal_cycles(
        "\n".join(json.dumps(record) for record in records),
        validation_errors=errors,
    )

    assert [cycle["action"] for cycle in result] == ["hold"]
    assert result[0]["blockers"] == ["spread"]
    assert len(errors) == 3
    assert all("journal" in error for error in errors)


def test_number_defaults_instead_of_raising_on_oversized_integer():
    assert plugin_api._number(10**10000, default=7.0) == 7.0


def test_safety_critical_decimal_collisions_fail_closed():
    stop_state = plugin_api._strict_json_loads(
        """{
          "starting_collateral": 400.0,
          "collateral": 400.0,
          "currency": "USD",
          "positions": [{
            "symbol": "PF_ETHUSD", "side": "Long",
            "size": 0.1, "entry_price": 1900.0,
            "leverage": 1.0, "unrealized_funding": 0.0
          }],
          "open_orders": [{
            "id": "FP-precision", "client_order_id": "ethbot-stop-precision",
            "symbol": "PF_ETHUSD", "side": "Sell",
            "size": 0.1000000000000000000001, "filled_size": 0.0,
            "order_type": "stop", "status": "open", "reduce_only": true,
            "leverage": 1.0, "stop_price": 1750.0, "price": null,
            "trigger_signal": "mark"
          }],
          "fills": [], "history": [],
          "leverage_preferences": {"PF_ETHUSD": 1.0}
        }"""
    )
    assert stop_state["positions"][0]["size"] != stop_state["open_orders"][0]["size"]
    ticker = {
        "result": "success",
        "serverTime": "2026-07-27T20:00:00Z",
        "ticker": {
            "symbol": "PF_ETHUSD",
            "markPrice": 1950.0,
            "indexPrice": 1949.5,
        },
    }

    stop_snapshot = plugin_api.build_paper_snapshot(stop_state, ticker)

    assert stop_snapshot["valid"] is False
    assert stop_snapshot["compliance"]["protected"] is False
    assert stop_snapshot["positions"][0]["protected"] is False

    risk_state = plugin_api._strict_json_loads(
        """{
          "starting_collateral": 400.0000000000000000001,
          "collateral": 400.0,
          "currency": "USD",
          "positions": [{
            "symbol": "PF_ETHUSD", "side": "Long",
            "size": 1.0, "entry_price": 100.0,
            "leverage": 1.0, "unrealized_funding": 0.0
          }],
          "open_orders": [], "fills": [], "history": [],
          "leverage_preferences": {"PF_ETHUSD": 1.0}
        }"""
    )
    risk_ticker = plugin_api._strict_json_loads(
        """{
          "result": "success", "serverTime": "2026-07-27T20:00:00Z",
          "ticker": {
            "symbol": "PF_ETHUSD",
            "markPrice": 100.0000000000000000001,
            "indexPrice": 100.0
          }
        }"""
    )
    risk_snapshot = plugin_api.build_paper_snapshot(risk_state, risk_ticker)
    experiment = {
        "symbol": "PF_ETHUSD",
        "account_baseline_usd": 400.0,
        "max_notional_usd": 100.0,
        "leverage": 1.0,
        "allowed_sides": ["long"],
    }

    contract_errors = plugin_api._experiment_contract_errors(
        experiment,
        risk_snapshot,
        {},
        now=datetime(2026, 7, 27, 20, 0, tzinfo=UTC),
    )

    assert risk_snapshot["valid"] is False
    assert any("baseline" in error for error in contract_errors)
    assert any("notional" in error for error in contract_errors)


def test_safety_critical_notional_uses_exact_source_decimal_arithmetic():
    state = plugin_api._strict_json_loads(
        """{
          "starting_collateral": 400.0,
          "collateral": 400.0,
          "currency": "USD",
          "positions": [{
            "symbol": "PF_ETHUSD", "side": "Long",
            "size": 0.9999999999999998, "entry_price": 100.0,
            "leverage": 1.0, "unrealized_funding": 0.0
          }],
          "open_orders": [{
            "id": "FP-exact", "client_order_id": "ethbot-stop-exact",
            "symbol": "PF_ETHUSD", "side": "Short",
            "size": 0.9999999999999998, "filled_size": 0.0,
            "order_type": "stop", "status": "open", "reduce_only": true,
            "leverage": 1.0, "stop_price": 90.0, "price": null,
            "trigger_signal": "mark"
          }],
          "fills": [], "history": [],
          "leverage_preferences": {"PF_ETHUSD": 1.0}
        }"""
    )
    ticker = plugin_api._strict_json_loads(
        """{
          "result": "success", "serverTime": "2026-07-27T20:00:00Z",
          "ticker": {
            "symbol": "PF_ETHUSD",
            "markPrice": 100.00000000000003,
            "indexPrice": 100.00000000000003
          }
        }"""
    )

    snapshot = plugin_api.build_paper_snapshot(state, ticker)
    contract_errors = plugin_api._experiment_contract_errors(
        {
            "symbol": "PF_ETHUSD",
            "account_baseline_usd": 400.0,
            "max_notional_usd": 100.0,
            "leverage": 1.0,
            "allowed_sides": ["long"],
        },
        snapshot,
        {},
        now=datetime(2026, 7, 27, 20, 0, tzinfo=UTC),
    )

    assert snapshot["valid"] is True
    assert snapshot["account"]["exposure_usd"] == 100.0
    assert any("notional" in error for error in contract_errors)


def test_strict_json_loads_rejects_duplicate_object_keys_at_any_depth():
    for payload in (
        '{"size": 0.1, "size": 0.2}',
        '{"position": {"size": 0.1, "size": 0.2}}',
    ):
        with unittest.TestCase().assertRaisesRegex(ValueError, "duplicate JSON key"):
            plugin_api._strict_json_loads(payload)


def test_run_json_preserves_exact_ticker_decimals_for_contract_arithmetic():
    async def fake_run_text(_command, *, timeout=15.0):
        del timeout
        return {
            "ok": True,
            "stdout": (
                '{"result":"success","ticker":{'
                '"symbol":"PF_ETHUSD","markPrice":100.00000000000003,'
                '"indexPrice":100.00000000000003}}'
            ),
            "message": "",
        }

    with mock.patch.object(plugin_api, "_run_text", fake_run_text):
        result = asyncio.run(plugin_api._run_json(["/usr/bin/true"]))

    assert result["ticker"]["markPrice"] == Decimal("100.00000000000003")
    assert isinstance(result["ticker"]["markPrice"], Decimal)


def test_futures_symbol_grammar_rejects_ambiguous_or_unsafe_values():
    for valid in ("PF_ETHUSD", "PF_XBTUSD", "PF_SOLUSD"):
        assert plugin_api._valid_futures_symbol(valid) is True
    for malformed in (
        "PF_ETH/USD",
        "pf_ethusd",
        "PF_ETHUSD\n",
        "PF_ETHUSD\x00tail",
        "PF_",
        "PF_A" * 20,
    ):
        assert plugin_api._valid_futures_symbol(malformed) is False


def test_internal_contract_decimal_and_environment_paths_fail_closed():
    assert plugin_api._contract_decimal_or_none("not-a-number") is None
    assert plugin_api._contract_decimal_or_none("sNaN", derived=True) is None
    for malformed in (
        "/tmp/newline\npath",
        "/tmp/tab\tpath",
        "/tmp/control-\x7f-path",
    ):
        with unittest.TestCase().assertRaises(ValueError):
            plugin_api._absolute_environment_path(malformed, label="test")


def test_read_json_file_rejects_symlinks_world_writable_files_and_large_payloads():
    with tempfile.TemporaryDirectory(dir=Path.home()) as temporary_directory:
        root = Path(temporary_directory)
        target = root / "target.json"
        target.write_text("{}", encoding="utf-8")
        symlink = root / "state.json"
        symlink.symlink_to(target)
        with unittest.TestCase().assertRaises((OSError, ValueError)):
            plugin_api._read_json_file(symlink)

        target.chmod(0o666)
        with unittest.TestCase().assertRaises((OSError, ValueError)):
            plugin_api._read_json_file(target)

        target.chmod(0o600)
        target.write_bytes(b"{" + b" " * (2 * 1024 * 1024) + b"}")
        with unittest.TestCase().assertRaises((OSError, ValueError)):
            plugin_api._read_json_file(target)


def test_resolve_state_path_prefers_service_environment():
    service_path = str(Path.home() / ".config/kraken/paper/service-state.json")
    process_path = str(Path.home() / ".config/kraken/paper/process-state.json")

    result = plugin_api._resolve_state_path(
        {"KRAKEN_PAPER_STATE_PATH": service_path},
        {"KRAKEN_PAPER_STATE_PATH": process_path},
    )

    assert result == Path(service_path)


def test_paper_dashboard_contains_unknown_user_state_and_ledger_paths():
    async def fake_run_text(command, *, timeout=15.0):
        del timeout
        command_text = " ".join(command)
        if "eth-paper-cycle.service" in command_text:
            return {
                "ok": True,
                "stdout": (
                    "ActiveState=inactive\n"
                    "SubState=dead\n"
                    "Result=success\n"
                    "ExecMainStatus=0\n"
                    "Environment=KRAKEN_PAPER_STATE_PATH=~dashboard-user-does-not-exist/state.json "
                    "LEDGER_PATH=~dashboard-user-does-not-exist/ledger.sqlite3\n"
                ),
                "message": "",
            }
        if "eth-paper-cycle.timer" in command_text:
            return {
                "ok": True,
                "stdout": "ActiveState=inactive\nUnitFileState=disabled\n",
                "message": "",
            }
        return {"ok": True, "stdout": "", "message": ""}

    with (
        mock.patch.object(plugin_api, "_run_text", fake_run_text),
        mock.patch.object(
            plugin_api,
            "_resolve_system_binary",
            lambda name: f"/usr/bin/{name}",
        ),
        mock.patch.object(
            plugin_api,
            "_resolve_kraken_cli",
            lambda environment=None: None,
        ),
    ):
        result = asyncio.run(plugin_api.paper_dashboard())

    assert result["healthy"] is False
    assert result["paper"]["valid"] is False
    assert result["ledger"]["healthy"] is False
    assert any("paper state" in error for error in result["errors"])
    assert any("ledger" in error for error in result["errors"])


def test_stale_ticker_fails_snapshot_validation():
    state = {
        "starting_collateral": 400.0,
        "collateral": 400.0,
        "currency": "USD",
        "positions": [
            {
                "symbol": "PF_ETHUSD",
                "side": "Long",
                "size": 0.1,
                "entry_price": 1900.0,
                "leverage": 1.0,
                "unrealized_funding": 0.0,
            }
        ],
        "open_orders": [],
        "fills": [],
        "history": [],
        "leverage_preferences": {"PF_ETHUSD": 1.0},
    }
    ticker = {
        "result": "success",
        "serverTime": "2026-07-27T20:00:00Z",
        "ticker": {"symbol": "PF_ETHUSD", "markPrice": 1950.0, "indexPrice": 1949.5},
    }

    result = plugin_api.build_paper_snapshot(
        state,
        ticker,
        now=datetime(2026, 7, 27, 20, 10, tzinfo=UTC),
    )

    assert result["valid"] is False
    assert result["account"]["equity"] is None
    assert any("stale" in error for error in result["validation_errors"])


def test_run_text_uses_minimal_environment_and_redacts_stderr():
    captured = {}

    class FakeProcess:
        returncode = 1

        async def communicate(self):
            return b"", b"TEST_DASHBOARD_SECRET=do-not-leak"

    async def fake_create(*command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    original = plugin_api.asyncio.create_subprocess_exec
    plugin_api.asyncio.create_subprocess_exec = fake_create
    os.environ["TEST_DASHBOARD_SECRET"] = "do-not-leak"
    try:
        result = asyncio.run(plugin_api._run_text(["/usr/bin/false"]))
    finally:
        plugin_api.asyncio.create_subprocess_exec = original
        os.environ.pop("TEST_DASHBOARD_SECRET", None)

    assert "env" in captured["kwargs"]
    assert "TEST_DASHBOARD_SECRET" not in captured["kwargs"]["env"]
    assert "do-not-leak" not in result["message"]


def test_run_text_contains_malformed_subprocess_arguments():
    for malformed in ("embedded\x00nul", "unpaired-\ud800-surrogate"):
        result = asyncio.run(plugin_api._run_text(["/usr/bin/true", malformed]))

        assert result["ok"] is False
        assert result["stdout"] == ""
        assert result["message"] == "command arguments are invalid"


def test_paper_dashboard_rejects_malformed_ticker_symbol_before_execution():
    state = {
        "starting_collateral": 400.0,
        "collateral": 400.0,
        "currency": "USD",
        "positions": [
            {
                "symbol": "PF_ETHUSD\x00malformed",
                "side": "Long",
                "size": 0.1,
                "entry_price": 1900.0,
                "leverage": 1.0,
                "unrealized_funding": 0.0,
            }
        ],
        "open_orders": [],
        "fills": [],
        "history": [],
        "leverage_preferences": {"PF_ETHUSD\x00malformed": 1.0},
    }
    original_run_text = plugin_api._run_text
    ticker_called = False

    async def fake_run_text(command, *, timeout=15.0):
        nonlocal ticker_called
        if "futures" in command:
            ticker_called = True
            return await original_run_text(command, timeout=timeout)
        if plugin_api.SERVICE_UNIT in command and "show" in command:
            return {
                "ok": True,
                "stdout": (
                    "ActiveState=active\nSubState=running\nResult=success\n"
                    "ExecMainStatus=0\n"
                    "Environment=KRAKEN_PAPER_STATE_PATH=/tmp/paper-state.json\n"
                ),
                "message": "",
            }
        if plugin_api.TIMER_UNIT in command:
            return {
                "ok": True,
                "stdout": "ActiveState=active\nUnitFileState=enabled\n",
                "message": "",
            }
        return {"ok": True, "stdout": "", "message": ""}

    with (
        mock.patch.object(plugin_api, "_run_text", fake_run_text),
        mock.patch.object(
            plugin_api,
            "_resolve_system_binary",
            lambda _name: "/usr/bin/true",
        ),
        mock.patch.object(plugin_api, "_read_json_file", lambda _path: state),
        mock.patch.object(
            plugin_api,
            "_resolve_kraken_cli",
            lambda _environment: "/usr/bin/true",
        ),
    ):
        result = asyncio.run(plugin_api.paper_dashboard())

    assert result["healthy"] is False
    assert result["paper"]["valid"] is False
    assert ticker_called is False
    assert any("symbol" in error for error in result["errors"])


def test_paper_dashboard_contains_malformed_service_environment_paths():
    state = {
        "starting_collateral": 400.0,
        "collateral": 400.0,
        "currency": "USD",
        "positions": [
            {
                "symbol": "PF_ETHUSD",
                "side": "Long",
                "size": 0.1,
                "entry_price": 1900.0,
                "leverage": 1.0,
                "unrealized_funding": 0.0,
            }
        ],
        "open_orders": [],
        "fills": [],
        "history": [],
        "leverage_preferences": {"PF_ETHUSD": 1.0},
    }
    base_environment = {
        "KRAKEN_PAPER_STATE_PATH": "/tmp/paper-state.json",
        "LEDGER_PATH": "/tmp/paper-ledger.sqlite3",
        "KILL_SWITCH_PATH": "/tmp/KILL_SWITCH",
        "KRAKEN_CLI": "/usr/bin/true",
        "PAPER_EXECUTE": "1",
    }
    expected_error = {
        "KRAKEN_PAPER_STATE_PATH": "paper state",
        "LEDGER_PATH": "ledger",
        "KILL_SWITCH_PATH": "kill-switch",
        "KRAKEN_CLI": "kraken-cli",
    }

    for key, error_fragment in expected_error.items():
        for malformed in ("/tmp/embedded\x00path", "/tmp/unpaired-\ud800-path"):
            environment = {**base_environment, key: malformed}

            async def fake_run_text(command, *, timeout=15.0, environment=environment):
                del timeout
                if plugin_api.SERVICE_UNIT in command and "show" in command:
                    return {
                        "ok": True,
                        "stdout": (
                            "ActiveState=active\nSubState=running\nResult=success\n"
                            "ExecMainStatus=0\nEnvironment="
                            + " ".join(
                                f"{name}={value}" for name, value in environment.items()
                            )
                            + "\n"
                        ),
                        "message": "",
                    }
                if plugin_api.TIMER_UNIT in command:
                    return {
                        "ok": True,
                        "stdout": "ActiveState=active\nUnitFileState=enabled\n",
                        "message": "",
                    }
                if "futures" in command:
                    return {
                        "ok": True,
                        "stdout": json.dumps(
                            {
                                "result": "success",
                                "ticker": {
                                    "symbol": "PF_ETHUSD",
                                    "markPrice": 1950.0,
                                    "indexPrice": 1949.5,
                                },
                            }
                        ),
                        "message": "",
                    }
                return {"ok": True, "stdout": "", "message": ""}

            with (
                mock.patch.object(plugin_api, "_run_text", fake_run_text),
                mock.patch.object(
                    plugin_api,
                    "_resolve_system_binary",
                    lambda _name: "/usr/bin/true",
                ),
                mock.patch.object(plugin_api, "_read_json_file", lambda _path: state),
                mock.patch.object(
                    plugin_api,
                    "read_ledger_summary",
                    lambda _path: {
                        "quick_check": "ok",
                        "chain_valid": True,
                        "event_count": 1,
                        "first_event_at": "2026-07-27T20:00:00Z",
                        "last_event_at": "2026-07-27T20:00:00Z",
                        "recent_events": [],
                    },
                ),
                mock.patch.object(
                    plugin_api, "_experiment_metadata", lambda *_a, **_k: {}
                ),
            ):
                result = asyncio.run(plugin_api.paper_dashboard())

            assert result["healthy"] is False, (key, repr(malformed))
            assert any(error_fragment in error for error in result["errors"]), (
                key,
                repr(malformed),
                result["errors"],
            )


def test_run_text_kills_child_when_cancelled():
    async def exercise():
        started = asyncio.Event()

        class FakeProcess:
            returncode = None
            killed = False

            async def communicate(self):
                started.set()
                await asyncio.Future()

            def kill(self):
                self.killed = True
                self.returncode = -9

            async def wait(self):
                return self.returncode

        process = FakeProcess()

        async def fake_create(*command, **kwargs):
            return process

        original = plugin_api.asyncio.create_subprocess_exec
        plugin_api.asyncio.create_subprocess_exec = fake_create
        try:
            task = asyncio.create_task(plugin_api._run_text(["/usr/bin/sleep", "30"]))
            await started.wait()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            plugin_api.asyncio.create_subprocess_exec = original
        return process.killed

    assert asyncio.run(exercise()) is True


def test_resolve_kraken_cli_rejects_world_writable_executable():
    with tempfile.TemporaryDirectory(dir=Path.home()) as temporary_directory:
        executable = Path(temporary_directory) / "kraken-cli"
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o777)

        assert plugin_api._resolve_kraken_cli({"KRAKEN_CLI": str(executable)}) is None


def test_trusted_executable_rejects_writable_parent_and_pins_executed_identity():
    with tempfile.TemporaryDirectory(dir=Path.home()) as temporary_directory:
        root = Path(temporary_directory)
        unsafe = root / "unsafe"
        unsafe.mkdir()
        unsafe.chmod(0o777)
        unsafe_executable = unsafe / "command"
        unsafe_executable.write_text("#!/bin/sh\nprintf unsafe", encoding="utf-8")
        unsafe_executable.chmod(0o755)
        assert plugin_api._trusted_executable(unsafe_executable) is None

        trusted = root / "trusted"
        trusted.mkdir(mode=0o700)
        executable = trusted / "command"
        replacement = trusted / "replacement"
        executable.write_text("#!/bin/sh\nprintf original", encoding="utf-8")
        replacement.write_text("#!/bin/sh\nprintf replacement", encoding="utf-8")
        executable.chmod(0o700)
        replacement.chmod(0o700)
        original_create = plugin_api.asyncio.create_subprocess_exec

        async def replacing_create(*command, **kwargs):
            os.replace(replacement, executable)
            return await original_create(*command, **kwargs)

        plugin_api.asyncio.create_subprocess_exec = replacing_create
        try:
            result = asyncio.run(plugin_api._run_text([str(executable)]))
        finally:
            plugin_api.asyncio.create_subprocess_exec = original_create

        assert result["ok"] is True
        assert result["stdout"] == "original"


def test_read_ledger_summary_does_not_create_sqlite_sidecars():
    with tempfile.TemporaryDirectory() as temporary_directory:
        ledger_path = Path(temporary_directory) / "ledger.sqlite3"
        connection = sqlite3.connect(ledger_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE events (
                sequence INTEGER PRIMARY KEY,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL
            )
            """
        )
        payload = {"sequence": 1}
        material = json.dumps(
            {
                "event_id": "event-1",
                "event_type": "strategy_decision",
                "occurred_at": "2026-07-27T20:00:00+00:00",
                "payload": payload,
                "previous_hash": "0" * 64,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        event_hash = hashlib.sha256(material.encode()).hexdigest()
        connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                "event-1",
                "strategy_decision",
                "2026-07-27T20:00:00+00:00",
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                "0" * 64,
                event_hash,
            ),
        )
        connection.commit()
        connection.close()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{ledger_path}{suffix}")
            if sidecar.exists():
                sidecar.unlink()

        result = plugin_api.read_ledger_summary(ledger_path)

        assert result["chain_valid"] is True
        assert not Path(f"{ledger_path}-wal").exists()
        assert not Path(f"{ledger_path}-shm").exists()


def test_router_keeps_inert_compatibility_endpoints_without_private_calls():
    paths = {route.path for route in plugin_api.router.routes}

    assert "/paper-dashboard" in paths
    assert "/summary" in paths
    assert "/positions" in paths
    assert "/balance" in paths
    assert "/trades" in paths

    balance = asyncio.run(plugin_api.balance())
    trades = asyncio.run(plugin_api.trades())
    assert balance == {
        "error": True,
        "retired": True,
        "message": "Live Kraken balance access was retired; this backend is paper-only.",
    }
    assert trades == {
        "error": True,
        "retired": True,
        "message": "Authenticated Kraken trade-history access was retired; this backend is paper-only.",
    }


def _valid_flat_state(timestamp: str) -> dict:
    return {
        "starting_collateral": 400.0,
        "collateral": 400.0,
        "currency": "USD",
        "last_reconciled_at": timestamp,
        "updated_at": timestamp,
        "positions": [],
        "open_orders": [],
        "fills": [],
        "history": [],
        "leverage_preferences": {"PF_ETHUSD": 1.0},
    }


def _valid_ticker(timestamp: str) -> dict:
    return {
        "result": "success",
        "serverTime": timestamp,
        "ticker": {
            "symbol": "PF_ETHUSD",
            "markPrice": 1950.0,
            "indexPrice": 1949.5,
        },
    }


def _create_events_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE events (
            sequence INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL
        )
        """
    )


def _insert_ledger_event(
    connection: sqlite3.Connection,
    sequence: int,
    previous_hash: str,
    occurred_at: str,
) -> str:
    payload = {"sequence": sequence}
    event_id = f"event-{sequence}"
    material = json.dumps(
        {
            "event_id": event_id,
            "event_type": "strategy_decision",
            "occurred_at": occurred_at,
            "payload": payload,
            "previous_hash": previous_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    event_hash = hashlib.sha256(material.encode()).hexdigest()
    connection.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            sequence,
            event_id,
            "strategy_decision",
            occurred_at,
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            previous_hash,
            event_hash,
        ),
    )
    return event_hash


def test_read_ledger_summary_rejects_live_uncheckpointed_wal():
    with tempfile.TemporaryDirectory() as temporary_directory:
        ledger_path = Path(temporary_directory) / "ledger.sqlite3"
        writer = sqlite3.connect(ledger_path)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            """
            CREATE TABLE events (
                sequence INTEGER PRIMARY KEY,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL
            )
            """
        )
        first_hash = _insert_ledger_event(
            writer,
            1,
            "0" * 64,
            "2026-07-28T00:00:00+00:00",
        )
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        _insert_ledger_event(
            writer,
            2,
            first_hash,
            "2026-07-28T00:05:00+00:00",
        )
        writer.commit()

        assert writer.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
        assert Path(f"{ledger_path}-wal").stat().st_size > 0
        with unittest.TestCase().assertRaises(ValueError):
            plugin_api.read_ledger_summary(ledger_path)
        writer.close()


def test_read_ledger_summary_rejects_path_replacement_during_open():
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        ledger_path = root / "ledger.sqlite3"
        replacement_path = root / "replacement.sqlite3"
        for path in (ledger_path, replacement_path):
            connection = sqlite3.connect(path)
            _create_events_table(connection)
            _insert_ledger_event(
                connection,
                1,
                "0" * 64,
                "2026-07-28T00:05:00+00:00",
            )
            connection.commit()
            connection.close()

        original_connect = plugin_api.sqlite3.connect
        replaced = False

        def replacing_connect(database, *args, **kwargs):
            nonlocal replaced
            if not replaced:
                replaced = True
                os.replace(replacement_path, ledger_path)
            return original_connect(database, *args, **kwargs)

        plugin_api.sqlite3.connect = replacing_connect
        try:
            with unittest.TestCase().assertRaises((ValueError, sqlite3.Error)):
                plugin_api.read_ledger_summary(ledger_path)
        finally:
            plugin_api.sqlite3.connect = original_connect


def test_read_ledger_summary_rejects_hardlinked_database():
    with tempfile.TemporaryDirectory() as temporary_directory:
        ledger_path = Path(temporary_directory) / "ledger.sqlite3"
        connection = sqlite3.connect(ledger_path)
        _create_events_table(connection)
        _insert_ledger_event(
            connection,
            1,
            "0" * 64,
            "2026-07-28T00:05:00+00:00",
        )
        connection.commit()
        connection.close()
        os.link(ledger_path, Path(temporary_directory) / "ledger-hardlink.sqlite3")

        with unittest.TestCase().assertRaises(ValueError):
            plugin_api.read_ledger_summary(ledger_path)


def test_read_ledger_summary_rejects_malformed_event_schema():
    with tempfile.TemporaryDirectory() as temporary_directory:
        ledger_path = Path(temporary_directory) / "ledger.sqlite3"
        connection = sqlite3.connect(ledger_path)
        connection.execute(
            """
            CREATE TABLE events (
                sequence INTEGER PRIMARY KEY,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                previous_digest TEXT NOT NULL,
                event_digest TEXT NOT NULL
            )
            """
        )
        connection.commit()
        connection.close()

        with unittest.TestCase().assertRaises(ValueError):
            plugin_api.read_ledger_summary(ledger_path)


def test_read_ledger_summary_rejects_real_sequence_without_primary_key():
    with tempfile.TemporaryDirectory() as temporary_directory:
        ledger_path = Path(temporary_directory) / "ledger.sqlite3"
        connection = sqlite3.connect(ledger_path)
        connection.execute(
            """
            CREATE TABLE events (
                sequence REAL,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL
            )
            """
        )
        payload_json = json.dumps({"action": "hold"}, sort_keys=True)
        previous_hash = "0" * 64
        event_hash = hashlib.sha256(
            (
                "EV-1.5"
                + "cycle_result"
                + "2026-07-28T00:05:00+00:00"
                + payload_json
                + previous_hash
            ).encode()
        ).hexdigest()
        connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                1.5,
                "EV-1.5",
                "cycle_result",
                "2026-07-28T00:05:00+00:00",
                payload_json,
                previous_hash,
                event_hash,
            ),
        )
        connection.commit()
        connection.close()

        with unittest.TestCase().assertRaises(ValueError):
            plugin_api.read_ledger_summary(ledger_path)


def test_snapshot_rejects_stale_flat_state_and_ticker():
    now = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
    stale = (now - timedelta(days=1)).isoformat()

    result = plugin_api.build_paper_snapshot(
        _valid_flat_state(stale),
        _valid_ticker(stale),
        now=now,
    )

    assert result["valid"] is False
    assert any("stale" in error for error in result["validation_errors"])


def test_timestamp_parser_requires_the_shared_rfc3339_subset():
    for valid in (
        "2026-07-28T00:00:00Z",
        "2026-07-28T00:00:00.123456+00:00",
        "2026-07-28T00:00:00.123456789Z",
        "2026-07-27T20:00:00-04:00",
    ):
        assert plugin_api._parse_timestamp(valid) is not None

    for invalid in (
        "2026-07-28",
        "2026-07-28 00:00:00+00:00",
        "2026-07-28T00:00:00",
        "2026-07-28T00:00:00+0000",
        "2026-07-28T00:00:00.1234567890Z",
        "2026-02-30T00:00:00Z",
        "0001-01-01T00:00:00+00:01",
        "9999-12-31T23:59:59-00:01",
        " 2026-07-28T00:00:00Z",
        1785196800,
    ):
        assert plugin_api._parse_timestamp(invalid) is None


def test_freshness_compares_boundaries_at_nanosecond_precision():
    now = datetime(2026, 7, 28, 0, 5, tzinfo=UTC)
    current = "2026-07-28T00:05:00Z"
    future_boundary = "2026-07-28T00:05:30.000000000Z"
    future_over = "2026-07-28T00:05:30.000000001Z"
    stale_boundary = "2026-07-28T00:00:00.000000000Z"
    stale_over = "2026-07-27T23:59:59.999999999Z"
    ticker_stale_boundary = "2026-07-28T00:04:00.000000000Z"
    ticker_stale_over = "2026-07-28T00:03:59.999999999Z"

    assert plugin_api._fresh_timestamp(future_boundary, now=now)
    assert not plugin_api._fresh_timestamp(future_over, now=now)
    assert plugin_api._fresh_timestamp(
        stale_boundary,
        now=now,
        max_age_seconds=300,
    )
    assert not plugin_api._fresh_timestamp(
        stale_over,
        now=now,
        max_age_seconds=300,
    )

    state_boundary = plugin_api.build_paper_snapshot(
        _valid_flat_state(future_boundary),
        _valid_ticker(current),
        now=now,
    )
    state_over = plugin_api.build_paper_snapshot(
        _valid_flat_state(future_over),
        _valid_ticker(current),
        now=now,
    )
    assert state_boundary["valid"] is True
    assert state_over["valid"] is False
    assert any("stale" in error for error in state_over["validation_errors"])

    for boundary_timestamp, over_timestamp in (
        (future_boundary, future_over),
        (ticker_stale_boundary, ticker_stale_over),
    ):
        ticker_boundary = plugin_api.build_paper_snapshot(
            _valid_flat_state(current),
            _valid_ticker(boundary_timestamp),
            now=now,
        )
        ticker_over = plugin_api.build_paper_snapshot(
            _valid_flat_state(current),
            _valid_ticker(over_timestamp),
            now=now,
        )
        assert ticker_boundary["valid"] is True
        assert ticker_over["valid"] is False
        assert any(
            "public ticker" in error for error in ticker_over["validation_errors"]
        )

    ledger = {"quick_check": "ok", "chain_valid": True, "event_count": 2}
    assert plugin_api.ledger_is_healthy(
        {**ledger, "last_event_at": future_boundary}, now=now
    )
    assert not plugin_api.ledger_is_healthy(
        {**ledger, "last_event_at": future_over}, now=now
    )


def test_snapshot_rejects_missing_or_malformed_fill_timestamps():
    now = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
    timestamp = now.isoformat()
    valid_fill = {
        "id": "FF-00001",
        "order_id": "FP-00001",
        "symbol": "PF_ETHUSD",
        "side": "Long",
        "size": 0.038,
        "price": 1936.8,
        "fee": 0.0368,
        "filled_at": timestamp,
    }
    valid_state = _valid_flat_state(timestamp)
    valid_state["fills"] = [valid_fill]

    assert (
        plugin_api.build_paper_snapshot(
            valid_state,
            _valid_ticker(timestamp),
            now=now,
        )["valid"]
        is True
    )

    for field in ("id", "order_id"):
        fill = {**valid_fill, field: " \t "}
        state = _valid_flat_state(timestamp)
        state["fills"] = [fill]

        result = plugin_api.build_paper_snapshot(
            state,
            _valid_ticker(timestamp),
            now=now,
        )

        assert result["valid"] is False, field
        assert any(field in error for error in result["validation_errors"]), field

    missing = object()
    for malformed in (
        missing,
        None,
        "",
        "not-a-timestamp",
        "2026-07-28T00:00:00",
        "2026-02-30T00:00:00Z",
        "0001-01-01T00:00:00+00:01",
        1785196800,
    ):
        fill = dict(valid_fill)
        if malformed is missing:
            del fill["filled_at"]
        else:
            fill["filled_at"] = malformed
        state = _valid_flat_state(timestamp)
        state["fills"] = [fill]

        result = plugin_api.build_paper_snapshot(
            state,
            _valid_ticker(timestamp),
            now=now,
        )

        assert result["valid"] is False
        assert any("filled_at" in error for error in result["validation_errors"])


def test_automation_status_rejects_stale_service_and_timer_timestamps():
    result = plugin_api.build_automation_status(
        {
            "Result": "success",
            "ExecMainStatus": "0",
            "ExecMainStartTimestamp": "Sat 2000-01-01 00:00:00 UTC",
            "ExecMainExitTimestamp": "Sat 2000-01-01 00:00:01 UTC",
        },
        {
            "ActiveState": "active",
            "UnitFileState": "enabled",
            "LastTriggerUSec": "Sat 2000-01-01 00:00:00 UTC",
            "NextElapseUSecRealtime": "Sat 2000-01-01 04:00:00 UTC",
        },
        {"PAPER_EXECUTE": "1"},
        kill_switch_armed=False,
        now=datetime(2026, 7, 28, 0, 0, tzinfo=UTC),
    )

    assert result["healthy"] is False
    assert result["timestamps_fresh"] is False


def test_automation_status_contains_timestamp_normalisation_overflow():
    overflow = "Fri 9999-12-31 23:59:59 EDT"
    assert plugin_api._parse_systemd_timestamp(overflow) is None

    result = plugin_api.build_automation_status(
        {
            "Result": "success",
            "ExecMainStatus": "0",
            "ExecMainStartTimestamp": overflow,
            "ExecMainExitTimestamp": overflow,
        },
        {
            "ActiveState": "active",
            "UnitFileState": "enabled",
            "LastTriggerUSec": overflow,
            "NextElapseUSecRealtime": overflow,
        },
        {"PAPER_EXECUTE": "1"},
        kill_switch_armed=False,
        now=datetime(2026, 7, 28, 0, 0, tzinfo=UTC),
    )

    assert result["healthy"] is False
    assert result["timestamps_fresh"] is False


def test_ledger_health_rejects_stale_or_malformed_last_event_time():
    now = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
    base = {"quick_check": "ok", "chain_valid": True, "event_count": 2}

    assert plugin_api.ledger_is_healthy(
        {**base, "last_event_at": (now - timedelta(minutes=5)).isoformat()},
        now=now,
    )
    assert not plugin_api.ledger_is_healthy(
        {**base, "last_event_at": (now - timedelta(days=1)).isoformat()},
        now=now,
    )
    assert not plugin_api.ledger_is_healthy(
        {**base, "last_event_at": "invalid"}, now=now
    )


def test_numeric_booleans_overflow_and_nonfinite_history_fail_closed():
    timestamp = "2026-07-28T00:00:00+00:00"
    now = datetime.fromisoformat(timestamp)
    for field, value in (
        ("starting_collateral", True),
        ("collateral", False),
    ):
        state = {**_valid_flat_state(timestamp), field: value}
        assert (
            plugin_api.build_paper_snapshot(state, _valid_ticker(timestamp), now=now)[
                "valid"
            ]
            is False
        )

    overflow_state = {
        **_valid_flat_state(timestamp),
        "positions": [
            {
                "symbol": "PF_ETHUSD",
                "side": "Long",
                "size": 1e308,
                "entry_price": 1e308,
                "leverage": 1.0,
                "unrealized_funding": 0.0,
            }
        ],
        "open_orders": [],
    }
    overflow_ticker = {
        **_valid_ticker(timestamp),
        "ticker": {"symbol": "PF_ETHUSD", "markPrice": 1e308, "indexPrice": 1e308},
    }
    overflow = plugin_api.build_paper_snapshot(overflow_state, overflow_ticker, now=now)
    assert overflow["valid"] is False
    json.dumps(overflow, allow_nan=False)

    nonfinite_state = _valid_flat_state(timestamp)
    nonfinite_state["history"] = [{"pnl": float("nan")}]
    nonfinite = plugin_api.build_paper_snapshot(
        nonfinite_state, _valid_ticker(timestamp), now=now
    )
    assert nonfinite["valid"] is False
    json.dumps(nonfinite, allow_nan=False)


def test_json_read_and_journal_reject_excessive_nesting_and_nonfinite_values():
    with tempfile.TemporaryDirectory(dir=Path.home()) as temporary_directory:
        state_path = Path(temporary_directory) / "state.json"
        state_path.write_text(
            '{"history":' + "[" * 1500 + "0" + "]" * 1500 + "}",
            encoding="utf-8",
        )
        with unittest.TestCase().assertRaises(ValueError):
            plugin_api._read_json_file(state_path)

    journal = json.dumps(
        {
            "__REALTIME_TIMESTAMP": "1785182702000000",
            "MESSAGE": '{"action":"position_observed","size":NaN}',
        }
    )
    assert plugin_api.parse_journal_cycles(journal) == []


def test_kill_switch_status_rejects_relative_paths():
    armed, error = plugin_api._kill_switch_status("relative/KILL_SWITCH")
    assert armed is True
    assert error

    with tempfile.TemporaryDirectory(dir=Path.home()) as temporary_directory:
        missing_absolute = str(Path(temporary_directory) / "KILL_SWITCH")
        armed, error = plugin_api._kill_switch_status(missing_absolute)
        assert armed is False
        assert error is None


def test_kill_switch_status_rejects_symlinked_or_untrusted_ancestors():
    with tempfile.TemporaryDirectory(dir=Path.home()) as temporary_directory:
        root = Path(temporary_directory)
        target = root / "target"
        nested = target / "nested"
        nested.mkdir(parents=True)
        alias = root / "alias"
        alias.symlink_to(target, target_is_directory=True)

        armed, error = plugin_api._kill_switch_status(
            str(alias / "nested" / "KILL_SWITCH")
        )
        assert armed is True
        assert error

        unsafe = root / "unsafe"
        unsafe.mkdir(mode=0o700)
        (unsafe / "nested").mkdir(mode=0o700)
        unsafe.chmod(0o777)
        try:
            armed, error = plugin_api._kill_switch_status(
                str(unsafe / "nested" / "KILL_SWITCH")
            )
        finally:
            unsafe.chmod(0o700)

        assert armed is True
        assert error


def test_paper_dashboard_fails_closed_for_symlinked_kill_switch_ancestor():
    with tempfile.TemporaryDirectory(dir=Path.home()) as temporary_directory:
        root = Path(temporary_directory)
        target = root / "target"
        (target / "nested").mkdir(parents=True)
        alias = root / "alias"
        alias.symlink_to(target, target_is_directory=True)
        kill_switch = alias / "nested" / "KILL_SWITCH"

        async def fake_run_text(command, *, timeout=15.0):
            del timeout
            if plugin_api.SERVICE_UNIT in command and "show" in command:
                return {
                    "ok": True,
                    "stdout": (
                        "ActiveState=inactive\nSubState=dead\nResult=success\n"
                        "ExecMainStatus=0\n"
                        f"Environment=PAPER_EXECUTE=1 KILL_SWITCH_PATH={kill_switch}\n"
                    ),
                    "message": "",
                }
            if plugin_api.TIMER_UNIT in command:
                return {
                    "ok": True,
                    "stdout": "ActiveState=active\nUnitFileState=enabled\n",
                    "message": "",
                }
            return {"ok": True, "stdout": "", "message": ""}

        with (
            mock.patch.object(plugin_api, "_run_text", fake_run_text),
            mock.patch.object(
                plugin_api,
                "_resolve_system_binary",
                lambda _name: "/usr/bin/true",
            ),
            mock.patch.object(plugin_api, "_read_json_file", lambda _path: {}),
            mock.patch.object(plugin_api, "_resolve_kraken_cli", lambda _env: None),
        ):
            result = asyncio.run(plugin_api.paper_dashboard())

        assert result["healthy"] is False
        assert result["automation"]["kill_switch_armed"] is True
        assert any("kill-switch" in error for error in result["errors"])


def test_kill_switch_status_fails_closed_when_path_cannot_be_inspected():
    with tempfile.TemporaryDirectory(dir=Path.home()) as temporary_directory:
        blocked_parent = Path(temporary_directory) / "blocked"
        blocked_parent.mkdir(mode=0o700)
        blocked_parent.chmod(0)
        try:
            armed, error = plugin_api._kill_switch_status(
                str(blocked_parent / "KILL_SWITCH")
            )
        finally:
            blocked_parent.chmod(0o700)

        assert armed is True
        assert error

    armed, error = plugin_api._kill_switch_status(
        "~hermes-dashboard-user-does-not-exist/KILL_SWITCH"
    )
    assert armed is True
    assert error


def test_experiment_metadata_binds_seal_to_configured_ledger_path():
    with tempfile.TemporaryDirectory() as temporary_directory:
        commit = "a" * 40
        runtime_path = Path(temporary_directory) / commit / "runtime"
        seals = runtime_path / "forward-experiments"
        seals.mkdir(parents=True)
        sealed_ledger = runtime_path / "paper-ledger.sqlite3"
        alternate_ledger = runtime_path / "alternate-ledger.sqlite3"
        seal = {
            "schema_version": 1,
            "experiment": {
                "experiment_id": "eth-pf-test-v1",
                "start_at": "2026-07-27T18:28:00Z",
            },
            "git": {"commit": commit, "tree": "b" * 40},
            "contract": {
                "mode": "futures_paper",
                "symbol": "PF_ETHUSD",
                "account_baseline_usd": 400.0,
                "max_notional_usd": 75.0,
                "leverage": 1.0,
                "allowed_sides": ["long"],
            },
            "ledger": {"path": str(sealed_ledger)},
        }
        (seals / "seal.json").write_text(json.dumps(seal), encoding="utf-8")

        assert (
            plugin_api._experiment_metadata(
                runtime_path,
                ledger_path=sealed_ledger,
            )["experiment_id"]
            == "eth-pf-test-v1"
        )
        assert (
            plugin_api._experiment_metadata(
                runtime_path,
                ledger_path=alternate_ledger,
            )
            == {}
        )


def test_experiment_contract_is_correlated_with_snapshot_and_ledger():
    now = datetime(2026, 7, 28, 0, 10, tzinfo=UTC)
    experiment = {
        "experiment_id": "wrong-contract",
        "start_at": "2026-07-28T00:00:00+00:00",
        "symbol": "PF_XBTUSD",
        "account_baseline_usd": 999.0,
        "max_notional_usd": 75.0,
        "leverage": 1.0,
        "allowed_sides": ["long"],
        "provenance_valid": True,
    }
    paper = {
        "account": {"starting_collateral": 400.0, "exposure_usd": 84.0},
        "market": {"symbol": "PF_ETHUSD"},
        "positions": [],
        "leverage_preferences": {"PF_ETHUSD": 1.0},
        "last_reconciled_at": "2026-07-28T00:05:00+00:00",
    }
    ledger = {"last_event_at": "2026-07-28T00:05:00+00:00"}

    errors = plugin_api._experiment_contract_errors(experiment, paper, ledger, now=now)

    assert any("symbol" in error for error in errors)
    assert any("baseline" in error for error in errors)
    assert any("notional" in error for error in errors)

    future = {**experiment, "start_at": "2026-07-29T00:00:00+00:00"}
    assert any(
        "start" in error
        for error in plugin_api._experiment_contract_errors(
            future, paper, ledger, now=now
        )
    )


def test_experiment_contract_compares_clock_skew_at_nanosecond_precision():
    now = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
    paper = {
        "account": {"starting_collateral": 400.0, "exposure_usd": 0.0},
        "market": {"symbol": "PF_ETHUSD"},
        "positions": [],
        "leverage_preferences": {"PF_ETHUSD": 1.0},
        "last_reconciled_at": "2026-07-28T00:00:00Z",
    }
    ledger = {"last_event_at": "2026-07-28T00:00:00Z"}

    def errors_for(started_at: str) -> list[str]:
        return plugin_api._experiment_contract_errors(
            {
                "experiment_id": "nanosecond-boundary",
                "start_at": started_at,
                "started_at": started_at,
                "symbol": "PF_ETHUSD",
                "account_baseline_usd": 400.0,
                "max_notional_usd": 75.0,
                "leverage": 1.0,
                "allowed_sides": ["long"],
                "provenance_valid": True,
            },
            paper,
            ledger,
            now=now,
        )

    assert errors_for("2026-07-28T00:00:30.000000000Z") == []
    assert any(
        "start time" in error for error in errors_for("2026-07-28T00:00:30.000000001Z")
    )


def test_experiment_contract_uses_unrounded_snapshot_risk_values():
    timestamp = "2026-07-28T00:05:00+00:00"
    now = datetime.fromisoformat(timestamp)
    state = {
        **_valid_flat_state(timestamp),
        "starting_collateral": 400.00000000000006,
        "positions": [
            {
                "symbol": "PF_ETHUSD",
                "side": "Long",
                "size": 1.0,
                "entry_price": 100.0,
                "leverage": 1.0,
                "unrealized_funding": 0.0,
            }
        ],
    }
    ticker = {
        "result": "success",
        "serverTime": timestamp,
        "ticker": {
            "symbol": "PF_ETHUSD",
            "markPrice": 100.00000000000001,
            "indexPrice": 100.0,
        },
    }
    paper = plugin_api.build_paper_snapshot(state, ticker, now=now)
    experiment = {
        "experiment_id": "strict-risk-contract",
        "start_at": "2026-07-28T00:00:00+00:00",
        "symbol": "PF_ETHUSD",
        "account_baseline_usd": 400.0,
        "max_notional_usd": 100.0,
        "leverage": 1.0,
        "allowed_sides": ["long"],
        "provenance_valid": True,
    }
    ledger = {"last_event_at": timestamp}

    errors = plugin_api._experiment_contract_errors(experiment, paper, ledger, now=now)

    assert any("baseline" in error for error in errors)
    assert any("notional" in error for error in errors)


def _synthetic_paper_dashboard(
    *,
    release: str,
    experiment_commit: str,
    journal_output: str,
    paper_snapshot: dict[str, Any] | None = None,
    service_environment: str | None = None,
) -> dict[str, Any]:
    ledger_path = f"/home/test/releases/{release}/runtime/paper-ledger.sqlite3"
    environment_text = (
        f"LEDGER_PATH={ledger_path}"
        if service_environment is None
        else service_environment
    )

    async def fake_run_text(command, *, timeout=15.0):
        del timeout
        command_text = " ".join(command)
        if "journalctl" in command_text:
            return {"ok": True, "stdout": journal_output, "message": ""}
        if "eth-paper-cycle.service" in command_text:
            return {
                "ok": True,
                "stdout": f"Environment={environment_text}\n",
                "message": "",
            }
        if "eth-paper-cycle.timer" in command_text:
            return {"ok": True, "stdout": "", "message": ""}
        return {"ok": True, "stdout": "", "message": ""}

    paper = (
        {
            "valid": True,
            "validation_errors": [],
            "compliance": {"synthetic_baseline": True},
        }
        if paper_snapshot is None
        else paper_snapshot
    )
    automation = {"healthy": True, "release": release}
    ledger = {"healthy": True}
    experiment = {"commit": experiment_commit}
    with (
        mock.patch.object(plugin_api, "_run_text", fake_run_text),
        mock.patch.object(
            plugin_api,
            "_resolve_system_binary",
            lambda name: f"/usr/bin/{name}",
        ),
        mock.patch.object(plugin_api, "_read_json_file", lambda _path: {}),
        mock.patch.object(
            plugin_api, "_kill_switch_status", lambda _value: (False, None)
        ),
        mock.patch.object(
            plugin_api,
            "build_automation_status",
            lambda *args, **kwargs: automation,
        ),
        mock.patch.object(
            plugin_api, "build_paper_snapshot", lambda *args, **kwargs: paper
        ),
        mock.patch.object(
            plugin_api, "read_ledger_summary", lambda *args, **kwargs: ledger
        ),
        mock.patch.object(
            plugin_api, "ledger_is_healthy", lambda _ledger, **_kwargs: True
        ),
        mock.patch.object(
            plugin_api,
            "_experiment_metadata",
            lambda *args, **kwargs: experiment,
        ),
        mock.patch.object(
            plugin_api,
            "_experiment_contract_errors",
            lambda *args, **kwargs: [],
        ),
    ):
        return asyncio.run(plugin_api.paper_dashboard())


def test_paper_dashboard_rejects_release_experiment_mismatch():
    release = "a" * 40
    realtime = str(int(datetime(2026, 7, 28, tzinfo=UTC).timestamp() * 1_000_000))
    result = _synthetic_paper_dashboard(
        release=release,
        experiment_commit="b" * 40,
        journal_output=json.dumps(
            {
                "MESSAGE": json.dumps({"action": "hold", "blockers": []}),
                "__REALTIME_TIMESTAMP": realtime,
            }
        ),
    )

    assert result["healthy"] is False
    assert result["experiment"]["contract_valid"] is False
    assert any("release" in error and "commit" in error for error in result["errors"])


def test_paper_dashboard_rejects_malformed_structured_journal_cycles():
    release = "a" * 40
    result = _synthetic_paper_dashboard(
        release=release,
        experiment_commit=release,
        journal_output=json.dumps(
            {
                "MESSAGE": json.dumps({"action": "", "blockers": {"bad": True}}),
                "__REALTIME_TIMESTAMP": "not-microseconds",
            }
        ),
    )

    assert result["healthy"] is False
    assert result["cycles"] == []
    assert any("journal" in error for error in result["errors"])


def test_paper_dashboard_ignores_unrelated_json_scalar_journal_records():
    release = "a" * 40
    result = _synthetic_paper_dashboard(
        release=release,
        experiment_commit=release,
        journal_output="\n".join(json.dumps(value) for value in (7, [], None)),
    )

    assert result["healthy"] is True
    assert result["cycles"] == []
    assert result["errors"] == []


def test_paper_dashboard_rejects_malformed_service_environment_serialization():
    release = "a" * 40
    ledger_path = f"/home/test/releases/{release}/runtime/paper-ledger.sqlite3"
    result = _synthetic_paper_dashboard(
        release=release,
        experiment_commit=release,
        journal_output=json.dumps(7),
        service_environment=f'PAPER_EXECUTE=1 LEDGER_PATH={ledger_path} "',
    )

    assert result["healthy"] is False
    assert any("service environment" in error for error in result["errors"])


def test_paper_dashboard_rejects_non_ascii_journal_microseconds():
    release = "a" * 40
    timestamp = "1785182700000000".translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
    result = _synthetic_paper_dashboard(
        release=release,
        experiment_commit=release,
        journal_output=json.dumps(
            {
                "MESSAGE": json.dumps({"action": "hold", "blockers": []}),
                "__REALTIME_TIMESTAMP": timestamp,
            },
            ensure_ascii=False,
        ),
    )

    assert result["healthy"] is False
    assert result["cycles"] == []
    assert any("timestamp" in error for error in result["errors"])


def test_paper_dashboard_rejects_whitespace_fill_identities():
    release = "a" * 40
    now = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
    timestamp = now.isoformat()
    state = _valid_flat_state(timestamp)
    state["fills"] = [
        {
            "id": " \t ",
            "order_id": "FP-00001",
            "symbol": "PF_ETHUSD",
            "side": "Long",
            "size": 0.038,
            "price": 1936.8,
            "fee": 0.0368,
            "filled_at": timestamp,
        }
    ]
    snapshot = plugin_api.build_paper_snapshot(
        state,
        _valid_ticker(timestamp),
        now=now,
    )
    realtime = str(int(now.timestamp() * 1_000_000))

    result = _synthetic_paper_dashboard(
        release=release,
        experiment_commit=release,
        journal_output=json.dumps(
            {
                "MESSAGE": json.dumps({"action": "hold", "blockers": []}),
                "__REALTIME_TIMESTAMP": realtime,
            }
        ),
        paper_snapshot=snapshot,
    )

    assert result["healthy"] is False
    assert result["paper"]["valid"] is False
    assert any("fills[0].id" in error for error in result["errors"])


class PluginApiTests(unittest.TestCase):
    """Expose the behavior tests through the standard-library runner."""


def _attach_tests() -> None:
    for name, function in list(globals().items()):
        if name.startswith("test_") and callable(function):
            setattr(PluginApiTests, name, staticmethod(function))


_attach_tests()


if __name__ == "__main__":
    unittest.main()
