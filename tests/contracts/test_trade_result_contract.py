from backtest_engine.models import TradeResult


def test_trade_result_contract_labels_score_boundary_trades():
    trade = TradeResult(
        entry_time=1_000,
        exit_time=2_000,
        direction="long",
        entry_price=100.0,
        exit_price=110.0,
        qty=1.0,
        profit=10.0,
        entry_phase="prehistory",
        exit_phase="score",
        crosses_score_boundary=True,
    )

    assert trade.entry_phase == "prehistory"
    assert trade.exit_phase == "score"
    assert trade.crosses_score_boundary is True
