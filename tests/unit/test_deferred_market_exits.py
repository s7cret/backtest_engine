"""Documented market-entry bracket scenarios, with explicit price-path assertions."""

import pytest

from backtest_engine import BacktestConfig, BacktestEngine
from backtest_engine.models import Bar
from openpine_contracts import Finality


def candles(*ohlc):
    return [
        Bar(
            time=i * 60_000,
            open=opened,
            high=high,
            low=low,
            close=closed,
            volume=1,
            finality=Finality.FINAL,
        )
        for i, (opened, high, low, closed) in enumerate(ohlc)
    ]


def run(commands, rows, **options):
    class Strategy:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx
            self.sent = set()

        def run_bar(self, bar, bar_index):
            # Recalculations deliberately do not submit duplicate commands.
            if bar_index not in self.sent:
                self.sent.add(bar_index)
                commands(self.ctx, bar_index)

    cfg = BacktestConfig(
        "S",
        "1m",
        0,
        (len(rows) - 1) * 60_000,
        commission_type="none",
        commission_value=0,
        mintick=1,
        force_close_on_end=False,
        **options,
    )
    engine = BacktestEngine(cfg)
    return engine, engine.run(Strategy, bars=rows)


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("relative", [False, True])
@pytest.mark.parametrize("recalc", [False, True])
@pytest.mark.parametrize("on_close", [False, True])
def test_same_callback_market_entry_and_exit(direction, relative, recalc, on_close):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("L", direction, qty=2)
            ctx.exit(
                "X",
                "L",
                **(
                    {"profit": 5, "loss": 7}
                    if relative
                    else {
                        "limit": 105 if direction == "long" else 95,
                        "stop": 93 if direction == "long" else 107,
                    }
                ),
            )

    # Low first: long TP afterwards; short TP occurs at low. No ambiguous stop.
    rows = candles((100, 101, 99, 100), (100, 106, 94, 100), (100, 101, 99, 100))
    engine, result = run(
        commands, rows, calc_on_order_fills=recalc, process_orders_on_close=on_close
    )
    assert result.status == "completed", result.errors
    assert len(result.closed_trades) == 1
    trade = result.closed_trades[0]
    assert (trade.entry_bar_index, trade.exit_bar_index) == (0 if on_close else 1, 1)
    assert (trade.entry_price, trade.exit_price, trade.qty) == (
        100,
        105 if direction == "long" else 95,
        2,
    )
    assert [f.order_id for f in engine.fills] == ["L", "X:L"]
    assert not result.open_trades


@pytest.mark.parametrize("cancel", ["exit", "entry", "all"])
def test_cancellation_before_market_fill_never_resurrects_template(cancel):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("L", "long")
            ctx.exit("X", "L", limit=105, stop=90)
            if cancel == "all":
                ctx.cancel_all()
            else:
                ctx.cancel("X" if cancel == "exit" else "L")
        if i == 2 and cancel != "exit":
            ctx.entry("L", "long")  # Same ID is a new order, not the old registration.

    engine, result = run(
        commands,
        candles((100, 101, 99, 100), (100, 106, 99, 100), (100, 101, 99, 100), (100, 106, 99, 100)),
    )
    assert result.status == "completed", result.errors
    assert len(result.open_trades) == 1 and not result.closed_trades
    assert not any(order.kind == "exit" for order in engine.orders)


def test_latest_exit_payload_replaces_waiting_bracket_and_unmatched_is_noop():
    def commands(ctx, i):
        if i == 0:
            ctx.exit("absent", "missing", limit=105)
            ctx.entry("L", "long", qty=4)
            ctx.exit("X", "L", limit=105, stop=90)
            ctx.exit("X", "L", limit=110, qty=1)

    engine, result = run(
        commands, candles((100, 101, 99, 100), (100, 108, 99, 100), (100, 111, 99, 100))
    )
    assert result.status == "completed", result.errors
    assert [(t.exit_bar_index, t.exit_price, t.qty) for t in result.closed_trades] == [(2, 110, 1)]
    assert result.open_trades[0].qty == 3
    assert not any(order.id in {"X:S", "absent:L"} for order in engine.orders)


def test_changed_entry_id_does_not_keep_previous_waiting_exit():
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", "long")
            ctx.entry("B", "long")
            ctx.exit("X", "A", limit=105)
            ctx.exit("X", "B", limit=105)

    engine, result = run(commands, candles((100, 101, 99, 100), (100, 106, 99, 100)), pyramiding=2)
    assert result.status == "completed", result.errors
    assert [t.entry_id for t in result.closed_trades] == ["B"]
    assert [t.entry_id for t in result.open_trades] == ["A"]


@pytest.mark.parametrize("price_args", [{"limit": 99}, {"stop": 101}, {"limit": 99, "stop": 101}])
def test_price_entry_activation_is_explicitly_not_claimed(price_args):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("L", "long", **price_args)
            ctx.exit("X", "L", limit=105)

    from backtest_engine.errors import StrategyRuntimeError

    with pytest.raises(StrategyRuntimeError, match="continuous fill activation"):
        run(commands, candles((100, 101, 99, 100), (100, 106, 98, 100)))


def test_deferred_instructions_survive_broker_snapshot_without_aliasing():
    from backtest_engine.core.state_snapshot import clone_state
    from backtest_engine.context import StrategyContext
    from backtest_engine.core.deferred_exits import activate_deferred_exits

    rows = candles((100, 101, 99, 100), (100, 106, 99, 100))
    e = BacktestEngine(BacktestConfig("S", "1m", 0, 60_000, mintick=1))
    ctx = StrategyContext(e.config, e.state)
    ctx.entry("L", "long", qty=1)
    ctx.exit("X", "L", profit=5)
    e._flush(ctx, rows[0], 0)
    checkpoint = clone_state(e.orders)
    e.orders[0].pending_exits.clear()
    e.orders = clone_state(checkpoint)
    e.orders[0].status = "active"
    e._fill(e.orders[0], rows[1], 1, 100, "open")
    assert e.orders[-1].limit_price == 105 and e.orders[-1].status == "active"
    assert checkpoint[0].pending_exits and not e.orders[0].pending_exits
    n = len(e.orders)
    activate_deferred_exits(e, e.orders[0], rows[1], 1)
    assert len(e.orders) == n


@pytest.mark.parametrize(
    "direction,entry_price,leg,level",
    [
        ("long", 110, "limit", 105),
        ("long", 90, "stop", 95),
        ("short", 90, "limit", 95),
        ("short", 110, "stop", 105),
    ],
)
@pytest.mark.parametrize("recalc", [False, True])
def test_gap_beyond_waiting_bracket_uses_entry_open_not_next_ohlc_point(
    direction, entry_price, leg, level, recalc
):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("L", direction, qty=1)
            ctx.exit("X", "L", **{leg: level})

    engine, result = run(
        commands,
        candles(
            (100, 101, 99, 100),
            (entry_price, entry_price + 2, entry_price - 1, entry_price),
        ),
        calc_on_order_fills=recalc,
    )
    assert result.status == "completed", result.errors
    assert [(t.entry_price, t.exit_price, t.qty) for t in result.closed_trades] == [
        (entry_price, entry_price, 1)
    ]
    assert [fill.intrabar_point for fill in engine.fills] == ["open", "open"]
