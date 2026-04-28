import csv
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path


class JSONResultWriter:
    def write(self, result, path: str) -> None:
        Path(path).write_text(
            json.dumps(
                result.to_dict() if hasattr(result, "to_dict") else result,
                default=str,
                indent=2,
                sort_keys=True,
            )
        )


class CSVTradeWriter:
    def write(self, result, path: str) -> None:
        trades = result.closed_trades or []
        with open(path, "w", newline="") as f:
            if not trades:
                f.write("")
                return
            rows = [asdict(t) if is_dataclass(t) else t for t in trades]
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
