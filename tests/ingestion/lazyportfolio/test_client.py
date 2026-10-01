from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from app.config import Settings
from app.ingestion.lazyportfolio.client import FetchError, LazyPortfolioClient


def test_retries_429_5xx_with_backoff_and_retry_after(settings: Settings) -> None:
    requests: list[httpx.Request] = []
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        if len(requests) == 2:
            return httpx.Response(503)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text="<html><body>é</body></html>",
        )

    client = LazyPortfolioClient(
        settings, transport=httpx.MockTransport(handler), sleep=delays.append
    )
    page = client.fetch_universe()
    assert len(requests) == 3
    assert delays == [3, 2]
    assert "QCrossInstrumentDiscovery" in requests[0].headers["User-Agent"]
    assert requests[0].extensions["timeout"]["connect"] == 10
    assert requests[0].extensions["timeout"]["read"] == 30
    assert "é" in page.html


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError]
)
def test_transport_failure_retried_and_exhausted(
    settings: Settings, error: type[httpx.RequestError]
) -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise error("Transient failure", request=request)

    with pytest.raises(FetchError, match="after 4 attempts"):
        LazyPortfolioClient(
            settings, transport=httpx.MockTransport(handler), sleep=delays.append
        ).fetch_universe()
    assert attempts == 4 and delays == [1, 2, 4]


def test_permanent_http_error_not_retried(settings: Settings) -> None:
    delays: list[float] = []
    client = LazyPortfolioClient(
        settings, transport=httpx.MockTransport(lambda _: httpx.Response(403)), sleep=delays.append
    )
    with pytest.raises(FetchError, match="HTTP 403"):
        client.fetch_universe()
    assert delays == []


def test_decodes_html_meta_encoding_and_preserves_bytes(settings: Settings) -> None:
    content = '<html><meta charset="windows-1252"><body>Café</body></html>'.encode("cp1252")
    client = LazyPortfolioClient(
        settings,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"Content-Type": "text/html"}, content=content)
        ),
    )
    page = client.fetch_universe()
    assert "Café" in page.html and page.content == content


def test_non_html_rejected(settings: Settings) -> None:
    client = LazyPortfolioClient(
        settings,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"error": "something went wrong"})
        ),
    )
    with pytest.raises(FetchError, match="Expected HTML"):
        client.fetch_universe()


def test_retry_after_http_date_bounded(settings: Settings) -> None:
    client = LazyPortfolioClient(settings)
    future = format_datetime(datetime.now(UTC) + timedelta(days=1))
    with pytest.raises(FetchError, match="retry discovery later"):
        client._delay(0, future)
    assert client._delay(1, "invalid date") == 2


def test_long_retry_after_does_not_request_again_too_soon(settings: Settings) -> None:
    requests = 0
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(429, headers={"Retry-After": "3600"})

    with pytest.raises(FetchError, match="retry discovery later"):
        LazyPortfolioClient(
            settings, transport=httpx.MockTransport(handler), sleep=delays.append
        ).fetch_universe()
    assert requests == 1 and delays == []
