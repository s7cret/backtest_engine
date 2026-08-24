from pathlib import Path

from openpine_contracts import list_schema_ids

def test_contracts_pin_and_catalog() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert '"openpine-contracts==5.0.0rc4"' in text
    assert "git+" not in text
    assert "openpine.intent.v2" in list_schema_ids()
    for commit in (
        "9362f0abe1c4b0924f5f348141826e005cd880ba",
        "e098947dfd30444273090e521e5c749673909c37",
        "a0ea8e7177e73217c595f978c76ad34bb47d6bec",
    ):
        assert f"ref: {commit}" in workflow
