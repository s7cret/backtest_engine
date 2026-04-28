from __future__ import annotations
import time
from dataclasses import replace
from typing import Any
from backtest_engine.config import BacktestConfig
from backtest_engine.context import StrategyContext, StrategyStateView
from backtest_engine.errors import BarMagnifierUnavailableError, ConfigError, ProviderError, ResumeUnsupportedError, StrategyRuntimeError
from backtest_engine.models import Bar, BarSeries, Order, Fill, Position, Trade, EquityPoint, Diagnostic, BacktestCallbacks, BacktestResumeState, InstrumentModel
from backtest_engine.broker.commission import calculate_commission
from backtest_engine.broker.slippage import slippage_value
from backtest_engine.broker.rounding import round_to_step
from backtest_engine.broker.fill_simulator import build_price_path, limit_reached, stop_reached
from backtest_engine.core.validation import validate_bars, data_fingerprint
from backtest_engine.results import BacktestResult
from backtest_engine.results.statistics import summarize

class _NoopRuntime:
    def begin_bar(self, bar:Bar, bar_index:int)->None: pass
    def end_bar(self)->None: pass

class BacktestEngine:
    def __init__(self, config: BacktestConfig):
        self.config=config
        self.instrument=config.instrument_model or InstrumentModel()
        self.callbacks=BacktestCallbacks()
        self._callbacks_disabled=False
        self._reset_state()

    def _reset_state(self)->None:
        self.position=Position(); self.cash=self.config.initial_capital; self.equity=self.config.initial_capital; self.peak_equity=self.config.initial_capital
        self.max_drawdown=0.0; self.max_drawdown_percent=0.0
        self.orders:list[Order]=[]; self.fills:list[Fill]=[]; self.closed_trades:list[Trade]=[]; self.open_trades:list[Trade]=[]
        self.events:list[Diagnostic]=[]; self.warnings:list[Diagnostic]=[]; self.errors:list[Diagnostic]=[]
        self.state=StrategyStateView(initial_capital=self.config.initial_capital,cash=self.config.initial_capital,equity=self.config.initial_capital,_open_trades_ref=self.open_trades,_closed_trades_ref=self.closed_trades)
        self.last_trade_bar:int|None=None

    def run(self, strategy_class:type, params:dict|None=None, bars:BarSeries|list[Bar]|None=None, callbacks:BacktestCallbacks|None=None, resume_state:BacktestResumeState|None=None)->BacktestResult:
        t0=time.perf_counter(); params=params or {}; self.callbacks=callbacks or BacktestCallbacks(); self._callbacks_disabled=False; self._reset_state(); self._validate_config()
        if resume_state is not None: raise ResumeUnsupportedError('resume requires external strategy/runtime state serializers')
        series=self._resolve_bars(bars)
        series=self._slice_range(series)
        if self.config.validate_bars: series,_=validate_bars(series,self.config.duplicate_bar_policy)
        if self.config.use_bar_magnifier and (not self.config.bar_magnifier_lower_tf or not self.config.data_provider or not hasattr(self.config.data_provider,'get_lower_tf_bars')):
            if self.config.bar_magnifier_missing_policy=='error': raise BarMagnifierUnavailableError('bar magnifier lower timeframe/provider unavailable')
            self._diag('BAR_MAGNIFIER_FALLBACK','bar magnifier unavailable; using OHLC path','warning')
        if self.config.calc_on_every_tick and not self.config.experimental_intrabar_strategy_mode:
            self._diag('CALC_ON_EVERY_TICK_HISTORICAL_LIMITED','historical mode calculates strategy on parent bars only','warning')
        ctx=StrategyContext(self.config,self.state); runtime=self.config.runtime or _NoopRuntime()
        try: strategy=strategy_class(params=params,runtime=runtime,ctx=ctx)
        except TypeError: strategy=strategy_class(params,runtime); strategy.ctx=ctx
        equity_curve=[] if self._want('equity_curve') or self.config.collect_equity_curve else None
        status='completed'; early_reason=None
        for i in range(len(series)):
            bar=series.get_bar(i)
            self._cb('on_bar_start',bar,i)
            for o in self.orders:
                if o.status=='pending' and o.active_from_bar_index<=i:
                    o.status='active'; self._event('ORDER_ACTIVATED',f'order {o.id} activated',i,bar.time,o.id); self._cb('on_order_activated',o)
            runtime.begin_bar(bar,i)
            self._call_strategy(strategy,bar,i); self._flush(ctx,bar,i)
            self._process_bar_fills(strategy,ctx,bar,i)
            self._update_open_profit(bar.close); self._update_trade_excursions(bar); self._update_state()
            if self.equity>self.peak_equity: self.peak_equity=self.equity
            dd=max(0.0,self.peak_equity-self.equity); ddp=dd/self.peak_equity*100 if self.peak_equity else 0.0
            self.max_drawdown=max(self.max_drawdown,dd); self.max_drawdown_percent=max(self.max_drawdown_percent,ddp); self._update_state()
            if equity_curve is not None:
                point=EquityPoint(i,bar.time,self.equity,self.cash,self.position.size,self.position.avg_price if self.position.direction!='flat' else None,self.position.open_profit,self.position.realized_profit,dd,ddp); equity_curve.append(point); self._cb('on_equity',point)
            if self.config.early_stop_enabled:
                if self.config.min_equity_stop is not None and self.equity <= self.config.min_equity_stop: status='early_stopped'; early_reason='min_equity_stop'; break
                if self.config.max_drawdown_stop_percent is not None and ddp >= self.config.max_drawdown_stop_percent: status='early_stopped'; early_reason='max_drawdown_stop_percent'; break
                if self.config.max_bars_without_trade is not None and self.last_trade_bar is not None and i-self.last_trade_bar>=self.config.max_bars_without_trade: status='early_stopped'; early_reason='max_bars_without_trade'; break
            runtime.end_bar(); self._cb('on_bar_end',bar,i,self.state)
        if self.config.force_close_on_end and self.position.direction!='flat' and len(series): self._force_close(series.get_bar(len(series)-1), len(series)-1)
        result=self._result(series,equity_curve,status,early_reason,(time.perf_counter()-t0)*1000)
        return result

    def _validate_config(self)->None:
        if self.config.margin_long != 100.0 or self.config.margin_short != 100.0:
            if self.config.unsupported_margin_policy=='error': raise ConfigError('margin/liquidation model is unsupported')
            if self.config.unsupported_margin_policy=='warn': self._diag('MARGIN_UNSUPPORTED','margin settings are recorded but liquidation is unsupported','warning')
        if self.config.tradingview_compare_mode=='streaming' and self.config.execution_mode!='debug':
            raise ConfigError('streaming TradingView compare requires execution_mode=debug')
        if 'equity_curve' in self.config.required_outputs and not self.config.collect_equity_curve:
            self.config.collect_equity_curve=True
        if 'order_lifecycle' in self.config.required_outputs or 'order_events' in self.config.required_outputs:
            self.config.collect_events=True
        if 'mfe_mae' in self.config.required_outputs:
            self.config.collect_mfe_mae=True; self.config.collect_trade_details=True

    def _slice_range(self,series:BarSeries)->BarSeries:
        start=self.config.start_time; end=self.config.end_time
        idx=[i for i,t in enumerate(series.time) if int(t)>=start and int(t)<=end]
        if not idx: return BarSeries([],[],[],[],[],[])
        first=max(0,idx[0]-max(0,self.config.max_bars_back)); last=idx[-1]+1
        return BarSeries(series.time[first:last],series.open[first:last],series.high[first:last],series.low[first:last],series.close[first:last],None if series.volume is None else series.volume[first:last])

    def _resolve_bars(self,bars:BarSeries|list[Bar]|None)->BarSeries:
        src=bars if bars is not None else self.config.preloaded_bars
        if src is None and self.config.data_provider:
            try: src=self.config.data_provider.get_bars(self.config.symbol,self.config.timeframe,self.config.start_time,self.config.end_time)
            except Exception as e: raise ProviderError(str(e)) from e
        if src is None: raise ProviderError('No bars or data_provider supplied')
        if isinstance(src,BarSeries): return src
        return BarSeries.from_bars(src)

    def _call_strategy(self,strategy:Any,bar:Bar,i:int)->None:
        try: strategy._process_bar(bar,i)
        except Exception as e: raise StrategyRuntimeError(str(e)) from e

    def _flush(self,ctx:StrategyContext,bar:Bar,i:int)->None:
        for c in ctx.buffer.drain():
            k=c.name; kw=c.kwargs
            if k=='cancel_all':
                for o in self.orders:
                    if o.status in ('pending','active'): o.status='cancelled'; self._cb('on_order_cancelled',o)
                continue
            if k=='cancel':
                for o in self.orders:
                    if o.id==kw['id'] and o.status in ('pending','active'): o.status='cancelled'; self._cb('on_order_cancelled',o)
                continue
            if k in ('close','close_all'):
                if self.position.direction=='flat': continue
                qty=self._qty_from_args(kw,self.position.size,bar.close)
                if k=='close_all': qty=abs(self.position.size)
                self._add_order(Order(id=kw.get('id','close_all'), kind='close', direction=self.position.direction, side='sell' if self.position.direction=='long' else 'buy', position_effect='close', order_type='market', qty=qty, created_bar_index=i, created_time=bar.time, active_from_bar_index=i if (kw.get('immediately') or self.config.process_orders_on_close) else i+1, position_direction=self.position.direction, reduce_only=True, comment=kw.get('comment'), immediately=kw.get('immediately',False)),bar,i); continue
            limit=kw.get('limit'); stop=kw.get('stop')
            if limit!=limit: limit=None
            if stop!=stop: stop=None
            typ='market' if limit is None and stop is None else 'limit' if stop is None else 'stop' if limit is None else 'stop_limit'
            if k=='exit':
                if self.position.direction=='flat': self._diag('ORDER_REJECTED_NO_AVAILABLE_POSITION_QTY','exit without position','warning',i,bar.time,kw['id']); continue
                direction=self.position.direction; side='sell' if direction=='long' else 'buy'; qty=self._qty_from_args(kw,self.position.size,bar.close)
                if limit is None and stop is None: self._diag('ORDER_REJECTED_EMPTY_EXIT','exit has no active legs','warning',i,bar.time,kw['id']); continue
                if limit is not None: self._add_order(Order(id=kw['id']+':L', kind='exit', direction=direction, side=side, position_effect='reduce', order_type='limit', qty=qty, created_bar_index=i, created_time=bar.time, active_from_bar_index=i+1, position_direction=direction, reduce_only=True, limit_price=limit, from_entry=kw.get('from_entry'), oca_name=kw.get('oca_name') or kw['id'], oca_type='reduce', reserved_qty=qty, parent_exit_id=kw['id'], comment=kw.get('comment')),bar,i)
                if stop is not None: self._add_order(Order(id=kw['id']+':S', kind='exit', direction=direction, side=side, position_effect='reduce', order_type='stop', qty=qty, created_bar_index=i, created_time=bar.time, active_from_bar_index=i+1, position_direction=direction, reduce_only=True, stop_price=stop, from_entry=kw.get('from_entry'), oca_name=kw.get('oca_name') or kw['id'], oca_type='reduce', reserved_qty=qty, parent_exit_id=kw['id'], comment=kw.get('comment')),bar,i)
                continue
            direction=kw['direction']; side='buy' if direction=='long' else 'sell'; qty=self._qty_from_args(kw,None,bar.close)
            effect='open'
            if k=='entry' and self.position.direction!='flat' and self.position.direction!=direction and self.config.reverse_on_opposite_entry: effect='reverse'; qty=abs(self.position.size)+qty
            if k=='entry' and not self._entry_allowed(direction): self._diag('ORDER_REJECTED_PYRAMIDING','pyramiding limit reached','warning',i,bar.time,kw['id']); continue
            existing=next((o for o in self.orders if o.id==kw['id'] and o.kind==k and o.status in ('pending','active')),None)
            new=Order(kw['id'],k,direction,side,effect,typ,qty,i,bar.time,i+1,direction,False,limit,stop,None,kw.get('oca_name'),kw.get('oca_type') or 'none',comment=kw.get('comment'))
            if existing:
                existing.qty=new.qty; existing.limit_price=new.limit_price; existing.stop_price=new.stop_price; existing.order_type=new.order_type; self._event('ORDER_MODIFIED',f'order {existing.id} modified',i,bar.time,existing.id)
            else: self._add_order(new,bar,i)

    def _qty_from_args(self,kw:dict,current_size:float|None,price:float)->float:
        if kw.get('qty') is not None: q=float(kw['qty'])
        elif kw.get('qty_percent') is not None and current_size is not None: q=abs(current_size)*float(kw['qty_percent'])/100.0
        elif self.config.default_qty_type=='fixed': q=self.config.default_qty_value
        elif self.config.default_qty_type=='cash': q=self.config.default_qty_value/price
        else: q=(self.equity*self.config.default_qty_value/100.0)/price
        q=round_to_step(q,self.config.qty_step,self.config.qty_rounding)
        if self.config.min_qty and q<self.config.min_qty: q=0.0
        return abs(q)

    def _entry_allowed(self,direction:str)->bool:
        if direction=='long' and not self.config.allow_long: return False
        if direction=='short' and not self.config.allow_short: return False
        if self.position.direction==direction and self.config.pyramiding<=0: return False
        existing_orders=sum(1 for o in self.orders if o.kind=='entry' and o.direction==direction and o.status in ('pending','active'))
        if existing_orders and self.config.pyramiding<=0: return False
        active=sum(1 for t in self.open_trades if t.direction==direction)
        return active + existing_orders <= self.config.pyramiding

    def _add_order(self,o:Order,bar:Bar,i:int)->None:
        if o.qty<=0: self._diag('ORDER_REJECTED_ZERO_QTY','order qty is zero','warning',i,bar.time,o.id); return
        if o.active_from_bar_index <= i:
            o.status='active'
        self.orders.append(o); self._event('ORDER_CREATED',f'order {o.id} created',i,bar.time,o.id); self._cb('on_order_created',o)

    def _process_bar_fills(self,strategy:Any,ctx:StrategyContext,bar:Bar,i:int)->None:
        recalc=0
        while True:
            filled=False
            path=self._price_path(bar)
            for price,point in path:
                for o in list(self.orders):
                    if o.status!='active': continue
                    is_open_point = point == 'open' or point.endswith('.open')
                    is_close_point = point == 'close' or point.endswith('.close')
                    if o.order_type=='market' and ((is_open_point and o.created_bar_index < i) or (is_close_point and (self.config.process_orders_on_close or o.immediately))): pass
                    elif o.order_type=='limit' and limit_reached(o,price,bar,self.config.mintick,self.config.backtest_fill_limits_assumption_ticks): price=o.limit_price or price
                    elif o.order_type=='stop' and stop_reached(o,price):
                        if self.config.stop_gap_fill_policy=='stop_price': price=o.stop_price or price
                        elif not is_open_point and not self.config.fill_worse_stop_at_path_price: price=o.stop_price or price
                    elif o.order_type=='stop_limit':
                        if not o.stop_limit_activated and stop_reached(o,price): o.stop_limit_activated=True; self._event('STOP_LIMIT_ACTIVATED',f'stop-limit {o.id} activated',i,bar.time,o.id)
                        if not (o.stop_limit_activated and limit_reached(o,price,bar,self.config.mintick,self.config.backtest_fill_limits_assumption_ticks)): continue
                        price=o.limit_price or price
                    else: continue
                    self._fill(o,bar,i,price,point); filled=True
                    if self.config.calc_on_order_fills:
                        recalc+=1
                        if recalc>self.config.max_recalc_depth: self._diag('MAX_RECALC_DEPTH_REACHED','max recalc depth reached','warning',i,bar.time); return
                        self._call_strategy(strategy,bar,i); self._flush(ctx,bar,i)
            if not (self.config.calc_on_order_fills and filled): break

    def _price_path(self,bar:Bar)->list[tuple[float,str]]:
        if self.config.fill_model=='close_only': return [(bar.close,'close')]
        if not self.config.use_bar_magnifier: return build_price_path(bar)
        provider=self.config.data_provider
        if not provider or not self.config.bar_magnifier_lower_tf or not hasattr(provider,'get_lower_tf_bars'):
            return build_price_path(bar)
        try:
            lower=provider.get_lower_tf_bars(self.config.symbol,self.config.timeframe,self.config.bar_magnifier_lower_tf,bar)
            lower_series=lower if isinstance(lower,BarSeries) else BarSeries.from_bars(lower)
        except Exception as e:
            if self.config.bar_magnifier_missing_policy=='error': raise BarMagnifierUnavailableError(str(e)) from e
            self._diag('BAR_MAGNIFIER_FALLBACK','lower timeframe bars unavailable; using OHLC path','warning')
            return build_price_path(bar)
        if len(lower_series)==0:
            if self.config.bar_magnifier_missing_policy=='error': raise BarMagnifierUnavailableError('empty lower timeframe bars')
            self._diag('BAR_MAGNIFIER_FALLBACK','empty lower timeframe bars; using OHLC path','warning')
            return build_price_path(bar)
        path:list[tuple[float,str]]=[]
        for j in range(len(lower_series)):
            lb=lower_series.get_bar(j)
            for price,point in build_price_path(lb):
                path.append((price,f'lower[{j}].{point}'))
        return path

    def _fill(self,o:Order,bar:Bar,i:int,price:float,point:str)->None:
        slip=slippage_value(price,o.side,o.position_effect,self.config.slippage,self.config.slippage_type,self.config.mintick); fprice=round_to_step(price+slip,self.config.mintick,self.config.price_rounding)
        before=self.position.direction; com=calculate_commission(fprice,o.qty,self.config.commission_type,self.config.commission_value)
        self.cash-=com; self.position.realized_profit-=com
        after=self._apply_position(o,fprice,bar,i,com)
        fill=Fill(o.id,i,bar.time,fprice,o.qty,o.direction,o.side,o.position_effect,before,after,'filled',com,slip,point)
        self.fills.append(fill); o.status='filled'; self.last_trade_bar=i; self._cb('on_fill',fill); self._event('ORDER_FILLED',f'order {o.id} filled',i,bar.time,o.id)
        self._apply_oca(o)

    def _apply_position(self,o:Order,price:float,bar:Bar,i:int,commission:float)->str:
        dir=o.direction; signed=o.qty if o.side=='buy' else -o.qty
        if self.position.direction=='flat' or (self.position.size==0):
            self.position.size=signed; self.position.direction='long' if signed>0 else 'short'; self.position.avg_price=price
            tr=Trade(o.id,o.id,None,self.position.direction,bar.time,i,price,None,None,None,abs(signed),commission,0.0,-commission,0.0,is_open=True); self.open_trades.append(tr); self._cb('on_trade_open',tr); return self.position.direction
        cur_sign=1 if self.position.direction=='long' else -1
        if signed*cur_sign>0:
            newabs=abs(self.position.size)+abs(signed); self.position.avg_price=(self.position.avg_price*abs(self.position.size)+price*abs(signed))/newabs; self.position.size+=signed
            tr=Trade(o.id,o.id,None,self.position.direction,bar.time,i,price,None,None,None,abs(signed),commission,0.0,-commission,0.0,is_open=True); self.open_trades.append(tr); self._cb('on_trade_open',tr); return self.position.direction
        qty_close=min(abs(signed),abs(self.position.size)); profit=self.instrument.pnl(self.position.avg_price,price,qty_close,self.position.direction)-commission; self.cash+=profit; self.position.realized_profit+=profit
        remaining=qty_close
        for tr in list(self.open_trades):
            if remaining<=0: break
            q=min(tr.qty,remaining); p=self.instrument.pnl(tr.entry_price,price,q,tr.direction)-commission*(q/qty_close); closed=replace(tr,exit_id=o.id,exit_time=bar.time,exit_bar_index=i,exit_price=price,qty=q,commission_exit=commission*(q/qty_close),profit=p,profit_percent=(p/(tr.entry_price*q)*100 if tr.entry_price*q else 0.0),exit_reason=o.id,bars_held=i-tr.entry_bar_index,is_open=False)
            self.closed_trades.append(closed); self._cb('on_trade_close',closed); tr.qty-=q; remaining-=q
            if tr.qty<=1e-12: self.open_trades.remove(tr)
        self.position.size+=signed
        if abs(self.position.size)<1e-12: self.position=Position(realized_profit=self.position.realized_profit); return 'flat'
        if self.position.size*cur_sign<0:
            self.position.direction='long' if self.position.size>0 else 'short'; self.position.avg_price=price; tr=Trade(o.id,o.id,None,self.position.direction,bar.time,i,price,None,None,None,abs(self.position.size),0.0,0.0,0.0,0.0,is_open=True); self.open_trades.append(tr); self._cb('on_trade_open',tr)
        return self.position.direction

    def _update_trade_excursions(self,bar:Bar)->None:
        if not self.config.collect_mfe_mae: return
        for tr in self.open_trades:
            if tr.direction=='long':
                fav=self.instrument.pnl(tr.entry_price,bar.high,tr.qty,tr.direction); adv=self.instrument.pnl(tr.entry_price,bar.low,tr.qty,tr.direction)
            else:
                fav=self.instrument.pnl(tr.entry_price,bar.low,tr.qty,tr.direction); adv=self.instrument.pnl(tr.entry_price,bar.high,tr.qty,tr.direction)
            tr.mfe=fav if tr.mfe is None else max(tr.mfe,fav)
            tr.mae=adv if tr.mae is None else min(tr.mae,adv)
            tr.profit=self.instrument.pnl(tr.entry_price,bar.close,tr.qty,tr.direction)-tr.commission_entry
            self._cb('on_trade_update',tr)

    def _apply_oca(self,o:Order)->None:
        if not o.oca_name: return
        for other in self.orders:
            if other is not o and other.status in ('pending','active') and other.oca_name==o.oca_name:
                if o.oca_type=='cancel': other.status='cancelled'
                elif o.oca_type=='reduce': other.qty=max(0.0,other.qty-o.qty); other.status='cancelled' if other.qty<=0 else other.status

    def _force_close(self,bar:Bar,i:int)->None:
        o=Order('forced_end_close','close',self.position.direction,'sell' if self.position.direction=='long' else 'buy','close','market',abs(self.position.size),i,bar.time,i,self.position.direction,True,immediately=True)
        self._fill(o,bar,i,bar.close,'close')

    def _update_open_profit(self,price:float)->None:
        self.position.open_profit=0.0 if self.position.direction=='flat' else self.instrument.pnl(self.position.avg_price,price,abs(self.position.size),self.position.direction)
        self.equity=self.cash+self.position.open_profit
    def _update_state(self)->None:
        self.state.position_size=self.position.size; self.state.position_avg_price=None if self.position.direction=='flat' else self.position.avg_price; self.state.position_direction=self.position.direction; self.state.cash=self.cash; self.state.equity=self.equity; self.state.open_profit=self.position.open_profit; self.state.net_profit=self.equity-self.config.initial_capital; self.state.gross_profit=sum(t.profit for t in self.closed_trades if t.profit>0); self.state.gross_loss=sum(t.profit for t in self.closed_trades if t.profit<0); self.state.max_drawdown=self.max_drawdown; self.state.max_drawdown_percent=self.max_drawdown_percent; self.state.closed_trades=len(self.closed_trades); self.state.open_trades=len(self.open_trades)
    def _want(self,name:str)->bool: return name in self.config.required_outputs
    def _event(self,code,msg,i=None,t=None,oid=None)->None:
        if self.config.collect_events: self.events.append(Diagnostic(code,msg,'info',i,t,oid))
    def _diag(self,code,msg,severity,i=None,t=None,oid=None)->None:
        d=Diagnostic(code,msg,severity,i,t,oid); (self.errors if severity=='error' else self.warnings).append(d); self._cb('on_diagnostic',d)
    def _cb(self,name:str,*args:Any)->None:
        fn=getattr(self.callbacks,name,None)
        if fn and not self._callbacks_disabled:
            try: fn(*args)
            except Exception as e:
                if self.config.callback_error_policy=='raise': raise
                self.warnings.append(Diagnostic('CALLBACK_ERROR',str(e),'warning'))
                if self.config.callback_error_policy=='disable_callbacks': self._callbacks_disabled=True
    def _result(self,series:BarSeries,equity_curve:list[EquityPoint]|None,status:str,reason:str|None,ms:float)->BacktestResult:
        profits=[t.profit for t in self.closed_trades]; stats=summarize(profits,self.config.initial_capital,self.equity)
        r=BacktestResult(trades=(self.closed_trades+self.open_trades if self.config.collect_trade_details else None),closed_trades=(self.closed_trades if self._want('closed_trades') or self.config.collect_trade_details else None),open_trades=(self.open_trades if self._want('open_trades') or self.config.collect_trade_details else None),equity_curve=equity_curve,available_outputs=set(),initial_capital=self.config.initial_capital,final_equity=self.equity,bars_processed=len(series),execution_time_ms=ms,status=status,early_stop_reason=reason,config_snapshot=self.config.snapshot(),warnings=self.warnings,errors=self.errors,events=(self.events if self.config.collect_events or self._want('order_events') else None),data_fingerprint=self.config.data_fingerprint or data_fingerprint(series),strategy_fingerprint=self.config.strategy_fingerprint,runtime_fingerprint=self.config.runtime_fingerprint)
        for k,v in stats.items(): setattr(r,k,v)
        r.max_drawdown=max((p.drawdown for p in equity_curve), default=self.max_drawdown) if equity_curve else self.max_drawdown; r.max_drawdown_percent=max((p.drawdown_percent for p in equity_curve), default=self.max_drawdown_percent) if equity_curve else self.max_drawdown_percent
        if r.closed_trades is not None: r.available_outputs.add('closed_trades')
        if r.open_trades is not None: r.available_outputs.add('open_trades')
        if r.equity_curve is not None: r.available_outputs.add('equity_curve')
        if r.events is not None: r.available_outputs.add('order_events')
        r.available_outputs.add('summary_metrics')
        if self.config.content_hash_enabled: r.content_hash_value=r.content_hash(self.config.content_hash_include_equity_curve,self.config.content_hash_include_events)
        return r
