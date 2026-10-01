import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from scripts.sanitize_lazyportfolio_fixture import main


def test_sanitization_preserves_currency_metadata(
    source_html: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.html"
    source_path.write_text(source_html, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["sanitize_lazyportfolio_fixture.py", str(source_path)])
    main()
    fixture = tmp_path / "tests/fixtures/lazyportfolio_asset_universe.html"
    soup = BeautifulSoup(fixture.read_bytes(), "lxml")
    vti = soup.select_one('select.asset-dropdown option[value="VTI"]')
    assert vti["data-basecurrency"] == "USD"
    assert vti["data-currency"] == "USD,,,JPY,,AUD,CHF"
    vtv = soup.select_one('select.asset-dropdown option[value="VTV"]')
    assert vtv["data-basecurrency"] == "USD"
    assert "CAD" in vtv["data-currency"].split(",")
    metadata = json.loads(fixture.with_suffix(".metadata.json").read_text(encoding="utf-8"))
    assert metadata["instrument_currency_counts"] == {"USD": 74, "CAD": 49, "EUR": 72, "GBP": 68}
