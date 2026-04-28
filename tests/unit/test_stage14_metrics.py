from backtest_engine import BacktestConfig, BacktestEngine, Bar


class TwoTrades:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("W", "long")
        if bar_index == 1:
            self.ctx.close("W")
        if bar_index == 2:
            self.ctx.entry("L", "long")
        if bar_index == 3:
            self.ctx.close("L")


def test_stage14_p1_trade_metrics_and_commission_total():
    bars = [
        Bar(1, 100, 100, 100, 100),
        Bar(2, 100, 102, 99, 102),
        Bar(3, 102, 103, 101, 103),
        Bar(4, 103, 103, 99, 99),
        Bar(5, 99, 100, 98, 98),
    ]
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1D",
        start_time=1,
        end_time=5,
        initial_capital=10000,
        commission_type="percent",
        commission_value=1.0,
        process_orders_on_close=True,
    )

    result = BacktestEngine(cfg).run(TwoTrades, bars=bars)

    assert result.total_trades == 2
    assert result.avg_trade == (0.98 - 4.99) / 2
    assert result.largest_win == 0.98
    assert result.largest_loss == 4.99
    assert result.avg_bars_in_trade == 0.0
    assert result.commission_total == 1.0 + 1.02 + 1.03 + 0.99
