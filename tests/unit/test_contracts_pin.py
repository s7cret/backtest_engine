from pathlib import Path

from openpine_contracts import list_schema_ids

RC5_CONTRACTS_SHA = "6b5e67445e2772057cd877e158c7aa0c58bdfe37"
RC6_CONTRACTS_SHA = "904e8f660834a10d3382cd1b2ed7380c24b73072"
RC6_MARKETDATA_SHA = "2fdbbcb3fa2b5e35fc98938c9b7c260c2b36935b"
RC6_PINELIB_SHA = "456ddff14cbf7309c5db9cadbdb798c2a7a9951c"


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
