from copy import deepcopy

import pytest
from bs4 import BeautifulSoup

from app.ingestion.lazyportfolio.parser import parse_instruments
from app.ingestion.lazyportfolio.service import normalize_instruments


def test_golden_fixture_complete(source_html: str) -> None:
    result = parse_instruments(source_html)
    assert len(result.instruments) == 263
    assert result.diagnostics.selector_option_counts == [263, 263]
    assert len({i.source_symbol for i in result.instruments}) == 263
    assert result.diagnostics.errors == []
    assert result.category_counts == {
        "US Stocks": 30,
        "Global / ex-US Stocks": 48,
        "US Theme - Sectors": 48,
        "US Factor - Dividends - Misc": 23,
        "US Fixed Income": 48,
        "EU Fixed Income": 12,
        "CA Fixed Income": 11,
        "UK Fixed Income": 7,
        "International Fixed Income": 15,
        "Commodity": 21,
    }
    assert result.diagnostics.panel_symbol_count == 261
    assert result.diagnostics.selector_only_symbols == ["BNDX--CAD", "EMB--CAD"]


@pytest.mark.parametrize("symbol", ["^BTC", "VUN.TO", "SXR8.DE", "CSP1.L", "BNDX--CAD", "EMB--CAD"])
def test_preserves_symbols_exactly(source_html: str, symbol: str) -> None:
    result = parse_instruments(source_html)
    item = next(i for i in result.instruments if i.source_symbol == symbol)
    assert item.source_symbol == symbol
    assert item.source_metadata["option_attributes"]["value"] == symbol


def test_extracts_normalized_name_category_and_provided_currency(source_html: str) -> None:
    result = parse_instruments(source_html)
    vti = next(i for i in result.instruments if i.source_symbol == "VTI")
    assert vti.name == "US Total Stock Market"
    assert vti.category == "US Stocks"
    assert vti.currency == "USD"
    assert vti.available_currencies == ["USD", "JPY", "AUD", "CHF"]
    normalized = normalize_instruments(result, "https://example.org/source")
    item = next(i for i in normalized if i.source_symbol == "VTI")
    assert item.currency == "USD"
    assert item.available_currencies == ["USD", "JPY", "AUD", "CHF"]
    assert item.exchange is None
    assert item.asset_type is None
    assert item.country is None
    assert item.raw_metadata == vti.source_metadata


def test_removes_identical_duplicates(source_html: str) -> None:
    soup = BeautifulSoup(source_html, "lxml")
    first = soup.select_one('select.asset-dropdown option[value="VTI"]')
    first.insert_after(deepcopy(first))
    result = parse_instruments(str(soup))
    assert len(result.instruments) == 263
    assert result.diagnostics.duplicate_records == 264
    assert result.diagnostics.conflicts == []


def test_conflicting_duplicate_not_silently_selected(source_html: str) -> None:
    soup = BeautifulSoup(source_html, "lxml")
    soup.select('select.asset-dropdown option[value="VTI"]')[1].string = "Different name"
    result = parse_instruments(str(soup))
    assert [c.source_symbol for c in result.diagnostics.conflicts] == ["VTI"]
    assert {i.name for i in result.diagnostics.conflicts[0].variants} == {
        "US Total Stock Market",
        "Different name",
    }
    assert "VTI" not in {i.source_symbol for i in result.instruments}
    assert result.diagnostics.errors


def test_missing_optional_metadata(source_html: str) -> None:
    soup = BeautifulSoup(source_html, "lxml")
    for option in soup.select('select.asset-dropdown option[value="VTI"]'):
        option.string = "   "
        del option["data-basecurrency"]
        del option["data-currency"]
    result = parse_instruments(str(soup))
    assert result.diagnostics.empty_names == ["VTI"]
    item = next(
        i for i in normalize_instruments(result, "https://example.org") if i.source_symbol == "VTI"
    )
    assert item.name is None and item.currency is None
    assert item.available_currencies is None
    assert result.diagnostics.missing_currencies == ["VTI"]
    assert result.diagnostics.missing_currency_availability == ["VTI"]


@pytest.mark.parametrize(
    "symbol,currency", [("VTI", "USD"), ("VUN.TO", "CAD"), ("XD9U.DE", "EUR"), ("XDUS.L", "GBP")]
)
def test_same_name_instruments_keep_distinct_currency(
    source_html: str,
    symbol: str,
    currency: str,
) -> None:
    result = parse_instruments(source_html)
    instrument = next(i for i in result.instruments if i.source_symbol == symbol)
    assert instrument.name == "US Total Stock Market"
    assert instrument.currency == currency
    assert instrument.available_currencies is not None
    assert currency in instrument.available_currencies


def test_currency_diagnostics(source_html: str) -> None:
    result = parse_instruments(source_html)
    assert result.currency_counts == {"USD": 74, "CAD": 49, "EUR": 72, "GBP": 68}
    assert result.available_currency_counts == {
        "USD": 74,
        "JPY": 70,
        "AUD": 70,
        "CHF": 70,
        "CAD": 79,
        "EUR": 72,
        "GBP": 68,
    }
    assert result.diagnostics.missing_currencies == []
    assert result.diagnostics.missing_currency_availability == []


def test_normalizes_currency_codes_without_changing_raw_metadata() -> None:
    result = parse_instruments(
        '<html><body><select class="asset-dropdown"><option value="AbC.x" '
        'data-basecurrency=" usd " data-currency=" USD,,, jpy ,USD, ">Name</option>'
        "</select></body></html>"
    )
    item = result.instruments[0]
    assert item.source_symbol == "AbC.x"
    assert item.currency == "USD"
    assert item.available_currencies == ["USD", "JPY"]
    assert item.source_metadata["option_attributes"]["data-currency"] == " USD,,, jpy ,USD, "


def test_invalid_currency_metadata_reported() -> None:
    result = parse_instruments(
        '<html><body><select class="asset-dropdown"><option value="ABC" '
        'data-basecurrency="US Dollars" data-currency="USD,invalid">Name</option>'
        "</select></body></html>"
    )
    assert result.instruments[0].currency is None
    assert any("Invalid currency code" in error for error in result.diagnostics.errors)


def test_missing_identifier_is_not_a_placeholder(source_html: str) -> None:
    soup = BeautifulSoup(source_html, "lxml")
    del soup.select_one('select.asset-dropdown option[value="VTI"]')["value"]
    result = parse_instruments(str(soup))
    assert result.diagnostics.missing_symbols == 1
    assert result.diagnostics.placeholder_options == 2


def test_missing_category_is_reported(source_html: str) -> None:
    soup = BeautifulSoup(source_html, "lxml")
    for group in soup.select('select.asset-dropdown optgroup[label="US Stocks"]'):
        del group["label"]
    result = parse_instruments(str(soup))
    assert len(result.diagnostics.unclassified_symbols) == 30
    assert any("Unclassified" in warning for warning in result.diagnostics.warnings)


def test_identifier_whitespace_and_case_are_not_changed() -> None:
    result = parse_instruments(
        '<html><body><select class="asset-dropdown">'
        '<option value="  AbC.x  "> A   name </option>'
        "</select></body></html>"
    )
    assert result.instruments[0].source_symbol == "  AbC.x  "
    assert result.instruments[0].name == "A name"
