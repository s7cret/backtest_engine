"""Exercise real delegated transactions and reject malformed boundary data."""
import pytest

from backtest_engine import BacktestConfig
from backtest_engine.core import delegated_strategy_intents as intents
from backtest_engine.core.strategy_capabilities import STRATEGY_COMMANDS
from pinelib.errors import PineRuntimeError
from tests.unit.test_strategy_host_surface import dispatch, handler, seal, transaction


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("recalc_iteration", True, ValueError),
        ("recalc_iteration", -1, ValueError),
        ("pine_version", True, ValueError),
        ("pine_version", 7, ValueError),
        ("identity", None, TypeError),
        ("producer_commit", "F" * 40, ValueError),
        ("producer_commit", "abc", ValueError),
        ("bar_open_time_utc_ms", {True: 1}, ValueError),
        ("bar_open_time_utc_ms", {0: -1}, ValueError),
    ],
)
def test_constructor_does_not_normalize_invalid_identities(field, value, error):
    known = handler()
    kwargs = dict(identity=known.identity, producer_commit=known.producer_commit,
                  bar_open_time_utc_ms={0: 60000}, config=BacktestConfig("S", "1m", 0, 60000))
    kwargs[field] = value
    with pytest.raises(error):
        intents.DelegatedStrategyIntentHandler(**kwargs)


def test_dispatcher_requires_its_handler_and_known_scalar_surface():
    with pytest.raises(TypeError, match="handler must be"):
        intents.build_delegated_strategy_dispatcher(object())
    with pytest.raises(ValueError, match="value target is unsupported"):
        intents.build_delegated_strategy_dispatcher(handler(), strategy_values={"strategy.unknown": 1})


@pytest.mark.parametrize(
    "name,args,named",
    [
        ("strategy.entry", ("", "strategy.long"), {}),
        ("strategy.entry", ("L", None), {}),
        ("strategy.entry", ("L", "sideways"), {}),
        ("strategy.entry", ("L", "strategy.long"), {"qty": True}),
        ("strategy.entry", ("L", "strategy.long"), {"qty": "not-a-number"}),
        ("strategy.entry", ("L", "strategy.long"), {"comment": 7}),
        ("strategy.entry", ("L", "strategy.long"), {"disable_alert": 1}),
        ("strategy.close", ("L",), {"immediately": 1}),
        ("strategy.risk.allow_entry_in", ("long",), {}),
        ("strategy.exit", ("X", "L"), {"trail_points": 2, "trail_offset": -1}),
    ],
)
def test_malformed_strategy_values_cannot_become_committed_orders(name, args, named):
    h = handler()
    tx = transaction(h)
    with pytest.raises((ValueError, PineRuntimeError)):
        dispatch(tx, name, args, named)
        seal(h, tx)


@pytest.mark.parametrize("when", [None, "yes"])
def test_historical_when_na_suppresses_but_text_is_not_truthy(when):
    h = handler(5)
    tx = transaction(h, 5)
    if when is None:
        dispatch(tx, "strategy.cancel_all", named={"when": when})
        assert seal(h, tx) == ()
    else:
        with pytest.raises((ValueError, PineRuntimeError)):
            dispatch(tx, "strategy.cancel_all", named={"when": when})
            seal(h, tx)


def test_actual_callback_bar_must_be_in_admitted_dataset():
    h = intents.DelegatedStrategyIntentHandler(
        identity=handler().identity, producer_commit="c" * 40,
        bar_open_time_utc_ms={}, config=BacktestConfig("S", "1m", 0, 60000))
    tx = transaction(h)
    with pytest.raises((ValueError, PineRuntimeError)):
        dispatch(tx, "strategy.cancel_all")
        seal(h, tx)


@pytest.mark.parametrize(
    "version,args,named,schema,policy",
    [
        (6, ("X",), {"profit": 2}, "2.3.0", None),
        (6, ("X", "L"), {"profit": 2, "limit": 103}, "2.4.0", "first_trigger"),
        (5, ("X", "L"), {"trail_points": 2, "trail_offset": 1}, "2.5.0", "absolute_first"),
        (6, ("X", "L"), {"trail_price": 102, "trail_offset": 1}, "2.5.0", "first_trigger"),
    ],
)
def test_exit_variants_seal_the_correct_existing_wire_version(version, args, named, schema, policy):
    h = handler(version)
    tx = transaction(h, version)
    dispatch(tx, "strategy.exit", args, named)
    (event,) = seal(h, tx)
    assert event["schema_version"] == schema
    if policy is not None:
        assert event["price_pair_policy"] == policy
    else:
        assert event["exit_scope"] == "all_entries"


