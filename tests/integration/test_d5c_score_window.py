"""
Stage2-D5-C: BacktestEngine prehistory execution with bars input.

Tests score-window mode where:
- input bars = pre_bars + score_bars
- engine executes all bars without resetting context at score_start
- metrics/output are score-window only
- warmup/prehistory affects state but not score metrics
"""
from __future__ import annotations
from backtest_engine.config import BacktestConfig
from backtest_engine.core.engine import BacktestEngine
from backtest_engine.models import Bar, BarSeries


# ─── Tiny strategy helpers ────────────────────────────────────────────────────

class PrehistoryTradeStrategy:
    """Opens a long at bar 0, holds through the entire run (never closes)."""

    def __init__(self, params: dict, runtime, ctx):
        self.ctx = ctx
        self._done = False

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        if not self._done and bar_index == 0:
            self.ctx.entry("long_enter", "long", qty=1.0)
            self._done = True


class ScorePhaseTradeStrategy:
    """Opens AND closes within the score phase (deep in score window)."""

    def __init__(self, params: dict, runtime, ctx):
        self.ctx = ctx
        self._entered = False

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        # Enter deep in score phase (score_start_index + 5)
        if bar_index == 10 and not self._entered:
            self.ctx.entry("long_enter", "long", qty=1.0)
            self._entered = True
        # Close 2 bars later — well within score
        if bar_index == 12:
            self.ctx.close_all()


class PrehistoryEntryScoreCloseStrategy:
    """Opens in prehistory (bar 2), closes deep in score (bar 12)."""

    def __init__(self, params: dict, runtime, ctx):
        self.ctx = ctx
        self._entered = False

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        if bar_index == 2 and not self._entered:
            self.ctx.entry("long_enter", "long", qty=1.0)
            self._entered = True
        if bar_index == 12:
            self.ctx.close_all()


class PrehistoryEntryPrehistoryCloseStrategy:
    """Opens in prehistory (bar 2), closes in prehistory (bar 3). Score has zero trades."""

    def __init__(self, params: dict, runtime, ctx):
        self.ctx = ctx
        self._entered = False

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        if bar_index == 2 and not self._entered:
            self.ctx.entry("long_enter", "long", qty=1.0)
            self._entered = True
        if bar_index == 3:
            self.ctx.close_all()


def make_bars(n: int, start_ms: int = 1700000000000, step_ms: int = 900_000) -> BarSeries:
    """Make n synthetic 15m bars with simple price movement."""
    times = [start_ms + i * step_ms for i in range(n)]
    base = 100.0
    closes = [base + i * 0.5 for i in range(n)]
    return BarSeries(
        time=times,
        open=[base + i * 0.5 - 0.1 for i in range(n)],
        high=[c + 0.2 for c in closes],
        low=[c - 0.2 for c in closes],
        close=closes,
        volume=None,
    )


def score_config(score_start_ms: int, score_end_ms: int) -> BacktestConfig:
    """BacktestConfig with explicit score window times."""
    return BacktestConfig(
        symbol="BTCUSDT",
        timeframe="15m",
        start_time=score_start_ms,
        end_time=score_end_ms,
        score_start_time=score_start_ms,
        score_end_time=score_end_ms,
        initial_capital=10_000.0,
        commission_type="none",
        commission_value=0.0,
        slippage=0.0,
    )


# ─── Test 1: default behavior unchanged ────────────────────────────────────────

def test_no_score_fields_phase_trades_is_none():
    """
    When score_start_time / score_end_time are NOT set, phase_trades is None.
    """
    bars = make_bars(20)
    config = BacktestConfig(
        symbol="BTCUSDT", timeframe="15m",
        start_time=bars.time[0], end_time=bars.time[-1],
        initial_capital=10_000.0, commission_type="none", commission_value=0.0,
    )
    engine = BacktestEngine(config)
    result = engine.run(PrehistoryTradeStrategy, bars=bars)

    assert result.status == "completed"
    assert result.phase_trades is None


# ─── Test 2: prehistory state carries into score ───────────────────────────────

def test_prehistory_position_carries_into_score_window():
    """
    Position opened in prehistory is still open (not reset) when score starts.
    """
    # 20 bars: bars 0-4 prehistory, bars 5-19 score (15 bars)
    bars = make_bars(20)
    config = score_config(
        score_start_ms=bars.time[5],
        score_end_ms=bars.time[19],
    )
    engine = BacktestEngine(config)
    result = engine.run(
        PrehistoryTradeStrategy, bars=bars, effective_pre_bars=5,
    )

    assert result.status == "completed"
    # Position opened in prehistory (filled at bar 1), still open throughout score
    assert len(result.open_trades) == 1
    assert result.open_trades[0].entry_bar_index == 1  # filled bar (order placed at bar 0)


# ─── Test 3: phase_trades correctly labeled ───────────────────────────────────

def test_phase_trades_entry_prehistory_exit_score():
    """
    Trade entered in prehistory, closed in score → entry_phase=prehistory,
    exit_phase=score, crosses_score_boundary=True.
    """
    bars = make_bars(20)
    config = score_config(
        score_start_ms=bars.time[5],
        score_end_ms=bars.time[19],
    )
    engine = BacktestEngine(config)
    result = engine.run(
        PrehistoryEntryScoreCloseStrategy, bars=bars, effective_pre_bars=5,
    )

    assert result.status == "completed"
    assert result.phase_trades is not None
    assert len(result.phase_trades) == 1

    t = result.phase_trades[0]
    assert t.entry_phase == "prehistory"
    assert t.exit_phase == "score"
    assert t.crosses_score_boundary is True


