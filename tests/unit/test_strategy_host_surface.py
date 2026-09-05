from __future__ import annotations

import pytest

from backtest_engine import BacktestConfig
from backtest_engine.core.delegated_strategy_intents import (
    DelegatedStrategyIntentHandler,
    build_delegated_strategy_dispatcher,
)
from backtest_engine.core.intent_replay import IntentReplayIdentity
from backtest_engine.core.strategy_capabilities import (
    STRATEGY_COMMANDS,
    OWNER,
    DELEGATION_SCHEMA_ID,
)
from openpine_contracts import validate_payload, verify_content_hash
from pinelib import CallbackFrame, RuntimeLanguageContext, RuntimeSession
from pinelib.events import SourceSpan
from pinelib.errors import PineRuntimeError


def handler(version=6, **kwargs):
    return DelegatedStrategyIntentHandler(
        identity=IntentReplayIdentity(
            "run", "strategy", "sha256:" + "b" * 64, "strict_5x", "series", "symbol", "1m"
        ),
        producer_commit="c" * 40,
        bar_open_time_utc_ms={0: 60_000},
        config=BacktestConfig("S", "1m", 0, 60_000),
        pine_version=version,
        **kwargs,
    )


def transaction(h, version=6):
    runtime = RuntimeSession(
        RuntimeLanguageContext(
            version, "snapshot", f"pine-v{version}", "sha256:" + "a" * 64, "compiler_annotation"
        ),
        delegated_dispatcher=build_delegated_strategy_dispatcher(h),
    )
    return runtime.begin(CallbackFrame("HISTORICAL_EVAL", 1, bar_index=0))


def dispatch(tx, name, positional=(), named=None, line=1):
    spec = STRATEGY_COMMANDS[name]
    return tx.dispatch_delegated(
        owner=OWNER,
        schema_id=DELEGATION_SCHEMA_ID,
        capability_id=name,
        symbol_id=spec.symbol_id,
        overload_id=spec.overload_id,
        arguments={"positional": list(positional), "named": named or {}},
        call_site_id=f"site:{line}",
        source_span=SourceSpan("sha256:" + "a" * 64, "test.pine", line, 0, line, 12),
    )


def seal(h, tx):
    return h.seal_committed([o.value for o in tx.commit().delegated_outputs])


@pytest.mark.parametrize(
    "name,args,named,kind",
    [
        ("strategy.entry", ("L", "strategy.long"), {"qty": 2}, "entry"),
        ("strategy.order", ("L", "strategy.short"), {"qty": 2}, "order"),
        ("strategy.close", ("L",), {"qty_percent": 25, "immediately": True}, "close"),
        ("strategy.close_all", (), {"immediately": True}, "close_all"),
        ("strategy.cancel", ("L",), {}, "cancel"),
        ("strategy.cancel_all", (), {}, "cancel_all"),
        ("strategy.exit", ("X", "L"), {"limit": 101, "stop": 99, "qty_percent": 50}, "exit"),
    ],
)
def test_all_order_handlers_seal_exact_contracts(name, args, named, kind):
    h = handler(open_entry_ids=("L",))
    tx = transaction(h)
    dispatch(tx, name, args, named)
    (event,) = seal(h, tx)
    assert event["kind"] == kind
    validate_payload("openpine.intent.v2", event)
    assert verify_content_hash(event, schema_id="openpine.intent.v2")
    if "immediately" in named:
        assert event["immediately"] is True
    if "qty_percent" in named:
        assert event["qty_percent"] == str(named["qty_percent"])


@pytest.mark.parametrize("version", range(1, 6))
@pytest.mark.parametrize(
    "name,args",
    [
        ("strategy.entry", ("L", "strategy.long")),
        ("strategy.order", ("L", "strategy.long")),
        ("strategy.close", ("L",)),
        ("strategy.close_all", ()),
        ("strategy.cancel", ("L",)),
        ("strategy.cancel_all", ()),
        ("strategy.exit", ("X", "L")),
    ],
)
def test_historical_when_suppresses_and_preserves_contiguous_sequence(version, name, args):
    h = handler(version, open_entry_ids=("L",))
    tx = transaction(h, version)
    extras = {"limit": 101} if name == "strategy.exit" else {}
    dispatch(tx, name, args, {**extras, "when": False}, line=1)
    dispatch(tx, name, args, {**extras, "when": True}, line=2)
    (event,) = seal(h, tx)
    assert event["sequence"] == 0 and event["source_span"]["start_line"] == 2


