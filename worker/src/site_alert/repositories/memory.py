from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from site_alert.config import SourcePageConfig
from site_alert.models import (
    ArticleCandidate,
    ArticleRecord,
    DeliveryClaim,
    DeliveryStatus,
    DeliveryWorkItem,
)


class InMemoryRepository:
    """Deterministic repository used by unit tests and local dry-run experiments."""

    def __init__(self) -> None:
        self.pages: dict[str, dict[str, Any]] = {}
        self.articles: dict[str, ArticleRecord] = {}
        self.deliveries: dict[str, dict[str, Any]] = {}
        self.attempts: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def sync_source_pages(self, pages: list[SourcePageConfig]) -> None:
        for page in pages:
            existing = self.pages.get(page.id, {})
            self.pages[page.id] = {
                "config": page,
                "initialized": existing.get("initialized", False),
                "initialized_at": existing.get("initialized_at"),
                "baseline_article_count": existing.get("baseline_article_count", 0),
            }

    def get_source_page_state(self, source_page_id: str) -> dict[str, Any]:
        return self.pages.get(source_page_id, {"initialized": False, "baseline_article_count": 0})

    def create_crawl_run(
        self, github_run_id: str | None, attempt: int, trigger_type: str, page_count: int
    ) -> str:
        return str(uuid.uuid4())

    def finish_crawl_run(self, crawl_run_id: str, values: dict[str, Any]) -> None:
        return None

    def create_source_page_crawl_run(self, crawl_run_id: str, source_page_id: str) -> str:
        return str(uuid.uuid4())

    def finish_source_page_crawl_run(self, run_id: str, values: dict[str, Any]) -> None:
        return None

    def upsert_article(self, candidate: ArticleCandidate) -> ArticleRecord:
        found: ArticleRecord | None = None
        for article in self.articles.values():
            if article.source_page_id != candidate.source_page_id:
                continue
            if candidate.external_id and article.external_id == candidate.external_id:
                found = article
                break
            if (
                article.normalized_url == candidate.normalized_url
                or article.article_key == candidate.article_key
            ):
                found = article
                break
        if found:
            updated = ArticleRecord(
                id=found.id,
                source_page_id=found.source_page_id,
                title=candidate.title,
                normalized_url=candidate.normalized_url,
                article_key=found.article_key,
                external_id=candidate.external_id or found.external_id,
                published_at=candidate.published_at or found.published_at,
                is_new=False,
            )
            self.articles[found.id] = updated
            return updated
        record = ArticleRecord(
            id=str(uuid.uuid4()),
            source_page_id=candidate.source_page_id,
            title=candidate.title,
            normalized_url=candidate.normalized_url,
            article_key=candidate.article_key,
            external_id=candidate.external_id,
            published_at=candidate.published_at,
            is_new=True,
        )
        self.articles[record.id] = record
        return record

    def ensure_delivery(
        self, article: ArticleRecord, page: SourcePageConfig, target_chat_id: str
    ) -> tuple[str, bool]:
        key = (article.id, page.bot_profile, str(target_chat_id), 1)
        for delivery_id, delivery in self.deliveries.items():
            if delivery["key"] == key:
                return delivery_id, False
        delivery_id = str(uuid.uuid4())
        self.deliveries[delivery_id] = {
            "key": key,
            "article_id": article.id,
            "page_id": page.id,
            "bot_profile": page.bot_profile,
            "target_chat_id": str(target_chat_id),
            "status": DeliveryStatus.PENDING,
            "attempt_count": 0,
            "telegram_message_id": None,
            "http_status": None,
            "last_error_code": None,
            "last_error_message": None,
            "next_retry_at": None,
            "sent_at": None,
            "delivery_generation": 1,
            "updated_at": datetime.now(UTC),
        }
        return delivery_id, True

    def mark_initial_sync(self, delivery_id: str) -> None:
        if self.deliveries[delivery_id]["status"] in {
            DeliveryStatus.PENDING,
            DeliveryStatus.FAILED,
            DeliveryStatus.RETRY_REQUESTED,
        }:
            self.deliveries[delivery_id]["status"] = DeliveryStatus.SKIPPED_INITIAL_SYNC
        self.deliveries[delivery_id]["updated_at"] = datetime.now(UTC)

    def set_source_page_initialized(self, source_page_id: str, count: int) -> None:
        self.pages[source_page_id]["initialized"] = True
        self.pages[source_page_id]["initialized_at"] = datetime.now(UTC)
        self.pages[source_page_id]["baseline_article_count"] = count

    def request_delivery_retry(self, delivery_id: str) -> None:
        delivery = self.deliveries.get(delivery_id)
        if delivery and delivery["status"] in {
            DeliveryStatus.FAILED,
            DeliveryStatus.DEAD_LETTER,
            DeliveryStatus.PENDING,
        }:
            delivery["status"] = DeliveryStatus.RETRY_REQUESTED
            delivery["next_retry_at"] = datetime.now(UTC)
            delivery["updated_at"] = datetime.now(UTC)

    def update_source_page_status(
        self,
        source_page_id: str,
        success: bool,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        page = self.pages[source_page_id]
        page["last_crawled_at"] = datetime.now(UTC)
        if success:
            page["last_success_at"] = page["last_crawled_at"]
            page["last_error_code"] = None
            page["last_error_message"] = None
            page["consecutive_failure_count"] = 0
        else:
            page["last_error_at"] = page["last_crawled_at"]
            page["last_error_code"] = error_code
            page["last_error_message"] = error_message
            page["consecutive_failure_count"] = page.get("consecutive_failure_count", 0) + 1

    def _work_item(self, delivery_id: str) -> DeliveryWorkItem:
        delivery = self.deliveries[delivery_id]
        article = self.articles[delivery["article_id"]]
        page: SourcePageConfig = self.pages[delivery["page_id"]]["config"]
        return DeliveryWorkItem(
            id=delivery_id,
            article=article,
            site_name=page.site_name,
            page_name=page.page_name,
            bot_profile=delivery["bot_profile"],
            target_chat_id=delivery["target_chat_id"],
            status=delivery["status"],
            attempt_count=delivery["attempt_count"],
            delivery_generation=delivery["delivery_generation"],
        )

    def get_due_deliveries(self, delivery_id: str | None = None) -> list[DeliveryWorkItem]:
        now = datetime.now(UTC)
        result: list[DeliveryWorkItem] = []
        for current_id, delivery in self.deliveries.items():
            if delivery_id and current_id != delivery_id:
                continue
            if delivery["status"] not in {
                DeliveryStatus.PENDING,
                DeliveryStatus.RETRY_REQUESTED,
                DeliveryStatus.FAILED,
            }:
                continue
            retry_at = delivery["next_retry_at"]
            if not delivery_id and retry_at is not None and retry_at > now:
                continue
            result.append(self._work_item(current_id))
        return result

    def claim_delivery(self, delivery_id: str) -> DeliveryClaim | None:
        with self._lock:
            delivery = self.deliveries.get(delivery_id)
            if not delivery or delivery["status"] in {
                DeliveryStatus.SENT,
                DeliveryStatus.DEAD_LETTER,
            }:
                return None
            if delivery["status"] == DeliveryStatus.SENDING:
                return None
            delivery["status"] = DeliveryStatus.SENDING
            delivery["attempt_count"] += 1
            delivery["updated_at"] = datetime.now(UTC)
            return DeliveryClaim(delivery_id, delivery["attempt_count"])

    def record_delivery_attempt(
        self,
        delivery_id: str,
        attempt_number: int,
        status: str,
        http_status: int | None,
        error_code: str | None,
        error_message: str | None,
        response_time_ms: int | None,
    ) -> None:
        self.attempts.append(
            {
                "delivery_id": delivery_id,
                "attempt_number": attempt_number,
                "status": status,
                "http_status": http_status,
                "error_code": error_code,
                "error_message": error_message,
                "response_time_ms": response_time_ms,
            }
        )

    def mark_delivery_sent(self, delivery_id: str, message_id: str, http_status: int) -> None:
        delivery = self.deliveries[delivery_id]
        delivery.update(
            status=DeliveryStatus.SENT,
            telegram_message_id=message_id,
            http_status=http_status,
            sent_at=datetime.now(UTC),
            next_retry_at=None,
            updated_at=datetime.now(UTC),
        )

    def mark_delivery_failed(
        self,
        delivery_id: str,
        status: str,
        error_code: str,
        error_message: str,
        next_retry_at: datetime | None,
        http_status: int | None,
    ) -> None:
        self.deliveries[delivery_id].update(
            status=DeliveryStatus(status),
            last_error_code=error_code,
            last_error_message=error_message,
            next_retry_at=next_retry_at,
            http_status=http_status,
            updated_at=datetime.now(UTC),
        )

    def recover_stale_deliveries(self) -> int:
        threshold = datetime.now(UTC) - timedelta(minutes=15)
        recovered = 0
        for delivery in self.deliveries.values():
            if delivery["status"] == DeliveryStatus.SENDING and delivery["updated_at"] < threshold:
                delivery["status"] = DeliveryStatus.FAILED
                delivery["next_retry_at"] = datetime.now(UTC)
                recovered += 1
        return recovered
