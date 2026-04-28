from backtest_engine import BacktestConfig, BacktestEngine, Bar
from backtest_engine.broker.fill_simulator import build_price_path


BARS_GAP = [Bar(1, 10, 10, 10, 10), Bar(2, 20, 20, 20, 20)]


def cfg(**kw):
    d = dict(symbol="S", timeframe="1D", start_time=1, end_time=2, commission_type="none")
    d.update(kw)
    return BacktestConfig(**d)


class BuyBar0:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)


def test_process_orders_on_close_market_entry_fills_current_close_not_next_open():
    result = BacktestEngine(cfg(process_orders_on_close=True)).run(BuyBar0, bars=BARS_GAP)
    assert result.open_trades[0].entry_price == 10


def test_ohlc_tie_rule_low_first():
    assert [point for _, point in build_price_path(Bar(1, 10, 12, 8, 10))] == [
        "open",
        "low",
        "high",
        "close",
    ]
