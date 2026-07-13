from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from site_alert.config import SourcePageConfig
from site_alert.models import ArticleCandidate, ArticleRecord, DeliveryClaim, DeliveryWorkItem


class Repository(Protocol):
    def sync_source_pages(self, pages: list[SourcePageConfig]) -> None: ...

    def get_source_page_state(self, source_page_id: str) -> dict[str, Any]: ...

    def create_crawl_run(
        self, github_run_id: str | None, attempt: int, trigger_type: str, page_count: int
    ) -> str: ...

    def finish_crawl_run(self, crawl_run_id: str, values: dict[str, Any]) -> None: ...

    def create_source_page_crawl_run(self, crawl_run_id: str, source_page_id: str) -> str: ...

    def finish_source_page_crawl_run(self, run_id: str, values: dict[str, Any]) -> None: ...

    def upsert_article(self, candidate: ArticleCandidate) -> ArticleRecord: ...

    def ensure_delivery(
        self,
        article: ArticleRecord,
        page: SourcePageConfig,
        target_chat_id: str,
    ) -> tuple[str, bool]: ...

    def mark_initial_sync(self, delivery_id: str) -> None: ...

    def set_source_page_initialized(self, source_page_id: str, count: int) -> None: ...

    def request_delivery_retry(self, delivery_id: str) -> None: ...

    def update_source_page_status(
        self,
        source_page_id: str,
        success: bool,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None: ...

    def get_due_deliveries(self, delivery_id: str | None = None) -> list[DeliveryWorkItem]: ...

    def claim_delivery(self, delivery_id: str) -> DeliveryClaim | None: ...

    def record_delivery_attempt(
        self,
        delivery_id: str,
        attempt_number: int,
        status: str,
        http_status: int | None,
        error_code: str | None,
        error_message: str | None,
        response_time_ms: int | None,
    ) -> None: ...

    def mark_delivery_sent(self, delivery_id: str, message_id: str, http_status: int) -> None: ...

    def mark_delivery_failed(
        self,
        delivery_id: str,
        status: str,
        error_code: str,
        error_message: str,
        next_retry_at: datetime | None,
        http_status: int | None,
    ) -> None: ...

    def recover_stale_deliveries(self) -> int: ...
