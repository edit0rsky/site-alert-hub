from pathlib import Path

import pytest

from site_alert.config import SourcePageConfig
from site_alert.parsers.base import GenericHTMLParser, ParserSelectorMismatch

FIXTURE = Path(__file__).parent / "fixtures" / "board.html"


def page_config() -> SourcePageConfig:
    return SourcePageConfig.model_validate(
        {
            "id": "example",
            "site_name": "예시",
            "page_name": "공지",
            "category": "government",
            "bot_profile": "government",
            "list_url": "https://example.com/board",
            "base_url": "https://example.com",
            "selectors": {
                "item": "table tbody tr",
                "title": ".p-subject a",
                "link": ".p-subject a",
                "date": ".date",
                "external_id": "td:nth-child(1)",
            },
        }
    )


def test_generic_parser_extracts_title_link_date_and_id() -> None:
    candidates = GenericHTMLParser().parse(FIXTURE.read_text(), page_config())
    assert len(candidates) == 2
    assert candidates[0].title == "첫 번째 공지"
    assert candidates[0].normalized_url == "https://example.com/notice/42"
    assert candidates[0].external_id == "42"
    assert candidates[1].original_url == "https://example.com/notice/41#top"


def test_zero_items_is_a_parser_error() -> None:
    page = page_config()
    page.selectors.item = ".missing"
    with pytest.raises(ParserSelectorMismatch):
        GenericHTMLParser().parse(FIXTURE.read_text(), page)
