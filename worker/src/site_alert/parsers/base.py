from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import ClassVar
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from soupsieve import match as css_match

from site_alert.config import SourcePageConfig
from site_alert.models import ArticleCandidate
from site_alert.utils.urls import make_article_key, normalize_url


class ParserSelectorMismatch(RuntimeError):
    """Raised when a configured list selector no longer matches the page."""

    code = "parser_selector_mismatch"


class BoardParser(ABC):
    key: ClassVar[str]

    @abstractmethod
    def parse(self, html_text: str, page: SourcePageConfig) -> list[ArticleCandidate]:
        raise NotImplementedError


class GenericHTMLParser(BoardParser):
    key = "generic_html"

    @staticmethod
    def _select_self_or_descendant(node: Tag, selector: str) -> Tag | None:
        if css_match(selector, node):
            return node
        return node.select_one(selector)

    @staticmethod
    def _published_text(node: Tag | None) -> str | None:
        if not node:
            return None
        text = node.get_text(" ", strip=True)
        match = re.search(
            r"\b\d{4}[./-]\d{1,2}[./-]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\b", text
        )
        return match.group(0) if match else text or None

    def parse(self, html_text: str, page: SourcePageConfig) -> list[ArticleCandidate]:
        soup = BeautifulSoup(html_text, "html.parser")
        items = soup.select(page.selectors.item)
        if not items:
            raise ParserSelectorMismatch(f"selector returned zero items: {page.selectors.item}")

        candidates: list[ArticleCandidate] = []
        for item in items[: page.crawl.max_items]:
            title_node = self._select_self_or_descendant(item, page.selectors.title)
            link_node = self._select_self_or_descendant(item, page.selectors.link)
            if not title_node or not link_node:
                continue
            title = title_node.get_text(" ", strip=True)
            href = link_node.get("href") if isinstance(link_node, Tag) else None
            if not title or not href:
                continue
            original_url = urljoin(page.base_url, str(href).strip())
            try:
                normalized_url = normalize_url(original_url, page.base_url)
            except ValueError:
                continue
            date_node = item.select_one(page.selectors.date) if page.selectors.date else None
            id_node = (
                item.select_one(page.selectors.external_id) if page.selectors.external_id else None
            )
            published_at = self._published_text(date_node)
            external_id = id_node.get_text(" ", strip=True) if id_node else None
            candidates.append(
                ArticleCandidate(
                    source_page_id=page.id,
                    title=title,
                    original_url=original_url,
                    normalized_url=normalized_url,
                    article_key=make_article_key(page.id, title, normalized_url, external_id),
                    external_id=external_id or None,
                    published_at=published_at or None,
                )
            )
        return candidates


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: dict[str, BoardParser] = {}

    def register(self, parser: BoardParser) -> None:
        self._parsers[parser.key] = parser

    def get(self, key: str) -> BoardParser:
        try:
            return self._parsers[key]
        except KeyError as exc:
            raise ValueError(f"unknown parser: {key}") from exc


def default_registry() -> ParserRegistry:
    registry = ParserRegistry()
    registry.register(GenericHTMLParser())
    return registry
