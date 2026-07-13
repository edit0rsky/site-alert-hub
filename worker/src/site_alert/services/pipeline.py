from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from site_alert.clients.http import FetchError, HttpFetcher
from site_alert.clients.telegram import TelegramClient, TelegramError
from site_alert.config import AppSettings, SourcePageConfig
from site_alert.models import DeliveryStatus, DeliveryWorkItem
from site_alert.parsers.base import ParserRegistry, ParserSelectorMismatch
from site_alert.repositories.protocol import Repository
from site_alert.utils.logging import log_event
from site_alert.utils.messages import telegram_message

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(
        self,
        settings: AppSettings,
        pages: list[SourcePageConfig],
        repository: Repository,
        fetcher: HttpFetcher,
        parsers: ParserRegistry,
        browser_fetcher: HttpFetcher | None = None,
    ) -> None:
        self.settings = settings
        self.pages = pages
        self.repository = repository
        self.fetcher = fetcher
        self.browser_fetcher = browser_fetcher
        self.parsers = parsers
        self._telegram_clients: dict[str, TelegramClient] = {}

    def close(self) -> None:
        self.fetcher.close()
        if self.browser_fetcher:
            self.browser_fetcher.close()
        for client in self._telegram_clients.values():
            client.close()

    def _client_for(self, profile_name: str) -> TelegramClient:
        client = self._telegram_clients.get(profile_name)
        if client:
            return client
        try:
            profile = self.settings.bot_profiles[profile_name]
        except KeyError as exc:
            raise ValueError(f"bot profile is not configured: {profile_name}") from exc
        client = TelegramClient(profile, timeout_seconds=self.settings.http_timeout_seconds)
        self._telegram_clients[profile_name] = client
        return client

    def run(
        self,
        source_page_id: str | None = None,
        dry_run: bool = False,
        force_baseline: bool = False,
        delivery_id: str | None = None,
        trigger_type: str = "schedule",
    ) -> dict[str, int | str]:
        pages = [page for page in self.pages if page.enabled]
        if source_page_id:
            pages = [page for page in pages if page.id == source_page_id]
            if not pages:
                raise ValueError(f"enabled source page not found: {source_page_id}")
        self.repository.sync_source_pages(pages)
        if delivery_id:
            self.repository.request_delivery_retry(delivery_id)
        crawl_run_id = self.repository.create_crawl_run(
            github_run_id=None,
            attempt=1,
            trigger_type=trigger_type,
            page_count=len(pages),
        )
        totals: dict[str, int] = {
            "source_page_count": len(pages),
            "success_page_count": 0,
            "failed_page_count": 0,
            "discovered_count": 0,
            "new_article_count": 0,
            "sent_count": 0,
            "failed_delivery_count": 0,
        }
        for page_index, page in enumerate(pages):
            result = self._crawl_page(crawl_run_id, page, force_baseline=force_baseline)
            totals["discovered_count"] += result["item_count"]
            totals["new_article_count"] += result["new_article_count"]
            if result["error_code"]:
                totals["failed_page_count"] += 1
            else:
                totals["success_page_count"] += 1
            if page_index < len(pages) - 1 and page.request.interval_seconds:
                time.sleep(page.request.interval_seconds)

        if not dry_run:
            self.repository.recover_stale_deliveries()
            delivery_items = self.repository.get_due_deliveries(delivery_id)
            for item in delivery_items:
                sent, failed = self._deliver(item)
                totals["sent_count"] += sent
                totals["failed_delivery_count"] += failed
        else:
            log_event(logger, logging.INFO, "dry run: Telegram delivery skipped", status="dry_run")

        self.repository.finish_crawl_run(
            crawl_run_id,
            {
                "finished_at": datetime.now(UTC).isoformat(),
                "status": "failed" if totals["failed_page_count"] else "succeeded",
                **{key: value for key, value in totals.items() if key != "crawl_run_id"},
            },
        )
        summary: dict[str, int | str] = {**totals, "crawl_run_id": crawl_run_id}
        return summary

    def _crawl_page(
        self,
        crawl_run_id: str,
        page: SourcePageConfig,
        force_baseline: bool,
    ) -> dict[str, Any]:
        page_run_id = self.repository.create_source_page_crawl_run(crawl_run_id, page.id)
        state = self.repository.get_source_page_state(page.id)
        baseline = force_baseline or not bool(state.get("initialized", False))
        started = time.perf_counter()
        try:
            active_fetcher = self.browser_fetcher if page.render_js else self.fetcher
            if active_fetcher is None:
                raise FetchError(
                    "render_js_not_supported", "render_js requires the optional browser worker"
                )
            response = active_fetcher.fetch(page.list_url, page.request, page.crawl.max_retries)
            parser = self.parsers.get(page.parser)
            candidates = parser.parse(response.html, page)
            new_count = 0
            for candidate in candidates:
                article = self.repository.upsert_article(candidate)
                new_count += int(article.is_new)
                target_chat_id = self.settings.bot_profiles.get(page.bot_profile)
                if not target_chat_id:
                    raise ValueError(f"bot profile is not configured: {page.bot_profile}")
                delivery_id, _ = self.repository.ensure_delivery(
                    article, page, str(target_chat_id.chat_id)
                )
                if baseline:
                    self.repository.mark_initial_sync(delivery_id)
            if baseline:
                self.repository.set_source_page_initialized(page.id, len(candidates))
            self.repository.update_source_page_status(page.id, success=True)
            elapsed = int((time.perf_counter() - started) * 1000)
            self.repository.finish_source_page_crawl_run(
                page_run_id,
                {
                    "finished_at": datetime.now(UTC).isoformat(),
                    "status": "succeeded",
                    "http_status": response.http_status,
                    "response_time_ms": elapsed,
                    "item_count": len(candidates),
                    "new_article_count": new_count,
                    "sent_count": 0,
                    "failed_count": 0,
                },
            )
            log_event(
                logger,
                logging.INFO,
                "board crawl succeeded",
                crawl_run_id=crawl_run_id,
                source_page_id=page.id,
                status="succeeded",
            )
            return {
                "item_count": len(candidates),
                "new_article_count": new_count,
                "error_code": None,
            }
        except ParserSelectorMismatch as exc:
            return self._page_failure(
                page_run_id,
                crawl_run_id,
                page,
                "parser_selector_mismatch",
                str(exc),
                None,
                started,
            )
        except FetchError as exc:
            return self._page_failure(
                page_run_id,
                crawl_run_id,
                page,
                exc.code,
                str(exc),
                exc.http_status,
                started,
            )
        except Exception as exc:
            return self._page_failure(
                page_run_id,
                crawl_run_id,
                page,
                "worker_error",
                str(exc)[:500],
                None,
                started,
            )

    def _page_failure(
        self,
        page_run_id: str,
        crawl_run_id: str,
        page: SourcePageConfig,
        error_code: str,
        error_message: str,
        http_status: int | None,
        started: float,
    ) -> dict[str, Any]:
        self.repository.finish_source_page_crawl_run(
            page_run_id,
            {
                "finished_at": datetime.now(UTC).isoformat(),
                "status": "failed",
                "http_status": http_status,
                "response_time_ms": int((time.perf_counter() - started) * 1000),
                "error_code": error_code,
                "error_message": error_message[:500],
                "item_count": 0,
                "new_article_count": 0,
                "sent_count": 0,
                "failed_count": 0,
            },
        )
        self.repository.update_source_page_status(
            page.id,
            success=False,
            error_code=error_code,
            error_message=error_message[:500],
        )
        log_event(
            logger,
            logging.ERROR,
            "board crawl failed",
            crawl_run_id=crawl_run_id,
            source_page_id=page.id,
            status="failed",
            error_code=error_code,
        )
        return {"item_count": 0, "new_article_count": 0, "error_code": error_code}

    def _deliver(self, item: DeliveryWorkItem) -> tuple[int, int]:
        claim = self.repository.claim_delivery(item.id)
        if not claim:
            return 0, 0
        started = time.perf_counter()
        try:
            client = self._client_for(item.bot_profile)
            result = client.send_message(telegram_message(item), item.article.normalized_url)
        except (TelegramError, ValueError) as exc:
            error_code = getattr(exc, "code", "configuration_error")
            http_status = getattr(exc, "http_status", None)
            retryable = bool(getattr(exc, "retryable", False))
            is_dead_letter = (
                claim.attempt_number >= self.settings.max_delivery_attempts or not retryable
            )
            status = (
                DeliveryStatus.DEAD_LETTER.value if is_dead_letter else DeliveryStatus.FAILED.value
            )
            next_retry_at = None
            if not is_dead_letter:
                delay = self.settings.delivery_retry_base_seconds * (
                    2 ** (claim.attempt_number - 1)
                )
                next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
            message = str(exc).replace("bot", "[redacted]")[:500]
            self.repository.record_delivery_attempt(
                item.id,
                claim.attempt_number,
                "failed",
                http_status,
                str(error_code),
                message,
                int((time.perf_counter() - started) * 1000),
            )
            self.repository.mark_delivery_failed(
                item.id,
                status,
                str(error_code),
                message,
                next_retry_at,
                http_status,
            )
            log_event(
                logger,
                logging.ERROR,
                "Telegram delivery failed",
                article_id=item.article.id,
                delivery_id=item.id,
                status=status,
                error_code=error_code,
            )
            return 0, 1
        else:
            response_time_ms = int((time.perf_counter() - started) * 1000)
            self.repository.record_delivery_attempt(
                item.id,
                claim.attempt_number,
                "sent",
                result.http_status,
                None,
                None,
                response_time_ms,
            )
            self.repository.mark_delivery_sent(
                item.id, result.telegram_message_id, result.http_status
            )
            log_event(
                logger,
                logging.INFO,
                "Telegram delivery succeeded",
                article_id=item.article.id,
                delivery_id=item.id,
                status="sent",
            )
            return 1, 0
