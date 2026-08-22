"""V5-WRM-001: fail-closed warmup phase machine and broker reset."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from openpine_contracts import Finality

from backtest_engine import BacktestConfig, BacktestEngine, Bar
from backtest_engine.core.warmup import (
    BrokerState,
    WarmupPhase,
    WarmupPhaseMachine,
    strategy_reads_broker_projection,
)
from backtest_engine.errors import ConfigError, WarmupAdmissionError

ROOT = Path(__file__).parents[2]
PRE_BARS = 3
BAR_COUNT = 8
INITIAL_CAPITAL = 10_000.0
POLICIES = ("CALC_ONLY", "TRADE_THROUGH_UNSCORED", "CALC_THEN_RESET_BROKER")


class EnterLastWarmup:
    """Emit a long entry on the last warmup bar; keep a running calc value."""

    required_runtime_capabilities: tuple[str, ...] = ()

    def __init__(self, params, runtime, ctx):
        self.ctx = ctx
        self.params = params
        self.calc = 0.0
        self.calc_series: list[float] = []
        self.buffer_on_first_score: int | None = None

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        last_warmup = int(self.params["last_warmup"])
        self.calc = self.calc * 0.5 + float(bar.close) * 0.5
        self.calc_series.append(self.calc)
        if bar_index == last_warmup + 1:
            self.buffer_on_first_score = len(self.ctx.buffer.commands)
        if bar_index == last_warmup:
            self.ctx.entry("L", "long", qty=1.0)
        if bar_index == last_warmup + 2:
            self.ctx.entry("S", "long", qty=1.0)


class ReadsBrokerProjection:
    required_runtime_capabilities = ("broker_projection_reads",)

    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        _ = self.ctx.state.position_size
        _ = self.ctx.state.open_trades
        if bar_index == 2:
            self.ctx.entry("L", "long", qty=1.0)


class HoldThroughScore:
    required_runtime_capabilities: tuple[str, ...] = ()

    def __init__(self, params, runtime, ctx):
        self.ctx = ctx
        self.params = params

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        if bar_index == int(self.params["score_entry"]):
            self.ctx.entry("H", "long", qty=1.0)


def _bars(n: int = BAR_COUNT) -> list[Bar]:
    return [
        Bar(
            time=1_000 + i,
            open=10.0 + i,
            high=11.0 + i,
            low=9.0 + i,
            close=10.5 + i,
            finality=Finality.FINAL,
        )
        for i in range(n)
    ]


def _cfg(policy: str | None, **kw: object) -> BacktestConfig:
    score_end = kw.pop("score_end_time", 1_000 + BAR_COUNT - 1)
    return BacktestConfig(
        symbol="S",
        timeframe="1m",
        start_time=1_000,
        end_time=1_000 + BAR_COUNT - 1,
        commission_type="none",
        initial_capital=INITIAL_CAPITAL,
        warmup_policy=policy,  # type: ignore[arg-type]
        score_start_time=1_000 + PRE_BARS,
        score_end_time=score_end,  # type: ignore[arg-type]
        score_end_policy=str(kw.pop("score_end_policy", "LEAVE_OPEN")),
        export_resume_state=bool(kw.pop("export_resume_state", False)),
        **kw,  # type: ignore[arg-type]
    )


def _run(policy: str, strategy: type = EnterLastWarmup, **kw: object):
    bars = _bars()
    engine = BacktestEngine(_cfg(policy, **kw))
    result = engine.run(
        strategy,
        params={"last_warmup": PRE_BARS - 1, "score_entry": PRE_BARS},
        bars=bars,
        effective_pre_bars=PRE_BARS,
    )
    return engine, result, bars


def test_phase_machine_transitions_initial_warmup_score_after_finalized() -> None:
    machine = WarmupPhaseMachine(
        policy="CALC_THEN_RESET_BROKER",
        warmup_end_index=2,
        score_end_index=5,
        series_len=8,
    )
    assert machine.phase is WarmupPhase.INITIAL
    first = machine.begin_bar(0)
    assert machine.phase is WarmupPhase.WARMUP
    assert first.intent_emission_allowed is True
    assert first.broker_execution_allowed is False
    machine.end_bar(2)
    score = machine.begin_bar(3)
    assert machine.phase is WarmupPhase.SCORE
    assert score.intent_emission_allowed is True
    assert score.broker_execution_allowed is True
    after = machine.begin_bar(6)
    assert machine.phase is WarmupPhase.AFTER
    assert after.phase is WarmupPhase.AFTER
    machine.finalize()
    assert machine.phase is WarmupPhase.FINALIZED


@pytest.mark.parametrize("policy", POLICIES)
def test_each_policy_accepts_entry_on_last_warmup_bar(policy: str) -> None:
    engine, result, _ = _run(policy)
    assert result.status == "completed"
    assert engine.warmup_phase is WarmupPhase.FINALIZED
    discarded = [
        event
        for event in (result.events or [])
        if event.code == "WARMUP_INTENT_DISCARDED"
    ]
    if policy == "TRADE_THROUGH_UNSCORED":
        assert engine.open_trades or engine.closed_trades or engine.fills
        assert not discarded
    else:
        assert discarded


def test_calc_only_does_not_mutate_broker_at_score_start() -> None:
    engine, result, _ = _run("CALC_ONLY")
    opening = engine.score_opening_broker
    canonical = BrokerState.canonical_initial(INITIAL_CAPITAL)
    assert opening == canonical
    assert engine.warmup_boundary_buffer_len_before == 0
    assert engine.warmup_boundary_buffer_len_after == 0
    warmup_fills = [fill for fill in engine.fills if fill.phase == "warmup"]
    assert warmup_fills == []
    assert all(
        order.phase != "warmup" or order.status == "cancelled"
        for order in engine.orders
    )


def test_calc_then_reset_does_not_execute_prehistory_intent_on_first_score_bar() -> (
    None
):
    engine, result, _ = _run("CALC_THEN_RESET_BROKER")
    score_fills = [fill for fill in engine.fills if fill.phase == "score"]
    leaked = [
        fill
        for fill in score_fills
        if fill.order_id == "L" and fill.bar_index == PRE_BARS
    ]
    assert leaked == []
    assert engine.score_opening_broker == BrokerState.canonical_initial(INITIAL_CAPITAL)
    discarded = [
        event
        for event in (result.events or [])
        if event.code == "WARMUP_INTENT_DISCARDED" and event.phase == "warmup"
    ]
    assert discarded


def test_buffer_empty_immediately_before_and_after_boundary() -> None:
    engine, result, _ = _run("CALC_THEN_RESET_BROKER")
    assert engine.warmup_boundary_buffer_len_before == 0
    assert engine.warmup_boundary_buffer_len_after == 0
    strategy = engine.last_strategy
    assert isinstance(strategy, EnterLastWarmup)
    assert strategy.buffer_on_first_score == 0


def test_broker_fields_match_canonical_initial_after_reset() -> None:
    engine, _, _ = _run("CALC_THEN_RESET_BROKER")
    opening = engine.score_opening_broker
    canonical = BrokerState.canonical_initial(INITIAL_CAPITAL)
    assert opening is not canonical
    assert opening == canonical
    assert opening.cash == INITIAL_CAPITAL
    assert opening.equity == INITIAL_CAPITAL
    assert opening.position.size == 0.0
    assert opening.position.direction == "flat"
    assert opening.orders == ()
    assert opening.fills == ()
    assert opening.open_trades == ()
    assert opening.closed_trades == ()
    assert opening.last_trade_bar is None


def test_calc_vars_and_series_are_preserved_across_reset() -> None:
    engine, _, bars = _run("CALC_THEN_RESET_BROKER")
    strategy = engine.last_strategy
    assert isinstance(strategy, EnterLastWarmup)
    assert len(strategy.calc_series) == len(bars)
    expected = 0.0
    for bar in bars:
        expected = expected * 0.5 + float(bar.close) * 0.5
    assert strategy.calc == pytest.approx(expected)
    assert strategy.calc_series[-1] == pytest.approx(expected)


def test_resume_on_boundary_matches_continuous_score_ledger_hash() -> None:
    bars = _bars()
    params = {"last_warmup": PRE_BARS - 1, "score_entry": PRE_BARS}
    continuous = BacktestEngine(_cfg("CALC_THEN_RESET_BROKER")).run(
        EnterLastWarmup,
        params=params,
        bars=bars,
        effective_pre_bars=PRE_BARS,
    )
    prefix = BacktestEngine(
        _cfg("CALC_THEN_RESET_BROKER", export_resume_state=True)
    ).run(
        EnterLastWarmup,
        params=params,
        bars=bars[:PRE_BARS],
        effective_pre_bars=PRE_BARS,
    )
    assert prefix.resume_state is not None
    resumed = BacktestEngine(_cfg("CALC_THEN_RESET_BROKER")).run(
        EnterLastWarmup,
        params=params,
        bars=bars,
        resume_state=prefix.resume_state,
        effective_pre_bars=PRE_BARS,
    )
    assert resumed.score_ledger_hash == continuous.score_ledger_hash
    assert resumed.score_ledger_hash


def test_strategy_reading_broker_projection_is_rejected_on_unsafe_mode() -> None:
    assert strategy_reads_broker_projection(ReadsBrokerProjection) is True
    assert strategy_reads_broker_projection(EnterLastWarmup) is False
    with pytest.raises(WarmupAdmissionError, match="WARMUP_UNSAFE_BROKER_PROJECTION"):
        BacktestEngine(_cfg("CALC_THEN_RESET_BROKER")).run(
            ReadsBrokerProjection,
            bars=_bars(),
            effective_pre_bars=PRE_BARS,
        )
    with pytest.raises(WarmupAdmissionError, match="WARMUP_UNSAFE_BROKER_PROJECTION"):
        BacktestEngine(_cfg("CALC_ONLY")).run(
            ReadsBrokerProjection,
            bars=_bars(),
            effective_pre_bars=PRE_BARS,
        )

    missing_metadata = type("MissingCapabilityMetadata", (), {})
    with pytest.raises(WarmupAdmissionError, match="CAPABILITY_METADATA_REQUIRED"):
        BacktestEngine(_cfg("CALC_ONLY")).run(
            missing_metadata,
            bars=_bars(),
            effective_pre_bars=PRE_BARS,
        )
    result = BacktestEngine(_cfg("TRADE_THROUGH_UNSCORED")).run(
        ReadsBrokerProjection,
        bars=_bars(),
        effective_pre_bars=PRE_BARS,
    )
    assert result.status == "completed"


def test_warmup_capabilities_accept_support_profile_and_reject_invalid_shape() -> None:
    profile_reader = type(
        "ProfileReader", (), {"support_profile": {"broker_projection_reads": True}}
    )
    profile_plain = type(
        "ProfilePlain",
        (),
        {"support_profile": {"required_runtime_capabilities": []}},
    )
    invalid = type("InvalidCapabilities", (), {"required_runtime_capabilities": "bad"})

    assert strategy_reads_broker_projection(profile_reader) is True
    assert strategy_reads_broker_projection(profile_plain) is False
    with pytest.raises(WarmupAdmissionError, match="METADATA_INVALID"):
        strategy_reads_broker_projection(invalid)


def test_warmup_and_score_ledgers_use_different_phase_labels() -> None:
    _, result, _ = _run("TRADE_THROUGH_UNSCORED")
    phases = {event.phase for event in (result.events or []) if event.phase}
    assert "warmup" in phases
    assert "score" in phases
    assert result.warmup_ledger_hash != result.score_ledger_hash


def test_property_warmup_phase_events_do_not_mutate_score_opening_in_reset_mode() -> (
    None
):
    engine, result, _ = _run("CALC_THEN_RESET_BROKER")
    warmup_events = [
        event for event in (result.events or []) if event.phase == "warmup"
    ]
    assert warmup_events
    assert engine.score_opening_broker == BrokerState.canonical_initial(INITIAL_CAPITAL)
    for event in warmup_events:
        assert event.phase == "warmup"
    assert engine.score_opening_broker.cash == INITIAL_CAPITAL
    assert engine.score_opening_broker.position.direction == "flat"


def test_trade_through_carries_cash_position_and_exposes_metrics_baseline() -> None:
    engine, result, _ = _run("TRADE_THROUGH_UNSCORED")
    assert engine.score_opening_broker.cash != INITIAL_CAPITAL or (
        engine.score_opening_broker.position.direction != "flat"
        or engine.score_opening_broker.open_trades
        or engine.score_opening_broker.orders
    )
    assert result.score_equity_baseline == engine.score_opening_broker.equity
    assert result.score_equity_baseline != INITIAL_CAPITAL or engine.fills


def test_phase_stamped_on_intents_fills_trades_and_metrics() -> None:
    engine, result, _ = _run("TRADE_THROUGH_UNSCORED")
    assert result.events
    assert all(event.phase in {"warmup", "score", "after"} for event in result.events)
    assert engine.fills
    assert all(fill.phase in {"warmup", "score", "after"} for fill in engine.fills)
    for trade in engine.open_trades + engine.closed_trades:
        assert trade.phase in {"warmup", "score", "after"}
    assert result.equity_curve
    assert all(
        point.phase in {"warmup", "score", "after"} for point in result.equity_curve
    )


def test_support_profile_declares_warmup_broker_reset_safe() -> None:
    profile = json.loads((ROOT / "support_profile.json").read_text(encoding="utf-8"))
    capability = profile["capabilities"]["warmup_broker_reset_safe"]
    assert capability["status"] == "supported"


def test_unknown_warmup_policy_and_end_policy_fail_closed() -> None:
    with pytest.raises(ConfigError, match="warmup_policy"):
        BacktestEngine(_cfg("NOT_A_POLICY")).run(
            EnterLastWarmup,
            params={"last_warmup": 2},
            bars=_bars(),
            effective_pre_bars=PRE_BARS,
        )
    with pytest.raises(ConfigError, match="score_end_policy"):
        BacktestEngine(_cfg("CALC_ONLY", score_end_policy="YEET")).run(
            EnterLastWarmup,
            params={"last_warmup": 2},
            bars=_bars(),
            effective_pre_bars=PRE_BARS,
        )


def test_score_end_policies_force_close_and_leave_open() -> None:
    params = {"score_entry": PRE_BARS}
    leave = BacktestEngine(_cfg("CALC_THEN_RESET_BROKER", score_end_time=1_005)).run(
        HoldThroughScore,
        params=params,
        bars=_bars(),
        effective_pre_bars=PRE_BARS,
    )
    forced = BacktestEngine(
        _cfg(
            "CALC_THEN_RESET_BROKER",
            score_end_time=1_005,
            score_end_policy="FORCE_CLOSE",
        )
    ).run(
        HoldThroughScore,
        params=params,
        bars=_bars(),
        effective_pre_bars=PRE_BARS,
    )
    assert leave.open_trades
    assert not forced.open_trades
    assert forced.closed_trades


def test_warmup_reset_documentation_covers_boundary_and_end_policies() -> None:
    text = (ROOT / "docs" / "WARMUP_RESET.md").read_text(encoding="utf-8")
    for token in (
        "inclusive",
        "exclusive",
        "score equity baseline",
        "pending order",
        "open position",
        "MARK_TO_MARKET",
        "FORCE_CLOSE",
        "LEAVE_OPEN",
        "CALC_THEN_RESET_BROKER",
    ):
        assert token in text


def test_trade_through_and_calc_only_independent_bar_decisions() -> None:
    reset = WarmupPhaseMachine(
        "CALC_THEN_RESET_BROKER", warmup_end_index=2, score_end_index=5, series_len=8
    )
    through = WarmupPhaseMachine(
        "TRADE_THROUGH_UNSCORED", warmup_end_index=2, score_end_index=5, series_len=8
    )
    calc = WarmupPhaseMachine(
        "CALC_ONLY", warmup_end_index=2, score_end_index=5, series_len=8
    )
    reset_warmup = reset.begin_bar(1)
    through_warmup = through.begin_bar(1)
    calc_warmup = calc.begin_bar(1)
    assert reset_warmup.intent_emission_allowed is True
    assert reset_warmup.broker_execution_allowed is False
    assert through_warmup.intent_emission_allowed is True
    assert through_warmup.broker_execution_allowed is True
    assert calc_warmup.intent_emission_allowed is True
    assert calc_warmup.broker_execution_allowed is False
    assert reset.begin_bar(3).broker_execution_allowed is True
    assert calc.begin_bar(3).broker_execution_allowed is True
