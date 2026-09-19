"""Risk and projection boundaries, with explicit producer/converter fault injection."""
from dataclasses import replace

import pytest
from pinelib import is_na

from backtest_engine import BacktestConfig, BacktestEngine
from backtest_engine.context import StrategyContext
from backtest_engine.context.strategy_context import RiskRule
from backtest_engine.core import risk_rules
from backtest_engine.core.strategy_callback import call_strategy
from backtest_engine.core.strategy_capabilities import strategy_values_from_projection
from backtest_engine.core.strategy_projection import _trade_order_comment
from backtest_engine.errors import StrategyRuntimeError, UnsupportedRiskRuleError
from backtest_engine.models import Bar, Order


def engine():
    return BacktestEngine(BacktestConfig("S", "1m", 0, 120000))


def order(**updates):
    result = Order("A", "entry", "long", "buy", "open", "market", 1, 0, 0, 1)
    return replace(result, **updates)


@pytest.mark.parametrize(
    "bad",
    [
        RiskRule("max_drawdown", value=1, value_type="unknown"),
        RiskRule("max_position_size", value=1, value_type="cash"),
        RiskRule("allow_entry_in", direction="unknown"),
    ],
)
def test_invalid_risk_batch_never_partially_changes_policy(bad):
    broker = engine()
    before = risk_rules.capture_risk_state(broker)
    ctx = StrategyContext(broker.config)
    ctx.risk_rules.extend([RiskRule("allow_entry_in", direction="short"), bad])
    with pytest.raises(UnsupportedRiskRuleError):
        risk_rules.apply_risk_rules(broker, ctx)
    assert risk_rules.capture_risk_state(broker) == before


def test_risk_rejects_producer_changing_rule_between_validation_and_application():
    # The native Python callback API can supply a list subclass. No guard is mocked.
    class ChangingBatch(list):
        reads = 0

        def __iter__(self):
            self.reads += 1
            if self.reads == 1:
                return iter([RiskRule("allow_entry_in", direction="long")])
            return iter([RiskRule("unknown")])

    class Producer(StrategyContext):
        def drain_risk_rules(self):
            return ChangingBatch()

    broker = engine()
    with pytest.raises(UnsupportedRiskRuleError):
        risk_rules.apply_risk_rules(broker, Producer(broker.config))


def test_huge_integer_risk_limit_is_not_representable_as_runtime_float():
    with pytest.raises(ValueError, match="outside the runtime range"):
        risk_rules.validate_position_limit(10**400)


def test_numeric_conversion_loss_is_rejected_explicitly(monkeypatch):
    # Fault injection at conversion, not at the condition or validation result.
    class LossyFloat(float):
        def __new__(cls, value):
            return super().__new__(cls, 0.0)

    monkeypatch.setattr(risk_rules, "float", LossyFloat, raising=False)
    with pytest.raises(ValueError, match="outside the runtime range"):
        risk_rules.validate_position_limit(1)


@pytest.mark.parametrize(
    "key,value",
    [("_max_position_size", 10**400), ("_max_position_size", -1),
     ("_max_drawdown_stop_cash", float("nan")), ("_max_bars_without_trade", 1.5)],
)
def test_risk_snapshot_values_have_exact_finite_domains(key, value):
    state = risk_rules.capture_risk_state(engine())
    state[key] = value
    with pytest.raises(ValueError):
        risk_rules.validate_risk_state(state)


def test_fill_rechecks_direction_and_clears_attached_exits():
    broker = engine()
    broker._allow_long = False
    pending = order(pending_exits={"X": {"profit": 1}})
    assert not risk_rules.enforce_entry_fill_risk(broker, pending, Bar(0, 1, 1, 1, 1), 0)
    assert pending.status == "cancelled" and pending.pending_exits == {}
    assert broker.fills == []


@pytest.mark.parametrize("direction,quantity", [("FLAT", 0.0), ("LONG", 2.0), ("SHORT", -2.0)])
def test_projection_uses_total_trade_counts_and_signed_position(direction, quantity):
    projection = {
        "position": {"direction": direction, "qty": "2" if direction != "FLAT" else "0",
                     "avg_price": "50" if direction != "FLAT" else None,
                     "entry_name": "A" if direction != "FLAT" else None},
        "equity": "105", "realized_pnl": "5", "unrealized_pnl": "0",
        "gross_profit": "7", "gross_loss": "-2", "max_drawdown": "2", "max_runup": "7",
        "winning_trades": 2, "losing_trades": 1, "even_trades": 0,
        "open_trades": [] if direction == "FLAT" else [{"entry_id": "A"}], "currency": "USD",
    }
    config = BacktestConfig("S", "1m", 0, 1, initial_capital=100)
    values = strategy_values_from_projection(projection, config)
    assert values["strategy.position_size"] == quantity
    assert values["strategy.closedtrades"] == 3
    assert values["strategy.netprofit"] == 5.0
    assert values["strategy.initial_capital"] == 100
    assert values["strategy.account_currency"] == "USD"
    if direction == "FLAT":
        assert is_na(values["strategy.position_avg_price"])
        assert is_na(values["strategy.position_entry_name"])
    else:
        assert values["strategy.position_avg_price"] == 50.0
        assert values["strategy.position_entry_name"] == "A"


def test_legacy_order_comment_uses_latest_eligible_order_not_future_reuse():
    rows = [order(comment="old"), order(comment="same-time-latest"),
            order(comment="future", created_bar_index=2),
            order(comment="exit", position_effect="close")]
    assert _trade_order_comment(rows, order_id="A", at_bar_index=1, is_entry=True) == "same-time-latest"
    assert _trade_order_comment(rows, order_id="A", at_bar_index=1, is_entry=False) == "exit"
    assert _trade_order_comment(rows, order_id="missing", at_bar_index=1, is_entry=True) is None
    assert _trade_order_comment([order(comment=None)], order_id="A", at_bar_index=1, is_entry=True) is None


def test_execution_callback_cannot_fabricate_dataset_bounds():
    class CallbackStrategy:
        def run_callback(self, bar, event):
            pytest.fail("callback must not receive a fabricated event")

    with pytest.raises(StrategyRuntimeError, match="explicit chart dataset bounds"):
        call_strategy(engine(), CallbackStrategy(), Bar(0, 1, 1, 1, 1), 0)


def test_empty_legacy_exit_views_have_zero_reservations_and_current_price():
    broker = engine()
    assert broker._reserved_exit_qty(None) == 0
    assert broker._reserved_exit_qty("missing") == 0
    assert broker._exit_base_price(None) == broker.position.avg_price
