from pathlib import Path

from openpine_contracts import list_schema_ids

def test_contracts_pin_and_catalog() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert '"openpine-contracts==5.0.0rc6"' in text
    assert "git+" not in text
    assert "openpine.intent.v2" in list_schema_ids()
    for commit in (
        "6b5e67445e2772057cd877e158c7aa0c58bdfe37",
        "de1cb68be33a9b51ee6ddf2513932c90af721874",
        "1204d02ed5cea1a3be8b5e8252bc4d881c539a25",
    ):
        assert f"ref: {commit}" in workflow
