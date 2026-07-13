from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from supabase import Client, create_client

from site_alert.config import SourcePageConfig
from site_alert.models import (
    ArticleCandidate,
    ArticleRecord,
    DeliveryClaim,
    DeliveryStatus,
    DeliveryWorkItem,
)


class SupabaseRepository:
    def __init__(self, url: str, service_role_key: str) -> None:
        self.client: Client = create_client(url, service_role_key)

    @staticmethod
    def _first(data: Any) -> dict[str, Any]:
        if not data:
            raise RuntimeError("Supabase returned no data")
        return data[0] if isinstance(data, list) else data

    def sync_source_pages(self, pages: list[SourcePageConfig]) -> None:
        rows = [
            {
                "id": page.id,
                "site_name": page.site_name,
                "page_name": page.page_name,
                "category": page.category,
                "list_url": page.list_url,
                "base_url": page.base_url,
                "parser_key": page.parser,
                "bot_profile": page.bot_profile,
                "enabled": page.enabled,
                "config": page.model_dump(mode="json"),
                "updated_at": datetime.now(UTC).isoformat(),
            }
            for page in pages
        ]
        if rows:
            self.client.table("source_pages").upsert(rows, on_conflict="id").execute()  # type: ignore[arg-type]

    def get_source_page_state(self, source_page_id: str) -> dict[str, Any]:
        response = (
            self.client.table("source_pages")
            .select("id,initialized,baseline_article_count")
            .eq("id", source_page_id)
            .limit(1)
            .execute()
        )
        return (
            self._first(response.data)
            if response.data
            else {"initialized": False, "baseline_article_count": 0}
        )

    def create_crawl_run(
        self, github_run_id: str | None, attempt: int, trigger_type: str, page_count: int
    ) -> str:
        response = (
            self.client.table("crawl_runs")
            .insert(
                {
                    "github_run_id": github_run_id,
                    "github_run_attempt": attempt,
                    "trigger_type": trigger_type,
                    "source_page_count": page_count,
                    "status": "running",
                }
            )
            .execute()
        )
        return str(self._first(response.data)["id"])

    def finish_crawl_run(self, crawl_run_id: str, values: dict[str, Any]) -> None:
        self.client.table("crawl_runs").update(values).eq("id", crawl_run_id).execute()

    def create_source_page_crawl_run(self, crawl_run_id: str, source_page_id: str) -> str:
        response = (
            self.client.table("source_page_crawl_runs")
            .insert(
                {
                    "crawl_run_id": crawl_run_id,
                    "source_page_id": source_page_id,
                    "status": "running",
                }
            )
            .execute()
        )
        return str(self._first(response.data)["id"])

    def finish_source_page_crawl_run(self, run_id: str, values: dict[str, Any]) -> None:
        self.client.table("source_page_crawl_runs").update(values).eq("id", run_id).execute()

    def upsert_article(self, candidate: ArticleCandidate) -> ArticleRecord:
        response = self.client.rpc(
            "upsert_article_candidate",
            {
                "p_source_page_id": candidate.source_page_id,
                "p_external_id": candidate.external_id,
                "p_title": candidate.title,
                "p_original_url": candidate.original_url,
                "p_normalized_url": candidate.normalized_url,
                "p_article_key": candidate.article_key,
                "p_published_at": candidate.published_at,
            },
        ).execute()
        row = self._first(response.data)
        return ArticleRecord(
            id=str(row["id"]),
            source_page_id=str(row["source_page_id"]),
            title=str(row["title"]),
            normalized_url=str(row["normalized_url"]),
            article_key=str(row["article_key"]),
            external_id=row.get("external_id"),
            published_at=row.get("published_at"),
            is_new=bool(row.get("is_new", False)),
        )

    def ensure_delivery(
        self, article: ArticleRecord, page: SourcePageConfig, target_chat_id: str
    ) -> tuple[str, bool]:
        response = self.client.rpc(
            "ensure_delivery",
            {
                "p_article_id": article.id,
                "p_bot_profile": page.bot_profile,
                "p_target_chat_id": str(target_chat_id),
                "p_delivery_generation": 1,
            },
        ).execute()
        row = self._first(response.data)
        return str(row["id"]), bool(row.get("created", False))

    def mark_initial_sync(self, delivery_id: str) -> None:
        self.client.rpc("mark_delivery_initial_sync", {"p_delivery_id": delivery_id}).execute()

    def set_source_page_initialized(self, source_page_id: str, count: int) -> None:
        self.client.rpc(
            "set_source_page_initialized",
            {"p_source_page_id": source_page_id, "p_baseline_article_count": count},
        ).execute()

    def request_delivery_retry(self, delivery_id: str) -> None:
        self.client.rpc("force_retry_delivery", {"p_delivery_id": delivery_id}).execute()

    def update_source_page_status(
        self,
        source_page_id: str,
        success: bool,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.client.rpc(
            "record_source_page_status",
            {
                "p_source_page_id": source_page_id,
                "p_success": success,
                "p_error_code": error_code,
                "p_error_message": error_message,
            },
        ).execute()

    @staticmethod
    def _to_work_item(row: dict[str, Any]) -> DeliveryWorkItem:
        article = row["articles"]
        page = article["source_pages"]
        return DeliveryWorkItem(
            id=str(row["id"]),
            article=ArticleRecord(
                id=str(article["id"]),
                source_page_id=str(article["source_page_id"]),
                title=str(article["title"]),
                normalized_url=str(article["normalized_url"]),
                article_key=str(article["article_key"]),
                external_id=article.get("external_id"),
                published_at=article.get("published_at"),
            ),
            site_name=str(page["site_name"]),
            page_name=str(page["page_name"]),
            bot_profile=str(row["bot_profile"]),
            target_chat_id=str(row["target_chat_id"]),
            status=DeliveryStatus(row["status"]),
            attempt_count=int(row.get("attempt_count", 0)),
            delivery_generation=int(row.get("delivery_generation", 1)),
        )

    def get_due_deliveries(self, delivery_id: str | None = None) -> list[DeliveryWorkItem]:
        query = (
            self.client.table("deliveries")
            .select(
                "id,bot_profile,target_chat_id,status,attempt_count,delivery_generation,"
                "articles(id,source_page_id,title,normalized_url,article_key,external_id,published_at,"
                "source_pages(site_name,page_name))"
            )
            .in_("status", ["pending", "retry_requested", "failed"])
        )
        if not delivery_id:
            now = datetime.now(UTC).isoformat()
            query = query.or_(f"next_retry_at.is.null,next_retry_at.lte.{now}")
        if delivery_id:
            query = query.eq("id", delivery_id)
        response = query.execute()
        return [self._to_work_item(cast(dict[str, Any], row)) for row in response.data or []]

    def claim_delivery(self, delivery_id: str) -> DeliveryClaim | None:
        response = self.client.rpc("claim_delivery", {"p_delivery_id": delivery_id}).execute()
        if not response.data:
            return None
        row = self._first(response.data)
        return DeliveryClaim(str(row["delivery_id"]), int(row["attempt_number"]))

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
        self.client.table("delivery_attempts").insert(
            {
                "delivery_id": delivery_id,
                "attempt_number": attempt_number,
                "status": status,
                "http_status": http_status,
                "error_code": error_code,
                "error_message": error_message,
                "response_time_ms": response_time_ms,
            }
        ).execute()

    def mark_delivery_sent(self, delivery_id: str, message_id: str, http_status: int) -> None:
        self.client.rpc(
            "mark_delivery_sent",
            {
                "p_delivery_id": delivery_id,
                "p_telegram_message_id": message_id,
                "p_http_status": http_status,
            },
        ).execute()

    def mark_delivery_failed(
        self,
        delivery_id: str,
        status: str,
        error_code: str,
        error_message: str,
        next_retry_at: datetime | None,
        http_status: int | None,
    ) -> None:
        self.client.rpc(
            "mark_delivery_failed",
            {
                "p_delivery_id": delivery_id,
                "p_status": status,
                "p_error_code": error_code,
                "p_error_message": error_message,
                "p_next_retry_at": next_retry_at.isoformat() if next_retry_at else None,
                "p_http_status": http_status,
            },
        ).execute()

    def recover_stale_deliveries(self) -> int:
        response = self.client.rpc("recover_stale_deliveries").execute()
        return int(cast(int, response.data or 0))
