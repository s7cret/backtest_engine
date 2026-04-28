import pytest

from backtest_engine import BacktestCallbacks, BacktestConfig, BacktestEngine, Bar


def cfg(**kw):
    d = dict(
        symbol="S",
        timeframe="1D",
        start_time=1,
        end_time=4,
        initial_capital=100000,
        commission_type="percent",
        commission_value=1.0,
        slippage=0.5,
        slippage_type="price",
        process_orders_on_close=True,
    )
    d.update(kw)
    return BacktestConfig(**d)


class EntryThenCloseAll:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=2)
        if bar_index == 1:
            self.ctx.close_all()


def test_commission_slippage_ledger_is_net_once_for_close_all():
    fills = []
    result = BacktestEngine(cfg()).run(
        EntryThenCloseAll,
        bars=[Bar(1, 10, 10, 10, 10), Bar(2, 12, 12, 12, 12)],
        callbacks=BacktestCallbacks(on_fill=fills.append),
    )

    assert [(f.order_id, f.price, f.commission) for f in fills] == [
        ("L", 10.5, 0.21),
        ("close_all", 11.5, 0.23),
    ]
    assert result.commission_total == pytest.approx(0.44)
    assert result.net_profit == pytest.approx(1.56)
    assert result.final_equity == pytest.approx(100001.56)
    trade = result.closed_trades[0]
    assert trade.profit == pytest.approx(1.56)
    assert trade.commission_entry == pytest.approx(0.21)
    assert trade.commission_exit == pytest.approx(0.23)


class ReverseLongToShort:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=2)
        if bar_index == 1:
            self.ctx.entry("S", "short", qty=3)


def test_reversal_splits_commission_between_closed_and_new_open_lot():
    result = BacktestEngine(cfg()).run(
        ReverseLongToShort,
        bars=[Bar(1, 10, 10, 10, 10), Bar(2, 12, 12, 12, 12)],
    )

    assert len(result.closed_trades) == 1
    assert len(result.open_trades) == 1
    closed = result.closed_trades[0]
    opened = result.open_trades[0]

    # Reversal order qty is 5 at 11.5 with 1% commission = 0.575.
    # 2/5 belongs to closing the old long; 3/5 is entry commission for the new short.
    assert closed.exit_id == "S"
    assert closed.qty == 2
    assert closed.commission_entry == pytest.approx(0.21)
    assert closed.commission_exit == pytest.approx(0.23)
    assert closed.profit == pytest.approx(1.56)
    assert opened.direction == "short"
    assert opened.qty == 3
    assert opened.entry_price == 11.5
    assert opened.commission_entry == pytest.approx(0.345)
    assert result.commission_total == pytest.approx(0.21 + 0.23 + 0.345)