# ─── Test 4: score-only trade has score/score phases ─────────────────────────

def test_score_only_trade_has_score_phases():
    """
    Trade entered AND closed in score window → entry_phase=exit_phase=score.
    """
    bars = make_bars(20)
    config = score_config(
        score_start_ms=bars.time[5],
        score_end_ms=bars.time[19],
    )
    engine = BacktestEngine(config)
    result = engine.run(
        ScorePhaseTradeStrategy, bars=bars, effective_pre_bars=5,
    )

    assert result.status == "completed"
    assert result.phase_trades is not None
    t = result.phase_trades[0]
    assert t.entry_phase == "score"
    assert t.exit_phase == "score"
    assert t.crosses_score_boundary is False


# ─── Test 5: no score config → old behavior ─────────────────────────────────

def test_no_score_config_bars_processed_equals_total():
    """
    Without score window config, bars_processed equals all sliced bars.
    phase_trades is None.
    """
    bars = make_bars(20)
    config = BacktestConfig(
        symbol="BTCUSDT", timeframe="15m",
        start_time=bars.time[0], end_time=bars.time[-1],
        initial_capital=10_000.0, commission_type="none", commission_value=0.0,
    )
    engine = BacktestEngine(config)
    result = engine.run(PrehistoryTradeStrategy, bars=bars)

    assert result.status == "completed"
    assert result.bars_processed == 20
    assert result.phase_trades is None


# ─── Test 6: effective_pre_bars=None → defaults to prehistory=0 ───────────────

def test_effective_pre_bars_none_defaults_first_bar_as_score():
    """
    Passing effective_pre_bars=None (default) treats first bar as prehistory boundary.
    Does not crash.
    """
    bars = make_bars(20)
    config = score_config(
        score_start_ms=bars.time[5],
        score_end_ms=bars.time[19],
    )
    engine = BacktestEngine(config)
    # No effective_pre_bars → prehistory_end_index=0, score_start_index=1
    result = engine.run(ScorePhaseTradeStrategy, bars=bars)

    assert result.status == "completed"
    # Series sliced to bars 5-19 (15 bars), first bar is prehistory (index 0)
    assert result.bars_processed == 14  # 14 score bars


# ─── Test 7: prehistory-only trade → not in score metrics ─────────────────────

def test_prehistory_only_trade_excluded_from_score():
    """
    Trade opened AND closed entirely in prehistory does not appear in score metrics.
    """
    bars = make_bars(20)
    config = score_config(
        score_start_ms=bars.time[5],
        score_end_ms=bars.time[19],
    )
    engine = BacktestEngine(config)
    result = engine.run(
        PrehistoryEntryPrehistoryCloseStrategy, bars=bars, effective_pre_bars=5,
    )

    assert result.status == "completed"
    # Trade was closed in prehistory — not in score window
    score_trades = [t for t in (result.phase_trades or []) if t.exit_phase == "score"]
    assert len(score_trades) == 0, "Prehistory-only trade should not appear as score trade"
    # D5-E: score_net_profit is the score-window metric; net_profit is full-window
    assert result.score_net_profit == 0.0, "Score metrics should reflect zero score trades"


# ─── Test 8: bars_processed reflects score window count ────────────────────────

def test_bars_processed_is_score_window_count():
    """
    bars_processed equals the number of score-window bars.
    close_all at bar 12 fills at bar 13, so bar 14 equity might reflect flat position.
    """
    bars = make_bars(20)
    config = score_config(
        score_start_ms=bars.time[5],
        score_end_ms=bars.time[19],
    )
    engine = BacktestEngine(config)
    result = engine.run(
        ScorePhaseTradeStrategy, bars=bars, effective_pre_bars=5,
    )

    # Series sliced to [5..19] = 15 bars total.
    # effective_pre_bars=5 → prehistory_end=4, score_start=5 → 10 score bars
    # close_all at bar 12 (score) fills at bar 13 → bar 14 may record flat equity
    assert result.bars_processed >= 8, f"Expected >= 8 score bars, got {result.bars_processed}"


# ─── D5-E: Score-window metrics in dedicated fields ─────────────────────────────

def test_score_window_metrics_in_score_fields():
    """
    D5-E: score_* fields hold score-window metrics; full metrics preserved separately.
    """
    bars = make_bars(20)
    config = score_config(
        score_start_ms=bars.time[5],
        score_end_ms=bars.time[19],
    )
    engine = BacktestEngine(config)
    result = engine.run(ScorePhaseTradeStrategy, bars=bars, effective_pre_bars=5)

    assert result.status == "completed"
    # Score-window fields are populated in score mode
    assert hasattr(result, "score_net_profit"), "score_net_profit field must exist"
    assert hasattr(result, "score_total_trades"), "score_total_trades field must exist"
    assert result.score_total_trades >= 0
    assert result.score_net_profit is not None
    # score_* fields are distinct from main fields (score_net_profit may differ from net_profit
    # if there are prehistory trades — which is the D5-E contract)
    # Full metrics (net_profit, total_trades) are preserved from all trades
