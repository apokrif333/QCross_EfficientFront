"""Adapter for the site's ordinary POST form and client-generated monthly CSV.

No REST endpoint, browser fingerprint, challenge solver or URL crawl is used.
The FNV checksum is the public form's integrity serialization (myLayout.js).
An access denial stops this route for the whole adapter instance.
"""

import csv
import hashlib
import io
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.config import Settings
from app.ingestion.lazyportfolio.client import SOURCE_PATH, USER_AGENT, FetchError
from app.ingestion.lazyportfolio.returns_models import ExtractedReturns, ReturnExtractionError
from app.ingestion.lazyportfolio.returns_parser import _parse_returns
from app.storage.snapshots import save_snapshot

logger = logging.getLogger(__name__)


class BacktesterRestricted(FetchError):
    pass


class PairNotAvailable(ReturnExtractionError):
    pass


def offered_pairs(html: str) -> dict[str, dict]:
    from app.ingestion.lazyportfolio.parser import parse_instruments

    discovery = parse_instruments(html)
    if discovery.diagnostics.errors:
        raise ReturnExtractionError(
            "Incomplete backtester selector: " + "; ".join(discovery.diagnostics.errors)
        )
    soup = BeautifulSoup(html, "lxml")
    select = soup.select_one("select#a1")
    if select is None:
        raise ReturnExtractionError("Backtester asset selector #a1 is missing")
    result = {}
    for option in select.select("option[value]"):
        symbol = option["value"]
        if not symbol:
            continue
        record = {
            "currencies": [c for c in option.get("data-currency", "").split(",") if c],
            "base_currency": option.get("data-basecurrency"),
            "attributes": dict(option.attrs),
        }
        if symbol in result and result[symbol] != record:
            raise ReturnExtractionError(f"Conflicting selector metadata for {symbol}")
        result[symbol] = record
    if len(result) < 51:
        raise ReturnExtractionError("Implausibly incomplete backtester asset selector")
    return result


def form_fields(html: str, symbol: str, currency: str) -> tuple[str, dict[str, str]]:
    offered = offered_pairs(html)
    if symbol not in offered or currency not in offered[symbol]["currencies"]:
        raise PairNotAvailable(f"{symbol}–{currency} is not offered by the backtester")
    # lxml drops Alpine's @submit attribute; HTMLParser preserves it.
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", attrs={"onsubmit": "return verificaSomma()"})
    if form is None or form.get("method", "").lower() != "post":
        raise ReturnExtractionError("Expected the normal backtester POST form")
    handler = form.get("@submit", "")
    if "buildSubmitState" not in handler or "security_computeAllFieldsChecksum" not in handler:
        raise ReturnExtractionError("Backtester submission protocol changed")
    fields = {}
    for el in form.select("[name]"):
        if el.has_attr("disabled") or el.get("type") in {"submit", "button"}:
            continue
        if el.get("type") in {"checkbox", "radio"} and not el.has_attr("checked"):
            continue
        if el.name == "select":
            selected = el.find("option", selected=True) or el.find("option")
            value = selected.get("value", "") if selected else ""
        else:
            value = el.get("value", "")
        fields[el["name"]] = value
    required = {"_wpnonce", "hp_ts", "hp_js", "aC", "yF", "mF", "yT", "mT", "iA", "cA"}
    if not required <= fields.keys():
        raise ReturnExtractionError("Backtester required form fields changed")
    # Use the earliest/latest date choices actually published by the current form.
    for field in ("yF", "mF", "yT", "mT"):
        el = form.find(attrs={"name": field})
        if field == "yF":
            fields[field] = el.get("min", fields[field])
        elif field == "yT":
            fields[field] = el.get("max", fields[field])
    state = {}
    for i in range(1, 5):
        state.update(
            {
                f"ft{i}": "NONE",
                f"fme{i}": "SMA",
                f"fmo{i}": "10",
                f"fsa{i}": "",
                f"fsc{i}": "ALL",
                f"fas{i}": "[]",
                f"sR{i}": "10",
                f"txA{i}": "",
            }
        )
        fields[f"n{i}"] = symbol if i == 1 else ""
        fields[f"sommaPercentuali{i}"] = "100" if i == 1 else "0"
    for i in range(1, 13):
        fields[f"a{i}"] = symbol if i == 1 else ""
        fields.pop(f"h{i}", None)  # No currency hedge or alternative asset.
        for j in range(1, 5):
            fields[f"p{i}_{j}"] = "100" if i == j == 1 else ""
    fields.update(
        {
            "aC": currency,
            "cI": currency,
            "iA": "1",
            "cA": "0",
            "hp_js": "1",
            "bktState": json.dumps(state, separators=(",", ":")),
        }
    )
    # The inflation country chooses the separate capitalNoInfl comparison;
    # extraction always uses nominal capitalBase/rendList, never capitalNoInfl.
    payload = (
        fields["_wpnonce"]
        + "|"
        + "&".join(
            f"{key}={value}" for key, value in sorted(fields.items()) if key != "sec_checksum"
        )
    )
    checksum = 0x811C9DC5
    for byte in payload.encode("utf-8"):
        checksum = ((checksum ^ byte) * 0x01000193) & 0xFFFFFFFF
    fields["sec_checksum"] = format(checksum, "x")
    return form.get("action", ""), fields


