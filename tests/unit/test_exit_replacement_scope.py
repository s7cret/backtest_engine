"""Named exit sizing and replacement do not keep stale larger reservations."""

from tests.unit.test_deferred_market_exits import candles, run


def test_exit_percent_uses_only_matching_entry_quantity():
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", "long", qty=2)
            ctx.entry("B", "long", qty=8)
        if i == 1:
            ctx.exit("X", "A", limit=105, qty_percent=50)

    _, result = run(
        commands,
        candles((100, 101, 99, 100), (100, 101, 99, 100), (100, 106, 99, 100)),
        pyramiding=2,
    )
    assert [(t.entry_id, t.qty) for t in result.closed_trades] == [("A", 1)]
    assert [(t.entry_id, t.qty) for t in result.open_trades] == [("A", 1), ("B", 8)]


def test_reducing_exit_quantity_releases_reservation_for_second_exit():
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", "long", qty=10)
        if i == 1:
            ctx.exit("X", "A", limit=110, qty=8)
        if i == 2:
            ctx.exit("X", "A", limit=110, qty=3)
            ctx.exit("Y", "A", limit=105, qty=7)

    engine, result = run(
        commands,
        candles(
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (100, 106, 99, 100),
            (100, 111, 99, 100),
        ),
    )
    assert result.status == "completed", result.errors
    assert [(t.exit_id, t.qty, t.exit_price) for t in result.closed_trades] == [
        ("Y:L", 7, 105),
        ("X:L", 3, 110),
    ]
    assert not result.open_trades


def test_reservations_sum_repeated_entry_ids_instead_of_only_last_trade():
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", "long", qty=2)
        if i == 1:
            ctx.entry("A", "long", qty=3)
        if i == 2:
            ctx.exit("X", "A", qty=4, limit=110)
            ctx.exit("Y", "A", qty=4, limit=105)

    engine, result = run(
        commands,
        candles(
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (100, 106, 99, 100),
            (100, 111, 99, 100),
        ),
        pyramiding=2,
    )
    assert result.status == "completed", result.errors
    fills = [(f.order_id, f.qty) for f in engine.fills if f.order_id.startswith(("X:", "Y:"))]
    assert fills == [("Y:L", 1), ("X:L", 4)]
    assert sum(t.qty for t in result.closed_trades) == 5 and not result.open_trades
