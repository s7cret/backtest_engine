from pathlib import Path

from openpine_contracts import list_schema_ids

def test_contracts_pin_and_catalog() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert '"openpine-contracts==5.0.0rc4"' in text
    assert "git+" not in text
    assert "openpine.intent.v2" in list_schema_ids()
    for commit in (
        "bb1a56181e37c6f0ff7a60366d9a550103fcb8df",
        "e098947dfd30444273090e521e5c749673909c37",
        "e5c69acaca70613734985f84a9ef9d28c1a12b79",
    ):
        assert f"ref: {commit}" in workflow
