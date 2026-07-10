"""Machine-verifiable evidence for the declared realtime replay support boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from backtest_engine.config import BacktestConfig
from backtest_engine.context import StrategyContext
from backtest_engine.core.deterministic_hash import sha256_obj
from backtest_engine.core.engine import BacktestEngine
from backtest_engine.core.realtime import build_bar_tick_schedule
from backtest_engine.models import Bar, Tick

EvidenceValue = str | int | bool
_MISSING = object()
REQUIRED_EXCLUDED_FEATURES = (
    "calc_on_every_tick",
    "tick_replay",
    "tick_driven_orders",
    "tick_driven_fills",
    "tick_driven_trades",
    "realtime_restart",
    "realtime_result_hash",
)


@dataclass(frozen=True, slots=True)
class SupportProfileVerification:
    ok: bool
    schedule_sha256: str | None
    attempt_sha256: str | None
    notes: tuple[str, ...]


def build_realtime_replay_evidence() -> dict[str, EvidenceValue]:
    """Hash deterministic scheduler/attempt output without enabling tick execution."""

    bars = (
        Bar(
            time=1_700_000_000_000,
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.0,
            volume=25.0,
            time_close=1_700_000_060_000,
        ),
        Bar(
            time=1_700_000_060_000,
            open=101.0,
            high=103.0,
            low=100.0,
            close=102.0,
            volume=30.0,
            time_close=1_700_000_120_000,
        ),
    )
    ticks = (
        Tick(time=1_700_000_000_000, price=100.0, volume=1.0),
        Tick(time=1_700_000_059_999, price=101.0, volume=2.0),
        Tick(time=1_700_000_060_000, price=101.0, volume=3.0),
        Tick(time=1_700_000_119_999, price=102.0, volume=4.0),
    )
    schedule = build_bar_tick_schedule(bars, ticks)
    engine = BacktestEngine(
        BacktestConfig(
            symbol="PHASE2:EVIDENCE",
            timeframe="1",
            start_time=bars[0].time,
            end_time=int(bars[-1].time_close or bars[-1].time),
            commission_type="none",
        )
    )
    ctx = StrategyContext(engine.config, engine.state)
    attempts = tuple(
        attempt
        for tick_slice in schedule
        for attempt in engine._guarded_realtime_tick_loop_skeleton(tick_slice, ctx=ctx)
    )
    schedule_payload = [
        {
            "bar_index": tick_slice.bar_index,
            "bar": {
                "time": tick_slice.bar.time,
                "time_close": tick_slice.bar.time_close,
            },
            "ticks": [
                {
                    "time": tick.time,
                    "price": tick.price,
                    "volume": tick.volume,
                }
                for tick in tick_slice.ticks
            ],
        }
        for tick_slice in schedule
    ]
    attempt_payload = [
        {
            "bar_index": attempt.bar_index,
            "tick_index": attempt.tick_index,
            "tick": {
                "time": attempt.tick.time,
                "price": attempt.tick.price,
                "volume": attempt.tick.volume,
            },
            "rolled_back": attempt.rolled_back,
            "strategy_invoked": attempt.strategy_invoked,
            "policy": attempt.policy,
            "committed": attempt.committed,
        }
        for attempt in attempts
    ]
    return {
        "fixture_id": "phase2_guarded_realtime_replay_v1",
        "schedule_sha256": sha256_obj(schedule_payload),
        "attempt_sha256": sha256_obj(attempt_payload),
        "schedule_slice_count": len(schedule),
        "attempt_count": len(attempts),
        "all_attempts_rolled_back": all(attempt.rolled_back for attempt in attempts),
        "strategy_invoked": any(attempt.strategy_invoked for attempt in attempts),
    }


def _profile_value(profile: object, path: tuple[str, ...]) -> object:
    value = profile
    try:
        for key in path:
            value = value[key]  # type: ignore[index]
    except (KeyError, TypeError):
        return _MISSING
    return value


def verify_realtime_replay_support_profile(
    path: str | Path,
) -> SupportProfileVerification:
    """Verify that realtime replay stays excluded and its scaffold hashes match."""

    try:
        generated = build_realtime_replay_evidence()
    except Exception as exc:
        return SupportProfileVerification(
            False,
            None,
            None,
            (f"realtime evidence could not recompute: {type(exc).__name__}: {exc}",),
        )

    schedule_sha256 = str(generated["schedule_sha256"])
    attempt_sha256 = str(generated["attempt_sha256"])
    profile_path = Path(path)
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return SupportProfileVerification(
            False,
            schedule_sha256,
            attempt_sha256,
            (f"missing support profile: {profile_path}",),
        )
    except (OSError, json.JSONDecodeError) as exc:
        return SupportProfileVerification(
            False,
            schedule_sha256,
            attempt_sha256,
            (f"invalid support profile: {type(exc).__name__}: {exc}",),
        )

    reason = (
        "Exact TradingView realtime rollback, varip, and tick semantics are not "
        "oracle-verified."
    )
    expectations: list[tuple[tuple[str, ...], object, str]] = [
        (("schema_version",), 1, "support profile schema_version must be 1"),
        (
            ("profile",),
            "backtest-engine-4.0",
            "support profile name must be backtest-engine-4.0",
        ),
        (
            ("evidence", "guarded_realtime_replay", "evidence_kind"),
            "deterministic_guarded_skeleton",
            "realtime evidence must identify the guarded skeleton",
        ),
        (
            ("evidence", "guarded_realtime_replay", "synthetic_fixture"),
            True,
            "realtime evidence must identify its fixture as synthetic",
        ),
        (
            ("evidence", "guarded_realtime_replay", "oracle_verified"),
            False,
            "realtime evidence must not claim oracle verification",
        ),
        (
            ("evidence", "guarded_realtime_replay", "runtime_execution"),
            "fail_closed",
            "realtime runtime execution must remain fail_closed",
        ),
        (
            ("evidence", "guarded_realtime_replay", "tradingview_parity"),
            "not_claimed",
            "realtime evidence must not claim TradingView parity",
        ),
    ]
    for feature_name in REQUIRED_EXCLUDED_FEATURES:
        expectations.extend(
            (
                (
                    ("features", feature_name, "status"),
                    "excluded",
                    f"{feature_name} must remain excluded",
                ),
                (
                    ("features", feature_name, "reason_code"),
                    "platform_blocked",
                    f"{feature_name} reason_code must remain platform_blocked",
                ),
                (
                    ("features", feature_name, "reason"),
                    reason,
                    f"{feature_name} must retain the oracle-verification blocker",
                ),
                (
                    ("features", feature_name, "evidence_ref"),
                    "guarded_realtime_replay",
                    f"{feature_name} must reference guarded replay evidence",
                ),
            )
        )
    expectations.extend(
        (
            ("evidence", "guarded_realtime_replay", key),
            expected,
            f"{key} does not match recomputed guarded skeleton evidence",
        )
        for key, expected in generated.items()
    )
    notes: list[str] = []
    features = _profile_value(profile, ("features",))
    if not isinstance(features, dict):
        notes.append("support profile features must be an object")
    elif set(features) != set(REQUIRED_EXCLUDED_FEATURES):
        notes.append(
            "support profile features must exactly match excluded capabilities"
        )
    for field_path, expected, mismatch_note in expectations:
        actual = _profile_value(profile, field_path)
        if actual is _MISSING:
            notes.append(f"support profile evidence key {field_path[-1]} is missing")
        elif actual != expected:
            notes.append(mismatch_note)
    return SupportProfileVerification(
        not notes, schedule_sha256, attempt_sha256, tuple(notes)
    )