@pytest.mark.parametrize(
    "named,message",
    [
        ({"trail_price": 101, "trail_offset": 2}, "unsupported host parameters"),
        ({"comment_profit": "TP"}, "unsupported host parameters"),
        ({"limit": 101, "profit": 2}, "parameter pairs"),
        ({}, "active price leg"),
    ],
)
def test_unsupported_exits_fail_without_silent_parameter_loss(named, message):
    h = handler(open_entry_ids=("L",))
    tx = transaction(h)
    dispatch(tx, "strategy.exit", ("X", "L"), named)
    with pytest.raises(PineRuntimeError) as exc:
        seal(h, tx)
    assert message in str(exc.value.__cause__)


def test_pending_entry_exit_is_explicitly_rejected():
    h = handler(open_entry_ids=())
    tx = transaction(h)
    dispatch(tx, "strategy.entry", ("L", "strategy.long"))
    dispatch(tx, "strategy.exit", ("X", "L"), {"stop": 99}, line=2)
    with pytest.raises(PineRuntimeError) as exc:
        seal(h, tx)
    assert "pending-entry exits" in str(exc.value.__cause__)


def test_v5_absolute_exit_parameter_takes_precedence_in_existing_engine():
    h = handler(5, open_entry_ids=("L",))
    tx = transaction(h, 5)
    dispatch(tx, "strategy.exit", ("X", "L"), {"limit": 102, "profit": 4})
    (event,) = seal(h, tx)
    assert (event["limit"], event["profit"]) == ("102", "4")


@pytest.mark.parametrize("value,expected", [(0, 0), (1, 1), (-2, 1), (0.0, 0), (0.5, 1)])
def test_historical_numeric_when_uses_pine_boolean_conversion(value, expected):
    h = handler(5)
    tx = transaction(h, 5)
    dispatch(tx, "strategy.order", ("L", "strategy.long", 3), {"when": value})
    events = seal(h, tx)
    assert len(events) == expected
    if expected:
        assert events[0]["qty"] == "3"


@pytest.mark.parametrize("name", list(STRATEGY_COMMANDS))
def test_every_binding_rejects_duplicate_or_unknown_arguments(name):
    spec = STRATEGY_COMMANDS[name]
    with pytest.raises(ValueError):
        spec.bind((), {"typo": True}, 6)
    with pytest.raises(ValueError):
        spec.bind((), {"when": True}, 6)
    if spec.required:
        with pytest.raises(ValueError, match="repeats"):
            spec.bind(["L"], {spec.parameters[0]: "L"}, 6)


def test_state_snapshots_are_detached_and_flat_positions_expose_na():
    from backtest_engine.context.strategy_state_view import StrategyStateView
    from backtest_engine.core.strategy_capabilities import strategy_values_from_state
    from pinelib import is_na

    cfg = BacktestConfig("S", "1m", 0, 60_000)
    state = StrategyStateView(equity=100, closed_trades=7)
    values = strategy_values_from_state(state, cfg)
    assert values["strategy.closedtrades"] == 7
    assert is_na(values["strategy.position_avg_price"])
    assert is_na(values["strategy.position_entry_name"])
    state.closed_trades = 10
    state.equity = 200
    assert values["strategy.closedtrades"] == 7 and values["strategy.equity"] == 100


@pytest.mark.parametrize("version", range(1, 6))
@pytest.mark.parametrize(
    "name,args", [("strategy.close", ["L", True]), ("strategy.close_all", [True])]
)
def test_historical_close_overloads_never_guess_positional_when(version, name, args):
    with pytest.raises(ValueError, match="must be named"):
        STRATEGY_COMMANDS[name].bind(args, {}, version)
