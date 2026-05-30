from __future__ import annotations

from backtest_engine.models import Bar, InstrumentModel, Trade


def trade_excursion_values(
    trade: Trade,
    bar: Bar,
    instrument: InstrumentModel,
) -> tuple[float, float, float, float]:
    """Return updated MFE/MAE and TradingView-style runup/drawdown for a trade."""

    if trade.direction == "long":
        favorable = instrument.pnl(trade.entry_price, bar.high, trade.qty, trade.direction)
        adverse = instrument.pnl(trade.entry_price, bar.low, trade.qty, trade.direction)
    else:
        favorable = instrument.pnl(trade.entry_price, bar.low, trade.qty, trade.direction)
        adverse = instrument.pnl(trade.entry_price, bar.high, trade.qty, trade.direction)

    mfe = favorable if trade.mfe is None else max(trade.mfe, favorable)
    mae = adverse if trade.mae is None else min(trade.mae, adverse)
    return mfe, mae, max(0.0, mfe), max(0.0, -mae)
