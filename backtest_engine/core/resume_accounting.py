"""Pure admission of the native durable fill/trade ledger and accounting values."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, fields
import math
from typing import Any, Literal, NoReturn

from backtest_engine.broker.commission import calculate_commission
from backtest_engine.broker.rounding import round_to_step
from backtest_engine.config import BacktestConfig
from backtest_engine.core.state_snapshot import BrokerSnapshot
from backtest_engine.errors import ResumeUnsupportedError
from backtest_engine.models import EquityPoint, InstrumentModel

ExitKey = tuple[str, int, int, float]


def _fail(message: str) -> NoReturn:
    raise ResumeUnsupportedError("typed JSON resume accounting: " + message)


def _same(actual: float, expected: float, label: str, *, tolerance: float = 1e-12) -> None:
    if not math.isfinite(expected) or not math.isclose(
        actual, expected, rel_tol=1e-12, abs_tol=tolerance
    ):
        _fail(label + " does not match the native ledger")


def validate_broker_ledger(broker: BrokerSnapshot, *, qty_epsilon: float) -> None:
    """Terminal orders may be omitted; every fill still needs a durable trade link.

    Opening lots retain entry_fill_index after partial closes and order pruning.
    Closing allocations retain exit order/bar/time/price. Group identical closing
    identities because repeated native fills can legally share that identity.
    """
    entries = [0.0] * len(broker.fills)
    originals: dict[int, float] = {}
    exits: dict[ExitKey, float] = defaultdict(float)
    for trade in (*broker.open_trades, *broker.closed_trades):
        index = trade.entry_fill_index
        if index is None or trade.entry_qty is None:
            _fail("trade is missing its opening fill identity/quantity")
        if trade.commission_entry < 0 or trade.commission_exit < 0:
            _fail("negative allocated trade commission")
        entries[index] += trade.qty
        if index in originals:
            _same(trade.entry_qty, originals[index], "opening lot entry_qty")
        originals[index] = trade.entry_qty
        if not trade.is_open:
            if (
                trade.exit_id is None
                or trade.exit_bar_index is None
                or trade.exit_time is None
                or trade.exit_price is None
                or trade.exit_bar_index < trade.entry_bar_index
            ):
                _fail("closed trade is missing a valid closing fill identity")
            key = (trade.exit_id, trade.exit_bar_index, trade.exit_time, trade.exit_price)
            exits[key] += trade.qty
    for index, quantity in enumerate(entries):
        if index in originals:
            _same(
                quantity, originals[index], "opening lot quantity allocation", tolerance=qty_epsilon
            )

    closing_capacity: dict[ExitKey, float] = defaultdict(float)
    signed_position = 0.0
    direction: Literal["long", "short", "flat"] = "flat"
    for index, fill in enumerate(broker.fills):
        if fill.position_direction_before != direction:
            _fail("fill position direction chain is broken")
        sign = 1 if fill.side == "buy" else -1
        signed_position += sign * fill.qty
        if fill.position_direction_after == "flat":
            _same(signed_position, 0.0, "flat fill quantity", tolerance=qty_epsilon)
            signed_position = 0.0  # Native accounting discards only its configured dust.
        elif (fill.position_direction_after == "long" and signed_position <= 0) or (
            fill.position_direction_after == "short" and signed_position >= 0
        ):
            _fail("fill position direction differs from signed quantity")
        direction = fill.position_direction_after
        key = (fill.order_id, fill.bar_index, fill.time, fill.price)
        opening = entries[index]
        closing = max(0.0, fill.qty - opening)
        if opening > fill.qty + qty_epsilon:
            _fail("trade opening allocations exceed their fill")
        if opening:
            expected_direction = "long" if sign > 0 else "short"
            if fill.direction != expected_direction:
                _fail("opening fill side/direction mismatch")
        if not opening and key not in exits:
            _fail("fill lacks a durable opening/closing trade link")
        closing_capacity[key] += closing
    if set(exits) - set(closing_capacity):
        _fail("closed trade refers to an unknown closing fill")
    for key, quantity in closing_capacity.items():
        _same(
            exits.get(key, 0.0), quantity, "closing fill quantity allocation", tolerance=qty_epsilon
        )
    _same(broker.position.size, signed_position, "position signed fill quantity")
    if broker.position.direction != direction:
        _fail("position direction differs from the final fill")
    lots = broker.open_trades
    if any(trade.direction != broker.position.direction for trade in lots):
        _fail("open trade direction differs from position")
    quantity = sum(trade.qty for trade in lots)
    _same(
        abs(broker.position.size), quantity, "position open trade quantity", tolerance=qty_epsilon
    )
    average = sum(trade.entry_price * trade.qty for trade in lots) / quantity if quantity else 0.0
    _same(broker.position.avg_price, average, "position average price")
    _same(broker.equity, broker.cash + broker.position.open_profit, "broker equity")


def validate_broker_values(
    broker: BrokerSnapshot,
    config: BacktestConfig | AccountingInputs,
    *,
    mark_price: float | None,
    mark_tick: float | None,
    equity_points: list[EquityPoint],
    point_prices: dict[int, float] | None = None,
) -> None:
    """Use native instrument, commission and mark-rounding owners, not a new oracle."""
    instrument = config.instrument_model or InstrumentModel()
    commissions = 0.0
    entry_fees = [0.0] * len(broker.fills)
    entry_quantities = [0.0] * len(broker.fills)
    original_quantities = [0.0] * len(broker.fills)
    exit_fees: dict[ExitKey, float] = defaultdict(float)
    expected_exit_fees: dict[ExitKey, float] = defaultdict(float)
    for trade in (*broker.open_trades, *broker.closed_trades):
        assert trade.entry_fill_index is not None and trade.entry_qty is not None
        index = trade.entry_fill_index
        entry_fees[index] += trade.commission_entry
        entry_quantities[index] += trade.qty
        original_quantities[index] = trade.entry_qty
        if not trade.is_open:
            assert trade.exit_id is not None and trade.exit_bar_index is not None
            assert trade.exit_time is not None and trade.exit_price is not None
            exit_fees[(trade.exit_id, trade.exit_bar_index, trade.exit_time, trade.exit_price)] += (
                trade.commission_exit
            )
    for index, fill in enumerate(broker.fills):
        expected = calculate_commission(
            fill.price, fill.qty, config.commission_type, config.commission_value
        )
        _same(fill.commission, expected, "fill commission", tolerance=1e-9)
        commissions += fill.commission
        _same(
            entry_fees[index],
            fill.commission * entry_quantities[index] / fill.qty,
            "opening commission allocation",
            tolerance=1e-9,
        )
        key = (fill.order_id, fill.bar_index, fill.time, fill.price)
        expected_exit_fees[key] += (
            fill.commission * max(0.0, fill.qty - original_quantities[index]) / fill.qty
        )
    for key, expected in expected_exit_fees.items():
        _same(exit_fees.get(key, 0.0), expected, "closing commission allocation", tolerance=1e-9)
    gross = 0.0
    for trade in broker.closed_trades:
        assert trade.exit_price is not None
        profit = instrument.pnl(trade.entry_price, trade.exit_price, trade.qty, trade.direction)
        gross += profit
        _same(
            trade.profit,
            profit - trade.commission_entry - trade.commission_exit,
            "closed trade profit",
            tolerance=1e-9,
        )
    _same(broker.position.realized_profit, gross - commissions, "realized profit", tolerance=1e-9)
    _same(
        broker.cash,
        config.initial_capital + broker.position.realized_profit,
        "cash",
        tolerance=1e-9,
    )
    if mark_price is not None:
        price = round_to_step(mark_price, mark_tick, "nearest") if mark_tick else mark_price
        expected_open = (
            0.0
            if broker.position.direction == "flat"
            else instrument.pnl(
                broker.position.avg_price,
                price,
                abs(broker.position.size),
                broker.position.direction,
            )
        )
        _same(broker.position.open_profit, expected_open, "open profit", tolerance=1e-9)
    for point in equity_points:
        _same(
            point.cash,
            config.initial_capital + point.realized_profit,
            "equity history cash",
            tolerance=1e-9,
        )
        _same(point.equity, point.cash + point.open_profit, "equity history equity", tolerance=1e-9)
        if point.position_size == 0:
            if point.position_avg_price is not None:
                _fail("flat equity history contains an average entry price")
            _same(point.open_profit, 0.0, "flat equity history open profit", tolerance=1e-9)
        elif point.position_avg_price is None:
            _fail("non-flat equity history is missing an average entry price")
        elif point_prices is not None and point.bar_index in point_prices:
            price = point_prices[point.bar_index]
            price = round_to_step(price, mark_tick, "nearest") if mark_tick else price
            direction: Literal["long", "short"] = "long" if point.position_size > 0 else "short"
            _same(
                point.open_profit,
                instrument.pnl(
                    point.position_avg_price, price, abs(point.position_size), direction
                ),
                "equity history open profit",
                tolerance=1e-9,
            )


@dataclass(frozen=True)
class AccountingInputs:
    initial_capital: float
    commission_type: str
    commission_value: float
    instrument_model: InstrumentModel
    mintick: float | None
    qty_epsilon: float
    mark_price: float | None


def native_accounting_inputs(
    config: BacktestConfig, mark_tick: float | None, mark_price: float | None
) -> dict[str, Any]:
    from backtest_engine.core.position_accounting import quantity_epsilon

    return {
        "schema_id": "engine-accounting-v1",
        "initial_capital": config.initial_capital,
        "commission_type": config.commission_type,
        "commission_value": config.commission_value,
        "instrument_model": asdict(config.instrument_model or InstrumentModel()),
        "mintick": mark_tick,
        "qty_epsilon": quantity_epsilon(config),
        "mark_price": mark_price,
    }


def read_accounting_inputs(value: Any) -> AccountingInputs:
    names = {field.name for field in fields(AccountingInputs)}
    if (
        type(value) is not dict
        or set(value) != names | {"schema_id"}
        or value["schema_id"] != "engine-accounting-v1"
    ):
        _fail("unknown or incomplete accounting context")
    for name in ("initial_capital", "commission_value", "mintick", "qty_epsilon", "mark_price"):
        number = value[name]
        if number is None and name in ("mintick", "mark_price"):
            continue
        try:
            valid = type(number) in (int, float) and math.isfinite(number)
        except OverflowError:
            valid = False
        if not valid:
            _fail("invalid accounting context numeric field")
    if (
        value["initial_capital"] < 0
        or value["commission_value"] < 0
        or value["qty_epsilon"] < 1e-12
        or (value["mintick"] is not None and value["mintick"] <= 0)
        or value["commission_type"]
        not in ("none", "percent", "fixed_per_order", "fixed_per_contract")
    ):
        _fail("accounting context is outside its native domain")
    model = value["instrument_model"]
    if type(model) is not dict or set(model) != {field.name for field in fields(InstrumentModel)}:
        _fail("invalid accounting instrument fields")
    try:
        valid_size = type(model["contract_size"]) in (int, float) and math.isfinite(
            model["contract_size"]
        )
    except OverflowError:
        valid_size = False
    if (
        not valid_size
        or model["mode"] not in ("spot", "linear_futures", "inverse_futures")
        or type(model["quote_currency"]) is not str
        or type(model["settlement_currency"]) is not str
        or (model["base_currency"] is not None and type(model["base_currency"]) is not str)
    ):
        _fail("invalid accounting instrument scalar fields")
    return AccountingInputs(
        **{**{key: value[key] for key in names}, "instrument_model": InstrumentModel(**model)}
    )
