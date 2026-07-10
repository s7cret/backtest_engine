from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from backtest_engine.release import release_report
from backtest_engine.support_profile import REQUIRED_EXCLUDED_FEATURES

ROOT = Path(__file__).parents[2]
PROFILE_PATH = ROOT / "support_profile.json"


def test_realtime_replay_is_explicitly_excluded_in_machine_readable_profile() -> None:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

    assert profile["schema_version"] == 1
    assert profile["profile"] == "backtest-engine-4.0"
    assert set(profile["features"]) == set(REQUIRED_EXCLUDED_FEATURES)
    for feature_name in REQUIRED_EXCLUDED_FEATURES:
        feature = profile["features"][feature_name]
        assert feature["status"] == "excluded"
        assert feature["reason_code"] == "platform_blocked"
        assert feature["evidence_ref"] == "guarded_realtime_replay"
        assert feature["reason"]

    evidence = profile["evidence"]["guarded_realtime_replay"]
    assert evidence["runtime_execution"] == "fail_closed"
    assert evidence["tradingview_parity"] == "not_claimed"


def test_guarded_replay_evidence_has_stable_schedule_and_attempt_hashes() -> None:
    support_profile = importlib.import_module("backtest_engine.support_profile")

    first = support_profile.build_realtime_replay_evidence()
    second = support_profile.build_realtime_replay_evidence()

    assert first == second
    assert first == {
        "fixture_id": "phase2_guarded_realtime_replay_v1",
        "schedule_sha256": "da8da205a4d7a493cd908ee74d1560d2895ac8bb4ae947e47405842dd613617a",
        "attempt_sha256": "e5da3438c14bdc2e6aface90fe4084c44f1e4cf08001c946874b3ac697b80015",
        "schedule_slice_count": 2,
        "attempt_count": 4,
        "all_attempts_rolled_back": True,
        "strategy_invoked": False,
    }


def test_declared_hash_evidence_matches_guarded_skeleton_output() -> None:
    support_profile = importlib.import_module("backtest_engine.support_profile")
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    declared = profile["evidence"]["guarded_realtime_replay"]

    assert declared["evidence_kind"] == "deterministic_guarded_skeleton"
    assert declared["synthetic_fixture"] is True
    assert declared["oracle_verified"] is False
    generated = support_profile.build_realtime_replay_evidence()
    assert {key: declared[key] for key in generated} == generated


def _write_release_root(root: Path, profile: dict[str, object] | None) -> None:
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "backtest-engine"\nversion = "4.0.0"\n',
        encoding="utf-8",
    )
    if profile is not None:
        (root / "support_profile.json").write_text(
            json.dumps(profile), encoding="utf-8"
        )


def _profile() -> dict[str, object]:
    value = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_release_report_verifies_realtime_support_profile_and_hashes() -> None:
    report = release_report(ROOT)

    assert report.support_profile_ok is True
    assert (
        report.realtime_schedule_sha256
        == "da8da205a4d7a493cd908ee74d1560d2895ac8bb4ae947e47405842dd613617a"
    )
    assert (
        report.realtime_attempt_sha256
        == "e5da3438c14bdc2e6aface90fe4084c44f1e4cf08001c946874b3ac697b80015"
    )


def test_release_report_fails_closed_when_support_profile_is_missing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release"
    _write_release_root(root, None)

    report = release_report(root)

    assert report.support_profile_ok is False
    assert any("missing support profile" in note for note in report.notes)


def test_release_report_fails_closed_when_realtime_exclusion_disappears(
    tmp_path: Path,
) -> None:
    profile = _profile()
    features = profile["features"]
    assert isinstance(features, dict)
    calc_on_every_tick = features["calc_on_every_tick"]
    assert isinstance(calc_on_every_tick, dict)
    calc_on_every_tick["status"] = "supported"
    root = tmp_path / "release"
    _write_release_root(root, profile)

    report = release_report(root)

    assert report.support_profile_ok is False
    assert any(
        "calc_on_every_tick must remain excluded" in note for note in report.notes
    )


def test_release_report_rejects_missing_or_unknown_realtime_capabilities(
    tmp_path: Path,
) -> None:
    profile = _profile()
    features = profile["features"]
    assert isinstance(features, dict)
    del features["tick_driven_orders"]
    features["unverified_new_tick_feature"] = {
        "status": "supported",
        "reason_code": "none",
        "reason": "not verified",
        "evidence_ref": "guarded_realtime_replay",
    }
    root = tmp_path / "release"
    _write_release_root(root, profile)

    report = release_report(root)

    assert report.support_profile_ok is False
    assert any(
        "features must exactly match excluded capabilities" in note
        for note in report.notes
    )
    assert any("evidence key status is missing" in note for note in report.notes)

    invalid_profile = _profile()
    invalid_profile["features"] = []
    invalid_root = tmp_path / "invalid-release"
    _write_release_root(invalid_root, invalid_profile)
    invalid_report = release_report(invalid_root)
    assert invalid_report.support_profile_ok is False
    assert any("features must be an object" in note for note in invalid_report.notes)


def test_release_report_fails_closed_when_hash_evidence_disappears(
    tmp_path: Path,
) -> None:
    profile = _profile()
    evidence = profile["evidence"]
    assert isinstance(evidence, dict)
    guarded = evidence["guarded_realtime_replay"]
    assert isinstance(guarded, dict)
    del guarded["attempt_sha256"]
    root = tmp_path / "release"
    _write_release_root(root, profile)

    report = release_report(root)

    assert report.support_profile_ok is False
    assert any(
        "evidence key attempt_sha256 is missing" in note for note in report.notes
    )


def test_release_report_fails_closed_when_hash_evidence_is_tampered(
    tmp_path: Path,
) -> None:
    profile = _profile()
    evidence = profile["evidence"]
    assert isinstance(evidence, dict)
    guarded = evidence["guarded_realtime_replay"]
    assert isinstance(guarded, dict)
    guarded["schedule_sha256"] = "0" * 64
    root = tmp_path / "release"
    _write_release_root(root, profile)

    report = release_report(root)

    assert report.support_profile_ok is False
    assert any("schedule_sha256 does not match" in note for note in report.notes)


def test_release_report_fails_closed_when_support_profile_json_is_invalid(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release"
    _write_release_root(root, _profile())
    (root / "support_profile.json").write_text("{", encoding="utf-8")

    report = release_report(root)

    assert report.support_profile_ok is False
    assert any("invalid support profile" in note for note in report.notes)


def test_release_report_fails_closed_when_evidence_cannot_be_recomputed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    support_profile = importlib.import_module("backtest_engine.support_profile")

    def fail() -> dict[str, object]:
        raise RuntimeError("broken skeleton")

    monkeypatch.setattr(support_profile, "build_realtime_replay_evidence", fail)

    report = release_report(ROOT)

    assert report.support_profile_ok is False
    assert report.realtime_schedule_sha256 is None
    assert report.realtime_attempt_sha256 is None
    assert any("could not recompute" in note for note in report.notes)
