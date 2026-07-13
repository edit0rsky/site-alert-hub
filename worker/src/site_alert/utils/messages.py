from __future__ import annotations

import html

from site_alert.models import DeliveryWorkItem


def telegram_message(item: DeliveryWorkItem) -> str:
    emoji = "☕" if item.bot_profile == "cafe" else "🏛️"
    return (
        f"{emoji} <b>새로운 게시글</b>\n\n"
        f"<b>사이트:</b> {html.escape(item.site_name)}\n"
        f"<b>게시판:</b> {html.escape(item.page_name)}\n"
        f"<b>제목:</b> {html.escape(item.article.title)}\n\n"
        f"🔗 {html.escape(item.article.normalized_url, quote=True)}"
    )


def telegram_reply_markup(url: str) -> dict[str, list[list[dict[str, str]]]]:
    return {"inline_keyboard": [[{"text": "게시글 바로가기", "url": url}]]}
