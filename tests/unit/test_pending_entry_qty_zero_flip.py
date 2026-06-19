"""RED-тест для bug: pending entry order отменяется когда strategy повторно
выдаёт strategy.entry с тем же id и qty=0 на следующем баре.

TV semantic с process_orders_on_close=False:
  - bar T: strategy.entry("X", qty=lot) -> order created, active_from=T+1
  - bar T+1: strategy.entry("X", qty=0) -> pending order ДОЛЖЕН fill на T+1,
    НЕ отменяться (cancel only on explicit strategy.cancel)

Это regression для SOL 1D TV Parity run: 34 missing entry points,
all entries на 2023-09-14, где сигнал allowShort=1 на bar T и
strategy.entry("Short", allowShort ? lot : 0) выдаёт qty=0 на T+1
потому что allowShort flip-flop'ит 1->0->1->0.
"""
from backtest_engine import BacktestConfig, BacktestEngine, Bar


def cfg(**kw):
    d = dict(
        symbol="S",
        timeframe="1D",
        start_time=1,
        end_time=5,
        commission_type="none",
        initial_capital=1000,
    )
    d.update(kw)
    return BacktestConfig(**d)


class FlipFlopShortEntry:
    """Симулирует Pine-стратегию с conditional qty:
    strategy.entry("Short", strategy.short, allowShort ? lot : 0, stop=dn)

    bar 0: allowShort=1 -> strategy.entry("Short", qty=10, stop=9.0)
            -> pending order создан, active_from=1
    bar 1: allowShort=0 -> strategy.entry("Short", qty=0)
            -> pending order ДОЛЖЕН fill (открыт с bar 0).
               Pine НЕ отменяет pending order при qty=0 на следующем баре.
               Cancel only via explicit strategy.cancel.
    bar 2: allowShort=1 -> strategy.entry("Short", qty=10, stop=9.0) again
    """

    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("S", "short", qty=10)
        elif bar_index == 1:
            # Pine conditional qty: when allowShort=0, qty=0,
            # but the existing pending order from bar 0 must NOT be cancelled.
            self.ctx.entry("S", "short", qty=0)
        elif bar_index == 2:
            self.ctx.entry("S", "short", qty=10)


def test_pending_entry_not_cancelled_by_subsequent_qty_zero_repeat():
    """Pending market entry order от bar 0 должен fill на bar 1,
    даже если strategy повторно выдаёт strategy.entry с qty=0 на bar 1."""
    bars = [
        Bar(1, 10.0, 10.0, 10.0, 10.0),  # bar 0: signal short, order created
        Bar(2, 10.0, 10.0, 10.0, 10.0),  # bar 1: signal says 0 qty, but order must fill
        Bar(3, 10.0, 10.0, 10.0, 10.0),  # bar 2: signal again qty=10
    ]
    r = BacktestEngine(
        cfg(
            end_time=3,
            process_orders_on_close=False,
            pyramiding=0,
        )
    ).run(FlipFlopShortEntry, bars=bars)
    # Order from bar 0 must have filled at bar 1 (open of next bar).
    # After fill, position is short with qty=10, open_trades should contain
    # the trade from the bar-0 signal.
    ev_codes = [getattr(e, 'code', '?') for e in (r.events or [])]
    assert len(r.open_trades) == 1, (
        f"expected 1 open trade from bar-0 signal to fill at bar 1, "
        f"got {len(r.open_trades)} open_trades. events: {ev_codes}"
    )
    assert r.open_trades[0].qty == 10
    assert r.open_trades[0].entry_price == 10.0  # open of bar 1