def test_explicit_empty_alert_and_disable_flag_survive_sealing():
    h = handler()
    tx = transaction(h)
    dispatch(tx, "strategy.entry", ("L", "strategy.long"), {"alert_message": "", "disable_alert": True})
    (event,) = seal(h, tx)
    assert event["alert_message"] == "" and event["disable_alert"] is True


@pytest.mark.parametrize("sequence", [True, -1])
def test_sequence_is_exact_and_nonnegative(sequence):
    with pytest.raises(ValueError, match="start_sequence"):
        handler().seal_committed([], start_sequence=sequence)


@pytest.mark.parametrize("draft", [None, {}, {"draft_schema_id": intents.DRAFT_SCHEMA_ID, "payload": 1}])
def test_malformed_drafts_are_rejected(draft):
    with pytest.raises(ValueError, match="draft is invalid"):
        handler().seal_committed([draft])


def test_draft_requires_a_structured_source_span():
    with pytest.raises(ValueError, match="source span is invalid"):
        handler().seal_committed([{"draft_schema_id": intents.DRAFT_SCHEMA_ID, "payload": {}}])


def test_corrupted_sealer_output_is_detected_by_independent_verification(monkeypatch):
    real_sealer = intents.seal_content_hash

    def corrupt(*args, **kwargs):
        result = real_sealer(*args, **kwargs)
        bad_hash = "sha256:" + "1" * 64
        assert result["content_hash"] != bad_hash
        result["content_hash"] = bad_hash
        return result

    monkeypatch.setattr(intents, "seal_content_hash", corrupt)
    h = handler()
    tx = transaction(h)
    dispatch(tx, "strategy.cancel_all")
    with pytest.raises(ValueError, match="content hash is invalid"):
        seal(h, tx)


@pytest.mark.parametrize("version", [True, 0, 7])
def test_signature_binding_never_guesses_version(version):
    with pytest.raises(ValueError, match="exact Pine version"):
        STRATEGY_COMMANDS["strategy.cancel_all"].bind([], {}, version)


@pytest.mark.parametrize("args,named", [(None, {}), ([], [])])
def test_signature_binding_checks_both_containers(args, named):
    with pytest.raises(ValueError, match="malformed"):
        STRATEGY_COMMANDS["strategy.cancel_all"].bind(args, named, 6)


def test_signature_rejects_extra_positional_and_pre_v5_leg_metadata():
    with pytest.raises(ValueError, match="too many positional"):
        STRATEGY_COMMANDS["strategy.cancel_all"].bind([1], {}, 6)
    with pytest.raises(ValueError, match="requires Pine v5 or v6"):
        STRATEGY_COMMANDS["strategy.exit"].bind(["X"], {"comment_profit": "p"}, 4)


def test_handler_rejects_wrong_argument_envelope_at_real_commit():
    from pinelib.events import SourceSpan

    h = handler()
    tx = transaction(h)
    spec = STRATEGY_COMMANDS["strategy.cancel_all"]
    with pytest.raises((ValueError, PineRuntimeError)):
        tx.dispatch_delegated(
            owner=intents.OWNER, schema_id=intents.DELEGATION_SCHEMA_ID,
            capability_id=spec.name, symbol_id=spec.symbol_id, overload_id=spec.overload_id,
            arguments={"wrong": []}, call_site_id="malformed-envelope",
            source_span=SourceSpan("sha256:" + "a" * 64, "test.pine", 1, 0, 1, 5))
        seal(h, tx)


def test_corrupt_decimal_formatter_cannot_silently_underflow_risk_limit(monkeypatch):
    # The external decimal formatter is fault-injected; all admission guards run.
    h = handler()
    real_formatter = intents.decimal_string

    def corrupt(number):
        if number == 1:
            return "0." + "0" * 400 + "1"
        return real_formatter(number)

    monkeypatch.setattr(intents, "decimal_string", corrupt)
    tx = transaction(h)
    with pytest.raises((ValueError, PineRuntimeError)):
        dispatch(tx, "strategy.risk.max_position_size", (1,))
        seal(h, tx)


def test_normalizer_losing_trailing_offset_between_checks_fails_closed(monkeypatch):
    # Simulate a faulty external NA normalizer, not a mocked validation verdict.
    h = handler()
    normal_is_na = intents.is_na
    reads = 0

    def unstable(value):
        nonlocal reads
        if type(value) is int and value == 3:
            reads += 1
            return reads > 1
        return normal_is_na(value)

    monkeypatch.setattr(intents, "is_na", unstable)
    tx = transaction(h)
    with pytest.raises((ValueError, PineRuntimeError)):
        dispatch(tx, "strategy.exit", ("X", "L"), {"trail_points": 2, "trail_offset": 3})
        seal(h, tx)
    assert reads >= 2
