from pathlib import Path

from openpine_contracts import list_schema_ids

RC5_CONTRACTS_SHA = "6b5e67445e2772057cd877e158c7aa0c58bdfe37"
RC6_CONTRACTS_SHA = "5950e0f99214e60b64162d074ed47f1e4bbc7141"
RC6_MARKETDATA_SHA = "4e269bb7e3d389cae6214da9e17898c32c7ead15"
RC6_PINELIB_SHA = "547620ee70e707532dbabd6026645e4d9abaf748"


def test_contracts_pin_and_catalog() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert '"openpine-contracts==5.0.0rc6"' in text
    assert "git+" not in text
    assert "openpine.intent.v2" in list_schema_ids()
    assert f"ref: {RC6_CONTRACTS_SHA}" in workflow
    assert f"ref: {RC6_MARKETDATA_SHA}" in workflow
    assert f"ref: {RC6_PINELIB_SHA}" in workflow
    assert RC5_CONTRACTS_SHA not in workflow
