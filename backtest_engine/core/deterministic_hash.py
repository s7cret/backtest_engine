import hashlib, json
from dataclasses import asdict, is_dataclass
from typing import Any

def stable_json(obj: Any) -> str:
    def default(o: Any) -> Any:
        if is_dataclass(o): return asdict(o)
        if isinstance(o, set): return sorted(o)
        if hasattr(o, '__dict__'): return vars(o)
        return str(o)
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), default=default)
def sha256_obj(obj: Any) -> str: return hashlib.sha256(stable_json(obj).encode()).hexdigest()
