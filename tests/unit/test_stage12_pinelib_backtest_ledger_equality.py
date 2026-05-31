import pytest

from backtest_engine import BacktestConfig, BacktestEngine, Bar

pytestmark = pytest.mark.skip(
    reason="legacy PineLib backtest ledger equality tests; BacktestEngine is now the sole fill/trade ledger authority"
)

from pinelib import Bar as PineBar  # noqa: E402
from pinelib import PineRuntime, RuntimeConfig, StrategyContext, SymbolInfo, TimeframeInfo  # noqa: E402


def be_cfg(**kw):
    d = dict(symbol="S", timeframe="1D", start_time=1, end_time=10, initial_capital=100000.0, commission_type="percent", commission_value=0.0)
    d.update(kw)
    return BacktestConfig(**d)


def pine_rt(strategy: StrategyContext) -> PineRuntime:
    rt = PineRuntime(SymbolInfo("S", mintick=0.01), TimeframeInfo.from_string("D"), config=RuntimeConfig())
    strategy.attach_runtime(rt)
    return rt


def pine_bar(b: Bar) -> PineBar:
    return PineBar(b.time, b.open, b.high, b.low, b.close, volume=0.0, time_close=b.time_close)


def process_pine(rt: PineRuntime, strategy: StrategyContext, b: Bar) -> None:
    pb = pine_bar(b)
    rt.begin_bar(pb)
    strategy.process_orders_for_bar(runtime=rt, bar=pb)
    rt.end_bar()


BARS = [
    Bar(1, 10, 10, 10, 10, time_close=2),
    Bar(2, 12, 12, 12, 12, time_close=3),
    Bar(3, 15, 15, 15, 15, time_close=4),
    Bar(4, 15, 15, 15, 15, time_close=5),
]


class MarketEntryCloseAll:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=2)
        if bar_index == 2:
            self.ctx.close_all()


def run_pine_market_entry_close_all(process_orders_on_close=False, commission_type="percent", commission_value=0, slippage=0.0):
    s = StrategyContext(
        process_orders_on_close=process_orders_on_close,
        commission_type=commission_type,
        commission_value=commission_value,
        slippage=slippage,
    )
    rt = pine_rt(s)
    for i, b in enumerate(BARS):
        pb = pine_bar(b)
        rt.begin_bar(pb)
        if i == 0:
            s.entry("L", "long", qty=2)
        if i == 2:
            s.close_all()
        s.process_orders_for_bar(runtime=rt, bar=pb)
        rt.end_bar()
    return s


def test_pinelib_backtest_market_entry_close_all_ledger_equality():
    be = BacktestEngine(be_cfg()).run(MarketEntryCloseAll, bars=BARS)
    pl = run_pine_market_entry_close_all()

    assert be.closed_trades[0].qty == 2
    assert be.closed_trades[0].entry_price == pl.fills[0].price == 12
    assert be.closed_trades[0].exit_price == pl.fills[-1].price == 15
    assert be.net_profit == pl.netprofit == 6
    assert be.final_equity == pl.equity == 100006


def test_pinelib_backtest_process_orders_on_close_ledger_equality():
    be = BacktestEngine(be_cfg(process_orders_on_close=True)).run(MarketEntryCloseAll, bars=BARS)
    pl = run_pine_market_entry_close_all(process_orders_on_close=True)

    assert be.closed_trades[0].entry_price == pl.fills[0].price == 10
    assert be.closed_trades[0].exit_price == pl.fills[-1].price == 15
    assert be.net_profit == pl.netprofit == 10
    assert be.final_equity == pl.equity == 100010


def test_pinelib_backtest_commission_slippage_supported_subset_equality():
    cfg = be_cfg(commission_type="percent", commission_value=1, slippage=0.5, slippage_type="price")
    be = BacktestEngine(cfg).run(MarketEntryCloseAll, bars=BARS)
    pl = run_pine_market_entry_close_all(commission_type="percent", commission_value=1, slippage=0.5)

    assert be.closed_trades[0].entry_price == pl.fills[0].price == 12.5
    assert be.closed_trades[0].exit_price == pl.fills[-1].price == 14.5
    assert round(be.net_profit, 10) == round(pl.netprofit, 10)
    assert round(be.final_equity, 10) == round(pl.equity, 10)
