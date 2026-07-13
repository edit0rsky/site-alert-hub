from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    RETRY_REQUESTED = "retry_requested"
    DEAD_LETTER = "dead_letter"
    SKIPPED_INITIAL_SYNC = "skipped_initial_sync"


@dataclass(frozen=True)
class ArticleCandidate:
    source_page_id: str
    title: str
    original_url: str
    normalized_url: str
    article_key: str
    external_id: str | None = None
    published_at: str | None = None


@dataclass(frozen=True)
class ArticleRecord:
    id: str
    source_page_id: str
    title: str
    normalized_url: str
    article_key: str
    external_id: str | None = None
    published_at: str | None = None
    is_new: bool = False


@dataclass(frozen=True)
class DeliveryWorkItem:
    id: str
    article: ArticleRecord
    site_name: str
    page_name: str
    bot_profile: str
    target_chat_id: str
    status: DeliveryStatus
    attempt_count: int = 0
    delivery_generation: int = 1


@dataclass(frozen=True)
class DeliveryClaim:
    delivery_id: str
    attempt_number: int


@dataclass(frozen=True)
class TelegramSendResult:
    telegram_message_id: str
    http_status: int


@dataclass(frozen=True)
class CrawlResult:
    source_page_id: str
    item_count: int
    new_article_count: int
    sent_count: int
    failed_count: int
    error_code: str | None = None
    error_message: str | None = None


def utc_now() -> datetime:
    return datetime.now(UTC)
