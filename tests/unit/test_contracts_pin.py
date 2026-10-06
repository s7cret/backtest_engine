from pathlib import Path

from openpine_contracts import list_schema_ids

RC5_CONTRACTS_SHA = "6b5e67445e2772057cd877e158c7aa0c58bdfe37"
RC6_CONTRACTS_SHA = "db1745756516b47466756c9c5d38fbbb95595a3b"
RC6_MARKETDATA_SHA = "6ad408144ffcacfc3d0b3b4ac4d635949f6602e6"
RC6_PINELIB_SHA = "f66c67f90a7f3428fe68267d41de9ae31202f775"


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
