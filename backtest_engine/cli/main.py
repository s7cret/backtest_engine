from __future__ import annotations
import argparse, json
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec
from backtest_engine import BacktestConfig, BacktestEngine, BarSeries
from backtest_engine.results import JSONResultWriter

def _load_class(path:str, name:str):
    spec=spec_from_file_location('backtest_strategy', path); mod=module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(mod); return getattr(mod,name)
def _load_bars(path:str):
    rows=json.loads(Path(path).read_text())
    if isinstance(rows, dict) and 'bars' in rows: rows=rows['bars']
    return BarSeries.from_records(rows)
def main(argv=None)->int:
    p=argparse.ArgumentParser(prog='backtest'); sub=p.add_subparsers(dest='cmd')
    run=sub.add_parser('run'); run.add_argument('--strategy',required=True); run.add_argument('--class',dest='cls',required=True); run.add_argument('--bars',required=True); run.add_argument('--symbol',required=True); run.add_argument('--timeframe',required=True); run.add_argument('--start',type=int,default=0); run.add_argument('--end',type=int,default=2**63-1); run.add_argument('--capital',type=float,default=10000); run.add_argument('--params',default=None); run.add_argument('--execution-mode',default='normal'); run.add_argument('--output',required=True); run.add_argument('--no-events',action='store_true'); run.add_argument('--no-equity-curve',action='store_true')
    sub.add_parser('benchmark'); sub.add_parser('compare'); sub.add_parser('export')
    a=p.parse_args(argv)
    if a.cmd=='run':
        params=json.loads(Path(a.params).read_text()) if a.params else {}
        cfg=BacktestConfig(symbol=a.symbol,timeframe=a.timeframe,start_time=a.start,end_time=a.end,initial_capital=a.capital,execution_mode=a.execution_mode,collect_events=not a.no_events,collect_equity_curve=not a.no_equity_curve)
        res=BacktestEngine(cfg).run(_load_class(a.strategy,a.cls), params=params, bars=_load_bars(a.bars)); JSONResultWriter().write(res,a.output); return 0
    print('Command scaffold available; use run for executable backtests.'); return 0
if __name__=='__main__': raise SystemExit(main())
