from __future__ import annotations

import hashlib
import html
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

REMOVED_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
}


def normalize_url(value: str, base_url: str | None = None) -> str:
    raw = html.unescape(value).strip()
    if base_url:
        raw = urljoin(base_url, raw)
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"invalid article URL: {value}")
    hostname = parts.hostname.lower() if parts.hostname else ""
    port = parts.port
    netloc = hostname
    if port and not (
        (parts.scheme == "http" and port == 80) or (parts.scheme == "https" and port == 443)
    ):
        netloc = f"{hostname}:{port}"
    path = quote(unquote(parts.path or "/"), safe="/%:@-._~!$&'()*+,;=")
    if path != "/":
        path = path.rstrip("/") or "/"
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in REMOVED_QUERY_KEYS and not key.lower().startswith("utm_")
    ]
    query_pairs.sort()
    query = urlencode(query_pairs, doseq=True, safe="/:@-._~!$&'()*+,;=")
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def make_article_key(
    source_page_id: str, title: str, normalized_url: str, external_id: str | None
) -> str:
    identity = external_id.strip() if external_id and external_id.strip() else normalized_url
    if not identity:
        identity = title.strip()
    digest = hashlib.sha256(f"{source_page_id}\x00{identity}".encode()).hexdigest()
    return digest
