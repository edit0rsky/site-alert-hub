from site_alert.models import ArticleRecord, DeliveryStatus, DeliveryWorkItem
from site_alert.utils.messages import telegram_message


def test_telegram_message_escapes_html() -> None:
    item = DeliveryWorkItem(
        id="delivery",
        article=ArticleRecord(
            "article", "board", "<긴급> & 공지", "https://example.com/a?x=1&y=2", "key"
        ),
        site_name="<서울시>",
        page_name="공지 & 안내",
        bot_profile="government",
        target_chat_id="@channel",
        status=DeliveryStatus.PENDING,
    )
    message = telegram_message(item)
    assert "&lt;긴급&gt; &amp; 공지" in message
    assert "<서울시>" not in message
    assert "https://example.com/a?x=1&amp;y=2" in message
