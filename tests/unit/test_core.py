import pytest
from backtest_engine import BacktestConfig, BacktestEngine, Bar, BarSeries
from backtest_engine.broker.fill_simulator import build_price_path
from backtest_engine.broker.commission import calculate_commission
from backtest_engine.errors import BarValidationError
from backtest_engine.core.validation import validate_bars

BARS=[Bar(1,10,11,9,10),Bar(2,12,13,11,12),Bar(3,14,15,13,14),Bar(4,13,14,10,11)]
class BuyOnce:
    def __init__(self, params, runtime, ctx): self.ctx=ctx
    def _process_bar(self, bar, bar_index):
        if bar_index==0: self.ctx.entry('L','long',qty=1)
class BuyClose:
    def __init__(self, params, runtime, ctx): self.ctx=ctx
    def _process_bar(self, bar, bar_index):
        if bar_index==0: self.ctx.entry('L','long',qty=1)
        if bar_index==2: self.ctx.close('L',immediately=True)
class LimitStop:
    def __init__(self, params, runtime, ctx): self.ctx=ctx; self.kind=params['kind']
    def _process_bar(self, bar, bar_index):
        if bar_index==0:
            if self.kind=='limit': self.ctx.entry('L','long',qty=1,limit=11)
            if self.kind=='stop': self.ctx.entry('L','long',qty=1,stop=13)
            if self.kind=='stop_limit': self.ctx.entry('L','long',qty=1,stop=13,limit=12)

def cfg(**kw):
    d=dict(symbol='S',timeframe='1D',start_time=1,end_time=4,commission_type='none')
    d.update(kw); return BacktestConfig(**d)

def test_barseries_and_validation():
    s=BarSeries.from_bars(BARS); assert len(s)==4; assert s.get_bar(0).open==10
    with pytest.raises(BarValidationError): validate_bars(BarSeries.from_bars([Bar(1,1,0,1,1)]))

def test_ohlc_path_tie_and_variants():
    assert [p for _,p in build_price_path(Bar(1,10,12,8,10))]==['open','high','low','close']
    assert [p for _,p in build_price_path(Bar(1,10,11,5,10))]==['open','high','low','close']
    assert [p for _,p in build_price_path(Bar(1,10,15,9,10))]==['open','low','high','close']

def test_market_next_open_and_close_immediate():
    r=BacktestEngine(cfg()).run(BuyOnce,bars=BARS)
    assert r.open_trades[0].entry_price==12
    r2=BacktestEngine(cfg(process_orders_on_close=True)).run(BuyClose,bars=BARS)
    assert r2.closed_trades and r2.closed_trades[0].exit_price==14

def test_limit_stop_stoplimit():
    assert BacktestEngine(cfg()).run(LimitStop,{'kind':'limit'},BARS).open_trades[0].entry_price==11
    assert BacktestEngine(cfg()).run(LimitStop,{'kind':'stop'},BARS).open_trades[0].entry_price==13
    # stop-limit activates at 13 then waits for 12 in later path/bar
    assert BacktestEngine(cfg()).run(LimitStop,{'kind':'stop_limit'},BARS).open_trades[0].entry_price==12

def test_commission():
    assert calculate_commission(100,2,'percent',1)==2
    assert calculate_commission(100,2,'fixed_per_order',3)==3
    assert calculate_commission(100,2,'fixed_per_contract',3)==6

def test_pyramiding_reject_and_force_close():
    class Twice:
        def __init__(self, params,runtime,ctx): self.ctx=ctx
        def _process_bar(self,bar,bar_index):
            if bar_index in (0,1): self.ctx.entry('L'+str(bar_index),'long',qty=1)
    r=BacktestEngine(cfg(force_close_on_end=True)).run(Twice,bars=BARS)
    assert any(d.code=='ORDER_REJECTED_PYRAMIDING' for d in r.warnings)
    assert r.closed_trades

def test_early_stop_and_preloaded():
    c=cfg(preloaded_bars=BARS,early_stop_enabled=True,min_equity_stop=9999)
    r=BacktestEngine(c).run(BuyOnce)
    assert r.status=='early_stopped'
