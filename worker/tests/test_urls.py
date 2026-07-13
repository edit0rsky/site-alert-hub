from site_alert.utils.urls import make_article_key, normalize_url


def test_normalize_url_removes_tracking_and_fragment() -> None:
    actual = normalize_url(
        "/post/123/?utm_source=instagram&b=2&a=1#comments",
        "https://Example.com/base/",
    )
    assert actual == "https://example.com/post/123?a=1&b=2"


def test_article_key_is_stable_for_same_identity() -> None:
    first = make_article_key("board", "title", "https://example.com/a", "  123 ")
    second = make_article_key("board", "other title", "https://example.com/b", "123")
    assert first == second
