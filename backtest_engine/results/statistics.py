def summarize(profits: list[float], initial: float, final: float) -> dict:
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]
    gp = sum(wins)
    gl = sum(losses)
    gross_loss = abs(gl)
    total = len(profits)
    return {
        "net_profit": final - initial,
        "net_profit_percent": ((final - initial) / initial * 100 if initial else 0.0),
        "gross_profit": gp,
        "gross_loss": gross_loss,
        "profit_factor": (gp / gross_loss if gross_loss else (float("inf") if gp else 0.0)),
        "total_trades": total,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": (len(wins) / total * 100 if total else 0.0),
        "avg_win": (gp / len(wins) if wins else 0.0),
        "avg_loss": (gl / len(losses) if losses else 0.0),
        "avg_trade": (sum(profits) / total if total else 0.0),
        "expectancy": (sum(profits) / total if total else 0.0),
    }