def monthly_csv(data: ExtractedReturns) -> str:
    """Exactly the downloadCsv() columns and units, before any display rounding."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(["Period", "Return"])
    writer.writerows(
        (o.date.strftime("%Y-%m"), o.source_metadata["original_percent"]) for o in data.observations
    )
    return "\ufeff" + buffer.getvalue()


class BacktesterAdapter:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.sleep = sleep
        self.restricted: str | None = None
        self.last_request: float | None = None
        self.catalog: dict[str, dict] | None = None
        self._session: httpx.Client | None = None
        self._form_response: httpx.Response | None = None

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
        self._form_response = None

    def _get_session(self) -> httpx.Client:
        if self._session is None:
            self._session = self._client()
        return self._session

    def _request(self, client: httpx.Client, method: str, url: str, **kwargs) -> httpx.Response:
        from app.ingestion.lazyportfolio.client import LazyPortfolioClient

        if self.restricted:
            raise BacktesterRestricted(self.restricted)
        policy = LazyPortfolioClient(self.settings)
        for attempt in range(self.settings.http_max_attempts):
            if self.last_request is not None:
                self.sleep(
                    max(
                        0,
                        self.settings.returns_request_interval
                        - (time.monotonic() - self.last_request),
                    )
                )
            try:
                response = client.request(method, url, **kwargs)
            except httpx.RequestError as exc:
                if attempt + 1 == self.settings.http_max_attempts:
                    raise FetchError(
                        f"Backtester network request failed: {type(exc).__name__}"
                    ) from exc
                self.sleep(policy._delay(attempt, None))
                continue
            finally:
                self.last_request = time.monotonic()
            logger.info(
                "lazyportfolio.backtester.response method=%s status=%d bytes=%d",
                method,
                response.status_code,
                len(response.content),
            )
            if response.status_code in {401, 403}:
                self.restricted = (
                    f"Backtester explicitly denied access: HTTP {response.status_code}"
                )
                raise BacktesterRestricted(self.restricted)
            if response.status_code == 200 and any(
                message in response.text.lower()
                for message in ("verify you are human", "access denied", "request blocked")
            ):
                self.restricted = "Source returned an access restriction page"
                raise BacktesterRestricted(self.restricted)
            if response.status_code == 429 or 500 <= response.status_code < 600:
                try:
                    delay = policy._delay(attempt, response.headers.get("Retry-After"))
                except FetchError as exc:
                    self.restricted = str(exc)
                    raise BacktesterRestricted(str(exc)) from exc
                if attempt + 1 == self.settings.http_max_attempts:
                    message = f"Backtester HTTP {response.status_code}; retry limit reached"
                    if response.status_code == 429:
                        self.restricted = message
                        raise BacktesterRestricted(message)
                    raise FetchError(message)
                self.sleep(delay)
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise FetchError(f"Backtester HTTP {response.status_code}") from exc
            if "text/html" not in response.headers.get("content-type", ""):
                raise ReturnExtractionError("Backtester response is not HTML")
            return response
        raise FetchError("Backtester request failed")

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            follow_redirects=True,
            timeout=httpx.Timeout(
                self.settings.http_read_timeout, connect=self.settings.http_connect_timeout
            ),
            transport=self.transport,
        )

    def discover_pairs(self) -> dict[str, dict]:
        client = self._get_session()
        url = self.settings.lazyportfolio_base_url.rstrip("/") + SOURCE_PATH
        page = self._request(client, "GET", url)
        self.catalog = offered_pairs(page.text)
        self._form_response = page
        return self.catalog

    def extract(
        self, symbol: str, currency: str, *, save_raw: bool | None = None
    ) -> ExtractedReturns:
        url = self.settings.lazyportfolio_base_url.rstrip("/") + SOURCE_PATH
        client = self._get_session()
        initial = self._form_response or self._request(client, "GET", url)
        self.catalog = offered_pairs(initial.text)
        action, fields = form_fields(initial.text, symbol, currency)
        target = urljoin(url, action)
        if urlsplit(target).netloc != urlsplit(url).netloc:
            raise ReturnExtractionError("Backtester form points outside source host")
        # The result page contains the next normal form, as in the browser's
        # edit-and-run workflow. Reuse its fresh fields and session, not stale tokens.
        self._form_response = None
        page = self._request(client, "POST", target, data=fields, headers={"Referer": url})
        if 'onsubmit="return verificaSomma()"' in page.text:
            self._form_response = page
        timestamp = datetime.now(UTC)
        # formatState() explicitly strips --<selected currency> from data-etf
        # and marks it etf-force-swap. The returned weights use that underlying
        # code. Preserve the catalog identifier and verify etfSwap in the parser.
        source_code = self.catalog[symbol]["attributes"].get("data-etf", symbol)
        forced_conversion = source_code == symbol and source_code.endswith("--" + currency)
        component_symbol = (
            source_code.removesuffix("--" + currency) if forced_conversion else symbol
        )
        data = _parse_returns(
            page.text,
            symbol=symbol,
            currency=currency,
            source_url=str(page.url),
            extracted_at=timestamp,
            backtester=True,
            component_symbol=component_symbol,
        )
        data.currency_kind = (
            "native"
            if self.catalog[symbol]["base_currency"] == currency and not forced_conversion
            else "converted"
        )
        data.conversion_methodology = (
            "Site reports exchange-rate conversion; no hedge selected. "
            "FX provider and exact fixing convention are not disclosed."
            if data.currency_kind == "converted"
            else None
        )
        data.snapshot_sha256 = hashlib.sha256(page.content).hexdigest()
        data.request_metadata = {
            "method": "POST",
            "path": SOURCE_PATH,
            "symbol": symbol,
            "source_component_symbol": component_symbol,
            "forced_currency_conversion": forced_conversion,
            "currency": currency,
            "period_from": f"{fields['yF']}-{fields['mF']}",
            "period_to": f"{fields['yT']}-{fields['mT']}",
            "initial_capital": "1",
            "contributions": "0",
            "withdrawals": "0",
            "weight": "100",
            "filters": "NONE",
            "taxes": False,
            "hedged": False,
            "return_array": "MAX.rendList",
            "capital_array": "MAX.capitalBase",
            "request_identifier": hashlib.sha256(
                f"{symbol}:{currency}:{fields['yF']}:{fields['mF']}:{fields['yT']}:{fields['mT']}".encode()
            ).hexdigest(),
        }
        data.snapshot_encoding = page.encoding
        if self.settings.returns_save_raw_snapshots if save_raw is None else save_raw:
            path = save_snapshot(
                self.settings.raw_snapshot_dir / "backtester",
                page.content,
                symbol=symbol,
                currency=currency,
                method="backtester",
                captured_at=timestamp,
            )
            data.snapshot_path = str(path)
        return data
