import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from pydantic import BaseModel

from app.config import Settings
from app.ingestion.base import IngestionError

logger = logging.getLogger(__name__)
SOURCE_PATH = "/portfolio-backtest-and-simulation/"
USER_AGENT = (
    "Mozilla/5.0 (compatible; QCrossInstrumentDiscovery/0.1; +https://qcross.org) "
    "AppleWebKit/537.36 (KHTML, like Gecko)"
)


class FetchError(IngestionError):
    pass


class FetchedPage(BaseModel):
    url: str
    html: str
    content: bytes
    encoding: str
    fetched_at: datetime


class LazyPortfolioClient:
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

    def _delay(self, attempt: int, retry_after: str | None) -> float:
        delay = self.settings.http_backoff_seconds * 2**attempt
        if retry_after:
            try:
                requested = float(retry_after)
            except ValueError:
                try:
                    requested = (
                        parsedate_to_datetime(retry_after) - datetime.now(UTC)
                    ).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    requested = 0
            if requested > self.settings.http_max_retry_delay:
                raise FetchError(
                    f"Server Retry-After ({requested:.0f}s) exceeds the configured maximum "
                    f"wait ({self.settings.http_max_retry_delay:.0f}s); retry discovery later"
                )
            delay = max(delay, requested)
        return min(delay, self.settings.http_max_retry_delay)

    def fetch_universe(self) -> FetchedPage:
        return self.fetch_page(SOURCE_PATH)

    def fetch_page(self, path: str) -> FetchedPage:
        """Fetch a public HTML page using the same retry/encoding policy as discovery."""
        return self.fetch_document(path, media_types=("text/html", "application/xhtml+xml"))

    def fetch_document(self, path: str, *, media_types: tuple[str, ...]) -> FetchedPage:
        """Fetch a public document, checking its expected media type."""
        from urllib.parse import urlsplit

        base = self.settings.lazyportfolio_base_url.rstrip("/")
        url = path if path.startswith(("https://", "http://")) else base + path
        if urlsplit(url).netloc != urlsplit(base).netloc:
            raise FetchError("Refusing to fetch a page outside the configured source host")
        logger.info("lazyportfolio.fetch.started url=%s", url)
        timeout = httpx.Timeout(
            self.settings.http_read_timeout,
            connect=self.settings.http_connect_timeout,
        )
        with httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": ",".join(media_types)},
            timeout=timeout,
            follow_redirects=True,
            transport=self.transport,
        ) as client:
            for attempt in range(self.settings.http_max_attempts):
                retry_after = None
                try:
                    response = client.get(url)
                    if response.status_code == 429 or 500 <= response.status_code < 600:
                        retry_after = response.headers.get("Retry-After")
                        raise FetchError(f"Transient HTTP {response.status_code} fetching {url}")
                    response.raise_for_status()
                    content_type = response.headers.get("Content-Type", "").lower()
                    if not any(t in content_type for t in media_types):
                        expected = "HTML" if "text/html" in media_types else str(media_types)
                        raise FetchError(f"Expected {expected} from {url}; got {content_type!r}")
                    # BeautifulSoup considers HTTP charset, BOM and HTML meta encoding.
                    # Feed the decoded HTML to the parser but preserve the original bytes.
                    from bs4 import UnicodeDammit

                    declared = response.charset_encoding
                    decoded = UnicodeDammit(
                        response.content, [declared] if declared else [], is_html=True
                    )
                    if decoded.unicode_markup is None or decoded.contains_replacement_characters:
                        raise FetchError(
                            f"Cannot decode source response from {url} without data loss"
                        )
                    page = FetchedPage(
                        url=str(response.url),
                        html=decoded.unicode_markup,
                        content=response.content,
                        encoding=decoded.original_encoding or "utf-8",
                        fetched_at=datetime.now(UTC),
                    )
                    logger.info(
                        "lazyportfolio.fetch.completed status=%d bytes=%d encoding=%s",
                        response.status_code,
                        len(page.content),
                        page.encoding,
                    )
                    return page
                except httpx.HTTPStatusError as exc:
                    raise FetchError(f"HTTP {exc.response.status_code} fetching {url}") from exc
                except (
                    httpx.TimeoutException,
                    httpx.NetworkError,
                    httpx.RemoteProtocolError,
                    FetchError,
                ) as exc:
                    # Invalid content/encoding is permanent; retry only transient responses/errors.
                    if isinstance(exc, FetchError) and not str(exc).startswith("Transient HTTP"):
                        raise
                    if attempt + 1 == self.settings.http_max_attempts:
                        raise FetchError(
                            f"Unable to fetch {url} after {attempt + 1} attempts: {exc}"
                        ) from exc
                    delay = self._delay(attempt, retry_after)
                    logger.warning(
                        "lazyportfolio.fetch.retry attempt=%d delay=%.1fs reason=%s",
                        attempt + 1,
                        delay,
                        exc,
                    )
                    self.sleep(delay)
                except httpx.RequestError as exc:
                    raise FetchError(f"HTTP request failed for {url}: {exc}") from exc
        raise FetchError(f"Unable to fetch {url}")
