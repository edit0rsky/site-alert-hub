from __future__ import annotations

import logging
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from site_alert.config import RequestConfig

logger = logging.getLogger(__name__)


class FetchError(RuntimeError):
    def __init__(self, code: str, message: str, http_status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True)
class FetchResult:
    html: str
    http_status: int
    response_time_ms: int


class RobotsPolicy:
    """Fetches and caches robots.txt before a board request."""

    def __init__(self, client: httpx.Client, timeout_seconds: float) -> None:
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str, user_agent: str) -> bool:
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        if origin not in self._cache:
            robots_url = f"{origin}/robots.txt"
            try:
                response = self._client.get(
                    robots_url,
                    headers={"User-Agent": user_agent},
                    timeout=self._timeout_seconds,
                )
            except httpx.HTTPError:
                logger.warning("robots.txt could not be fetched; allowing request")
                self._cache[origin] = None
            else:
                if response.status_code == 404:
                    self._cache[origin] = None
                elif response.status_code >= 400:
                    logger.warning("robots.txt returned an error; allowing request")
                    self._cache[origin] = None
                else:
                    parser = urllib.robotparser.RobotFileParser()
                    parser.set_url(robots_url)
                    parser.parse(response.text.splitlines())
                    self._cache[origin] = parser
        robots_parser = self._cache[origin]
        return robots_parser is None or robots_parser.can_fetch(user_agent, url)


class HttpFetcher:
    def __init__(self, robots_timeout_seconds: float = 10) -> None:
        self._client = httpx.Client(follow_redirects=True)
        self._robots = RobotsPolicy(self._client, robots_timeout_seconds)

    def close(self) -> None:
        self._client.close()

    def fetch(self, url: str, request: RequestConfig, max_retries: int) -> FetchResult:
        if not self._robots.allowed(url, request.user_agent):
            raise FetchError("robots_disallowed", "robots.txt disallows this URL")
        last_error: FetchError | None = None
        for attempt in range(max_retries + 1):
            started = time.perf_counter()
            try:
                response = self._client.get(
                    url,
                    headers={
                        "User-Agent": request.user_agent,
                        "Accept": "text/html,application/xhtml+xml",
                    },
                    timeout=request.timeout_seconds,
                )
            except httpx.TimeoutException:
                last_error = FetchError("http_timeout", "board request timed out")
            except httpx.HTTPError:
                last_error = FetchError("http_connection_error", "board request failed")
            else:
                elapsed = int((time.perf_counter() - started) * 1000)
                if response.status_code == 200:
                    return FetchResult(response.text, response.status_code, elapsed)
                retryable = response.status_code == 429 or 500 <= response.status_code <= 599
                code = "http_rate_limited" if response.status_code == 429 else "http_status_error"
                last_error = FetchError(
                    code, f"board returned HTTP {response.status_code}", response.status_code
                )
                if not retryable:
                    break
            if attempt < max_retries:
                time.sleep(min(2.0, 0.5 * (2**attempt)))
        if last_error:
            raise last_error
        raise FetchError("http_unknown_error", "board request failed")


class BrowserFetcher(HttpFetcher):
    """Optional Playwright fetcher for pages rendered with JavaScript."""

    def __init__(self, robots_timeout_seconds: float = 10) -> None:
        super().__init__(robots_timeout_seconds)
        try:
            from playwright.sync_api import (  # type: ignore[import-not-found]
                TimeoutError as PlaywrightTimeoutError,
            )
            from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
        except ImportError as exc:
            raise FetchError(
                "playwright_not_installed",
                "install the worker browser extra and Chromium before using render_js",
            ) from exc
        self._playwright_factory = sync_playwright
        self._playwright_timeout_error = PlaywrightTimeoutError

    def fetch(self, url: str, request: RequestConfig, max_retries: int) -> FetchResult:
        if not self._robots.allowed(url, request.user_agent):
            raise FetchError("robots_disallowed", "robots.txt disallows this URL")
        last_error: FetchError | None = None
        for attempt in range(max_retries + 1):
            started = time.perf_counter()
            try:
                with self._playwright_factory() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    context = browser.new_context(user_agent=request.user_agent)
                    page = context.new_page()
                    response = page.goto(
                        url,
                        wait_until="networkidle",
                        timeout=int(request.timeout_seconds * 1000),
                    )
                    html = page.content()
                    status_code = response.status if response else 200
                    context.close()
                    browser.close()
                elapsed = int((time.perf_counter() - started) * 1000)
                if status_code == 200:
                    return FetchResult(html, status_code, elapsed)
                last_error = FetchError(
                    "http_status_error",
                    f"browser returned HTTP {status_code}",
                    status_code,
                )
            except self._playwright_timeout_error:
                last_error = FetchError("render_timeout", "browser page timed out")
            except Exception:
                last_error = FetchError("render_error", "browser page could not be rendered")
            if attempt < max_retries:
                time.sleep(min(2.0, 0.5 * (2**attempt)))
        if last_error:
            raise last_error
        raise FetchError("render_unknown_error", "browser page could not be rendered")
