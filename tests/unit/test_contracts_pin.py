from pathlib import Path

from openpine_contracts import list_schema_ids

def test_contracts_pin_and_catalog() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert '"openpine-contracts==5.0.0rc4"' in text
    assert "git+" not in text
    assert "openpine.intent.v2" in list_schema_ids()
    for commit in (
        "a91c0ce0d36d60e8dc5cb43e7aa92ab59c2eaa6c",
        "e098947dfd30444273090e521e5c749673909c37",
        "9fcbac5fa3d81d3b1c966693c0d3614a558c3637",
    ):
        assert f"ref: {commit}" in workflow
