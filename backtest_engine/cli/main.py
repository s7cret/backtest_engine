from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec
from backtest_engine import BacktestConfig, BacktestEngine, BarSeries
from backtest_engine.results import JSONResultWriter, CSVTradeWriter, compare_trades, load_trades_csv
from backtest_engine.reporting.compare_report import render as render_compare
from backtest_engine.reporting.benchmark_report import render as render_benchmark
from backtest_engine.performance.benchmark import run_benchmark
from backtest_engine.batch import BatchBacktestRunner, BacktestJob


def _load_class(path:str, name:str):
    strategy_path=Path(path).resolve()
    module_name=strategy_path.stem
    parent=str(strategy_path.parent)
    if parent not in sys.path: sys.path.insert(0,parent)
    spec=spec_from_file_location(module_name, strategy_path); mod=module_from_spec(spec); assert spec and spec.loader
    sys.modules[module_name]=mod
    spec.loader.exec_module(mod); return getattr(mod,name)

def _load_bars(path:str):
    rows=json.loads(Path(path).read_text())
    if isinstance(rows, dict) and 'bars' in rows: rows=rows['bars']
    return BarSeries.from_records(rows)

def _load_result(path:str):
    return json.loads(Path(path).read_text())

def main(argv=None)->int:
    p=argparse.ArgumentParser(prog='backtest'); sub=p.add_subparsers(dest='cmd')
    run=sub.add_parser('run'); run.add_argument('--strategy',required=True); run.add_argument('--class',dest='cls',required=True); run.add_argument('--bars',required=True); run.add_argument('--symbol',required=True); run.add_argument('--timeframe',required=True); run.add_argument('--start',type=int,default=0); run.add_argument('--end',type=int,default=2**63-1); run.add_argument('--capital',type=float,default=10000); run.add_argument('--params',default=None); run.add_argument('--execution-mode',default='normal'); run.add_argument('--output',required=True); run.add_argument('--no-events',action='store_true'); run.add_argument('--no-equity-curve',action='store_true')
    cmp=sub.add_parser('compare'); cmp.add_argument('--our',required=True); cmp.add_argument('--tv',required=True); cmp.add_argument('--output',required=True); cmp.add_argument('--price-tolerance',type=float,default=0.0); cmp.add_argument('--qty-tolerance',type=float,default=0.0); cmp.add_argument('--format',choices=['json','text'],default='json')
    exp=sub.add_parser('export'); exp.add_argument('--input',required=True); exp.add_argument('--trades-csv'); exp.add_argument('--summary-md')
    bench=sub.add_parser('benchmark'); bench.add_argument('--strategy',required=True); bench.add_argument('--class',dest='cls',required=True); bench.add_argument('--bars',required=True); bench.add_argument('--symbol',required=True); bench.add_argument('--timeframe',required=True); bench.add_argument('--start',type=int,default=0); bench.add_argument('--end',type=int,default=2**63-1); bench.add_argument('--capital',type=float,default=10000); bench.add_argument('--params'); bench.add_argument('--runs',type=int,default=3); bench.add_argument('--output',required=True); bench.add_argument('--format',choices=['json','text'],default='json')
    bat=sub.add_parser('batch'); bat.add_argument('--strategy',required=True); bat.add_argument('--class',dest='cls',required=True); bat.add_argument('--bars',required=True); bat.add_argument('--jobs',required=True,help='JSON list of {job_id, params?, config_overrides?}'); bat.add_argument('--symbol',required=True); bat.add_argument('--timeframe',required=True); bat.add_argument('--start',type=int,default=0); bat.add_argument('--end',type=int,default=2**63-1); bat.add_argument('--capital',type=float,default=10000); bat.add_argument('--backend',choices=['sequential','thread','process'],default='sequential'); bat.add_argument('--max-workers',type=int); bat.add_argument('--output',required=True)
    a=p.parse_args(argv)
    if a.cmd=='run':
        params=json.loads(Path(a.params).read_text()) if a.params else {}
        cfg=BacktestConfig(symbol=a.symbol,timeframe=a.timeframe,start_time=a.start,end_time=a.end,initial_capital=a.capital,execution_mode=a.execution_mode,collect_events=not a.no_events,collect_equity_curve=not a.no_equity_curve)
        res=BacktestEngine(cfg).run(_load_class(a.strategy,a.cls), params=params, bars=_load_bars(a.bars)); JSONResultWriter().write(res,a.output); return 0
    if a.cmd=='compare':
        our=_load_result(a.our).get('closed_trades') or []
        tv=load_trades_csv(a.tv)
        report=compare_trades(our,tv,price_tolerance=a.price_tolerance,qty_tolerance=a.qty_tolerance)
        Path(a.output).write_text(render_compare(report, format=a.format)); return 0
    if a.cmd=='benchmark':
        params=json.loads(Path(a.params).read_text()) if a.params else {}
        cfg=BacktestConfig(symbol=a.symbol,timeframe=a.timeframe,start_time=a.start,end_time=a.end,initial_capital=a.capital)
        report=run_benchmark(cfg,_load_class(a.strategy,a.cls),bars=_load_bars(a.bars),params=params,runs=a.runs)
        Path(a.output).write_text(render_benchmark(report,format=a.format)); return 0
    if a.cmd=='batch':
        strategy=_load_class(a.strategy,a.cls); bars=_load_bars(a.bars)
        cfg=BacktestConfig(symbol=a.symbol,timeframe=a.timeframe,start_time=a.start,end_time=a.end,initial_capital=a.capital)
        specs=json.loads(Path(a.jobs).read_text())
        jobs=[BacktestJob(str(j['job_id']),strategy,params=j.get('params',{}),config_overrides=j.get('config_overrides',{}),bars=bars) for j in specs]
        results=BatchBacktestRunner(cfg,backend=a.backend,max_workers=a.max_workers).run(jobs)
        Path(a.output).write_text(json.dumps({k:v.to_dict() if hasattr(v,'to_dict') else v for k,v in results.items()},indent=2,sort_keys=True,default=list)); return 0
    if a.cmd=='export':
        result=_load_result(a.input)
        if a.trades_csv:
            class R: pass
            r=R(); r.closed_trades=result.get('closed_trades') or []
            CSVTradeWriter().write(r,a.trades_csv)
        if a.summary_md:
            lines=[f"# Backtest summary", f"final_equity: {result.get('final_equity')}", f"net_profit: {result.get('net_profit')}", f"total_trades: {result.get('total_trades')}"]
            Path(a.summary_md).write_text('\n'.join(lines)+'\n')
        return 0
    print('Commands: run, compare, export, benchmark, batch.'); return 0
if __name__=='__main__': raise SystemExit(main())
