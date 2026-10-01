import logging
import re

from bs4 import BeautifulSoup, Tag

from app.ingestion.lazyportfolio.models import (
    DuplicateConflict,
    LazyPortfolioInstrument,
    ParseDiagnostics,
    ParseResult,
)

logger = logging.getLogger(__name__)


def _attributes(tag: Tag) -> dict[str, str | list[str]]:
    return {key: value for key, value in tag.attrs.items() if value is not None}


def _currency_code(value: str, symbol: str, diagnostics: ParseDiagnostics) -> str | None:
    code = value.strip().upper()
    if not code:
        return None
    if not re.fullmatch(r"[A-Z]{3}", code):
        message = f"Invalid currency code {value!r} for {symbol!r}"
        if message not in diagnostics.errors:
            diagnostics.errors.append(message)
        return None
    return code


def _available_currencies(
    option: Tag, symbol: str, diagnostics: ParseDiagnostics
) -> list[str] | None:
    value = option.get("data-currency")
    if not isinstance(value, str):
        return None
    # Empty slots in strings such as 'USD,,,JPY,,AUD,CHF' are intentional.
    codes = [_currency_code(token, symbol, diagnostics) for token in value.split(",")]
    return list(dict.fromkeys(code for code in codes if code))


def parse_instruments(html: str) -> ParseResult:
    """Read every server-rendered selector, retaining source identifiers verbatim.

    There is deliberately no guessed fallback when source markup changes. A parser
    result includes evidence for validation; extraction alone never means success.
    """
    soup = BeautifulSoup(html, "lxml")
    diagnostics = ParseDiagnostics()
    selectors = soup.select("select.asset-dropdown")
    diagnostics.selector_count = len(selectors)
    if not selectors:
        diagnostics.errors.append("No select.asset-dropdown asset selectors found")
    if not re.search(r"</body\s*>\s*(?:<!--.*?-->\s*)*</html\s*>\s*$", html, re.I | re.S):
        diagnostics.errors.append("Incomplete HTML document: missing closing body/html")
    # lxml repairs broken HTML. Check original closing tags before trusting repaired trees.
    for tag_name in ("select", "optgroup", "option"):
        opened = len(re.findall(rf"<{tag_name}\b[^>]*>", html, re.I))
        closed = len(re.findall(rf"</{tag_name}\s*>", html, re.I))
        if opened != closed:
            diagnostics.errors.append(
                f"Unbalanced {tag_name} tags: {opened} opening, {closed} closing"
            )

    variants: dict[str, list[LazyPortfolioInstrument]] = {}
    selector_universes: list[set[str]] = []
    for selector in selectors:
        symbols: set[str] = set()
        option_count = 0
        for option in selector.select("option"):
            group = option.find_parent("optgroup")
            symbol = option.get("value")
            if symbol == "" and group is None and not option.has_attr("data-etf"):
                diagnostics.placeholder_options += 1
                continue
            diagnostics.raw_instrument_records += 1
            option_count += 1
            if not isinstance(symbol, str) or not symbol.strip():
                diagnostics.missing_symbols += 1
                continue
            # Do not strip, case-fold, split, or otherwise rewrite an identifier.
            if symbol != symbol.strip():
                diagnostics.warnings.append(f"Identifier contains boundary whitespace: {symbol!r}")
            data_symbol = option.get("data-etf")
            if data_symbol is not None and data_symbol != symbol:
                diagnostics.errors.append(f"value/data-etf mismatch for {symbol!r}")
            category = group.get("label") if isinstance(group, Tag) else None
            base_currency = option.get("data-basecurrency")
            item = LazyPortfolioInstrument(
                source_symbol=symbol,
                name=option.get_text(" ", strip=True),
                category=category,
                currency=_currency_code(base_currency, symbol, diagnostics)
                if isinstance(base_currency, str)
                else None,
                available_currencies=_available_currencies(option, symbol, diagnostics),
                source_metadata={
                    "option_attributes": _attributes(option),
                    "group_attributes": _attributes(group) if isinstance(group, Tag) else {},
                },
            )
            symbols.add(symbol)
            previous = variants.setdefault(symbol, [])
            if item in previous:
                diagnostics.duplicate_records += 1
            else:
                previous.append(item)
        diagnostics.selector_option_counts.append(option_count)
        selector_universes.append(symbols)
    if selector_universes and any(s != selector_universes[0] for s in selector_universes):
        diagnostics.errors.append("Asset selectors disagree on their symbol universes")

    instruments: list[LazyPortfolioInstrument] = []
    for symbol, items in variants.items():
        if len(items) > 1:
            diagnostics.conflicts.append(DuplicateConflict(source_symbol=symbol, variants=items))
        else:
            instruments.append(items[0])
    if diagnostics.conflicts:
        diagnostics.errors.append("Conflicting duplicate symbols; no variant selected")
        logger.warning(
            "lazyportfolio.parse.conflicts symbols=%s",
            [conflict.source_symbol for conflict in diagnostics.conflicts],
        )

    # This panel is an independent, redundant server-rendered representation.
    # It can be a subset of the dropdowns, but must never contain a missing asset.
    panel_entries = soup.select(".asset-block .asset-li-list")
    panel_symbols: set[str] = set()
    for entry in panel_entries:
        badge = entry.select_one(".mini-badge")
        if badge is None or not badge.get_text(strip=True):
            diagnostics.errors.append("Available-assets panel contains a missing symbol badge")
            continue
        symbol = badge.get_text(strip=True)
        panel_symbols.add(symbol)
        block = entry.find_parent(class_="asset-block")
        heading = block.select_one("p b") if isinstance(block, Tag) else None
        category = " ".join(heading.get_text().split()) if heading else None
        if symbol in variants and category != variants[symbol][0].category:
            diagnostics.errors.append(f"Panel/selector category mismatch for {symbol!r}")
    if not panel_entries:
        diagnostics.errors.append(
            "Available-assets panel is absent; cannot cross-check completeness"
        )
    diagnostics.panel_symbol_count = len(panel_symbols)
    diagnostics.panel_missing_from_selector = sorted(panel_symbols - variants.keys())
    diagnostics.selector_only_symbols = sorted(variants.keys() - panel_symbols)
    if diagnostics.panel_missing_from_selector:
        diagnostics.errors.append(
            f"Panel symbols missing from selector: {diagnostics.panel_missing_from_selector}"
        )
    if diagnostics.selector_only_symbols:
        diagnostics.warnings.append(
            f"Selector-only symbols absent from available-assets panel: "
            f"{diagnostics.selector_only_symbols}"
        )
    diagnostics.empty_names = [i.source_symbol for i in instruments if not i.name]
    diagnostics.unclassified_symbols = [i.source_symbol for i in instruments if not i.category]
    diagnostics.missing_currencies = [i.source_symbol for i in instruments if not i.currency]
    diagnostics.missing_currency_availability = [
        i.source_symbol for i in instruments if i.available_currencies is None
    ]
    if diagnostics.empty_names:
        diagnostics.warnings.append(f"Empty names: {diagnostics.empty_names}")
    if diagnostics.unclassified_symbols:
        diagnostics.warnings.append(f"Unclassified symbols: {diagnostics.unclassified_symbols}")
    if diagnostics.missing_currencies:
        diagnostics.warnings.append(
            f"Missing instrument currencies: {diagnostics.missing_currencies}"
        )
    if diagnostics.missing_currency_availability:
        diagnostics.warnings.append(
            f"Missing currency availability: {diagnostics.missing_currency_availability}"
        )
    logger.info(
        "lazyportfolio.parse.completed instruments=%d selectors=%d duplicates=%d warnings=%d",
        len(instruments),
        len(selectors),
        diagnostics.duplicate_records,
        len(diagnostics.warnings),
    )
    return ParseResult(instruments=instruments, diagnostics=diagnostics)
