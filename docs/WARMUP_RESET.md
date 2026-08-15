# Warmup reset

Warmup bars are the inclusive range up to `prehistory_end_index`.
The first score bar is exclusive of that warmup range.

## Policies

- `CALC_ONLY` updates calculation state only. The intent sink is discarded. The command buffer is empty after every warmup bar. Broker state is not created or mutated.
- `TRADE_THROUGH_UNSCORED` executes intents/fills. Warmup ledger is separate from the score ledger. Cash, position, and pending orders carry into score. Metrics baseline is the score equity baseline at the first score bar.
- `CALC_THEN_RESET_BROKER` may emit warmup intents for calculation, but they are discarded at the boundary. The command buffer is empty immediately before and after the boundary. Calculation vars/series are kept. Broker state is replaced by a whole new immutable/default `BrokerState`, not field-by-field wipe. Score starts with a clean broker event sequence. Warmup broker events stay in the audit ledger only.

## Boundary

- Warmup range is inclusive of the last prehistory bar.
- Score range is exclusive of warmup and inclusive of `score_end_time`.
- After `score_end_time` the phase is `AFTER`.
- Pending orders are reset on `CALC_THEN_RESET_BROKER`.
- Open position at score start is flat after reset, or carried in `TRADE_THROUGH_UNSCORED`.
- Score equity baseline is the captured opening broker equity.

## End policy

- `LEAVE_OPEN` keeps the open position after the last score bar.
- `FORCE_CLOSE` closes the open position on the last score bar.
- `MARK_TO_MARKET` values open position into score equity without forcing a close.
