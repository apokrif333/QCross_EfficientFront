import pytest
from bs4 import BeautifulSoup

from app.config import Settings
from app.ingestion.base import CompletenessBaseline
from app.ingestion.lazyportfolio.parser import parse_instruments
from app.ingestion.lazyportfolio.validation import DiscoveryValidationError, validate_discovery
from tests.ingestion.lazyportfolio.helpers import remove_symbols


def test_valid_fixture_passes(source_html: str, settings: Settings) -> None:
    report = validate_discovery(parse_instruments(source_html), settings)
    assert report.passed and report.instrument_count == 263


def test_truncated_html_fails_even_after_lxml_repairs(source_html: str, settings: Settings) -> None:
    with pytest.raises(DiscoveryValidationError, match="Incomplete HTML"):
        validate_discovery(parse_instruments(source_html[: len(source_html) // 2]), settings)


def test_implausibly_low_count(source_html: str, settings: Settings) -> None:
    symbols = {i.source_symbol for i in parse_instruments(source_html).instruments}
    html = remove_symbols(source_html, symbols - {"VTI", "SPY", "TLT", "GLD"})
    with pytest.raises(DiscoveryValidationError, match="Implausibly low"):
        validate_discovery(parse_instruments(html), settings)


def test_missing_sentinel_fails(source_html: str, settings: Settings) -> None:
    with pytest.raises(DiscoveryValidationError, match="Missing sanity-check"):
        validate_discovery(parse_instruments(remove_symbols(source_html, {"VTI"})), settings)


def test_disagreeing_selectors_fail(source_html: str, settings: Settings) -> None:
    soup = BeautifulSoup(source_html, "lxml")
    soup.select_one('select.asset-dropdown option[value="QQQ"]').decompose()
    with pytest.raises(DiscoveryValidationError, match="selectors disagree"):
        validate_discovery(parse_instruments(str(soup)), settings)


def test_independent_panel_detects_omission_in_all_selectors(
    source_html: str, settings: Settings
) -> None:
    soup = BeautifulSoup(source_html, "lxml")
    for option in soup.select('select.asset-dropdown option[value="QQQ"]'):
        option.decompose()
    with pytest.raises(DiscoveryValidationError, match="Panel symbols missing"):
        validate_discovery(parse_instruments(str(soup)), settings)


def test_historical_count_drop_fails(source_html: str, settings: Settings) -> None:
    baseline = CompletenessBaseline(high_water_count=400)
    with pytest.raises(DiscoveryValidationError, match="high-water"):
        validate_discovery(parse_instruments(source_html), settings, baseline)


def test_category_drop_fails_even_if_total_drop_is_small(
    source_html: str, settings: Settings
) -> None:
    baseline = CompletenessBaseline(high_water_count=263, category_counts={"UK Fixed Income": 7})
    symbols = {
        i.source_symbol
        for i in parse_instruments(source_html).instruments
        if i.category == "UK Fixed Income"
    }
    with pytest.raises(DiscoveryValidationError, match="Category 'UK Fixed Income' dropped"):
        validate_discovery(
            parse_instruments(remove_symbols(source_html, symbols)), settings, baseline
        )


def test_conflict_fails(source_html: str, settings: Settings) -> None:
    soup = BeautifulSoup(source_html, "lxml")
    soup.select('select.asset-dropdown option[value="QQQ"]')[1].string = "Changed"
    with pytest.raises(DiscoveryValidationError, match="Conflicting duplicate"):
        validate_discovery(parse_instruments(str(soup)), settings)


def test_missing_option_value_fails(source_html: str, settings: Settings) -> None:
    soup = BeautifulSoup(source_html, "lxml")
    del soup.select_one('select.asset-dropdown option[value="QQQ"]')["value"]
    with pytest.raises(DiscoveryValidationError, match="Missing option identifiers"):
        validate_discovery(parse_instruments(str(soup)), settings)
