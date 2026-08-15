from backtest_engine import BacktestConfig, BacktestEngine, Bar, BarSeries


BARS = [
    Bar(1, 10, 11, 9, 10),
    Bar(2, 12, 13, 11, 12),
    Bar(3, 14, 15, 13, 14),
    Bar(4, 13, 14, 10, 11),
]


class BuyOnFirstBar:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)


def _cfg(**kw):
    d = dict(
        symbol="S",
        timeframe="1D",
        start_time=1,
        end_time=4,
        commission_type="none",
        score_start_time=3,
        score_end_time=4,
    )
    d.update(kw)
    return BacktestConfig(**d)


def test_calc_only_does_not_fill_during_prehistory() -> None:
    result = BacktestEngine(_cfg(warmup_mode="CALC_ONLY")).run(
        BuyOnFirstBar, bars=BarSeries.from_bars(BARS), effective_pre_bars=2
    )
    assert not result.closed_trades
    assert not result.open_trades


def test_trade_through_can_fill_in_prehistory() -> None:
    result = BacktestEngine(_cfg(warmup_mode="TRADE_THROUGH_UNSCORED")).run(
        BuyOnFirstBar, bars=BarSeries.from_bars(BARS), effective_pre_bars=2
    )
    assert result.open_trades or result.closed_trades
