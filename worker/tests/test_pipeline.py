from __future__ import annotations

from dataclasses import dataclass

from site_alert.clients.http import FetchResult
from site_alert.clients.telegram import TelegramError
from site_alert.config import AppSettings, SourcePageConfig
from site_alert.models import DeliveryStatus, TelegramSendResult
from site_alert.parsers.base import default_registry
from site_alert.repositories.memory import InMemoryRepository
from site_alert.services.pipeline import Pipeline

HTML = """
<table><tbody>
<tr><td>1</td><td class='title'><a href='/post/1'>기존 글</a></td>
<td class='date'>2026-07-01</td></tr>
</tbody></table>
"""
NEW_HTML = HTML.replace(
    "</tbody>",
    "<tr><td>2</td><td class='title'><a href='/post/2'>새 글</a></td>"
    "<td class='date'>2026-07-13</td></tr></tbody>",
)


def make_page() -> SourcePageConfig:
    return SourcePageConfig.model_validate(
        {
            "id": "board",
            "site_name": "사이트",
            "page_name": "게시판",
            "category": "government",
            "bot_profile": "government",
            "list_url": "https://example.com/list",
            "base_url": "https://example.com",
            "selectors": {
                "item": "table tr",
                "title": ".title a",
                "link": ".title a",
                "date": ".date",
            },
        }
    )


def make_settings() -> AppSettings:
    return AppSettings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-secret",
        telegram_bots_json='{"government":{"token":"bot-secret","chat_id":"@channel"}}',
        delivery_retry_base_seconds=1,
    )


@dataclass
class FakeFetcher:
    html: str

    def fetch(self, url: str, request: object, max_retries: int) -> FetchResult:
        return FetchResult(self.html, 200, 1)

    def close(self) -> None:
        pass


class FakeTelegramClient:
    sent: list[str] = []
    should_fail = False

    def __init__(self, profile: object, timeout_seconds: float) -> None:
        pass

    def close(self) -> None:
        pass

    def send_message(self, text: str, article_url: str) -> TelegramSendResult:
        if self.should_fail:
            raise TelegramError("temporary", "temporary failure", retryable=True)
        self.sent.append(article_url)
        return TelegramSendResult("123", 200)


def make_pipeline(repo: InMemoryRepository, fetcher: FakeFetcher) -> Pipeline:
    return Pipeline(make_settings(), [make_page()], repo, fetcher, default_registry())


def test_first_run_baselines_and_second_run_sends_only_new(monkeypatch) -> None:
    FakeTelegramClient.sent = []
    monkeypatch.setattr("site_alert.services.pipeline.TelegramClient", FakeTelegramClient)
    repo = InMemoryRepository()
    fetcher = FakeFetcher(HTML)
    pipeline = make_pipeline(repo, fetcher)
    pipeline.run()
    assert FakeTelegramClient.sent == []
    assert repo.pages["board"]["initialized"] is True

    fetcher.html = NEW_HTML
    pipeline.run()
    assert FakeTelegramClient.sent == ["https://example.com/post/2"]
    pipeline.run()
    assert FakeTelegramClient.sent == ["https://example.com/post/2"]


def test_concurrent_claim_allows_one_worker() -> None:
    from concurrent.futures import ThreadPoolExecutor

    repo = InMemoryRepository()
    repo.sync_source_pages([make_page()])
    pipeline = make_pipeline(repo, FakeFetcher(HTML))
    pipeline.run()
    article = next(iter(repo.articles.values()))
    delivery_id, _ = repo.ensure_delivery(article, make_page(), "@channel")
    repo.deliveries[delivery_id]["status"] = DeliveryStatus.PENDING
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(repo.claim_delivery, [delivery_id, delivery_id]))
    assert sum(claim is not None for claim in claims) == 1


def test_retry_failure_is_recorded(monkeypatch) -> None:
    FakeTelegramClient.sent = []
    FakeTelegramClient.should_fail = True
    monkeypatch.setattr("site_alert.services.pipeline.TelegramClient", FakeTelegramClient)
    repo = InMemoryRepository()
    fetcher = FakeFetcher(HTML)
    pipeline = make_pipeline(repo, fetcher)
    pipeline.run(force_baseline=True)
    fetcher.html = NEW_HTML
    pipeline.run()
    FakeTelegramClient.should_fail = False
    assert any(delivery["status"] == DeliveryStatus.FAILED for delivery in repo.deliveries.values())
