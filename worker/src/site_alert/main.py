from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

from site_alert.clients.http import BrowserFetcher, HttpFetcher
from site_alert.config import AppSettings, active_source_pages, load_source_pages
from site_alert.parsers.base import default_registry
from site_alert.repositories.supabase import SupabaseRepository
from site_alert.services.pipeline import Pipeline
from site_alert.utils.logging import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Site Alert Hub worker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    crawl = subparsers.add_parser("crawl", help="crawl active boards and deliver due notifications")
    crawl.add_argument("--source-page-id")
    crawl.add_argument("--dry-run", action="store_true")
    crawl.add_argument("--baseline", action="store_true")
    crawl.add_argument("--delivery-id")
    baseline = subparsers.add_parser(
        "baseline", help="store the current board list without notifying"
    )
    baseline.add_argument("--source-page-id", required=True)
    retry = subparsers.add_parser("retry", help="retry one delivery on the next worker run")
    retry.add_argument("--delivery-id", required=True)
    return parser


def build_pipeline(settings: AppSettings) -> Pipeline:
    pages = active_source_pages(load_source_pages(settings.source_pages_config))
    repository = SupabaseRepository(settings.supabase_url, settings.supabase_service_role_key)
    browser_fetcher = (
        BrowserFetcher(settings.robots_timeout_seconds)
        if any(page.render_js for page in pages)
        else None
    )
    return Pipeline(
        settings=settings,
        pages=pages,
        repository=repository,
        fetcher=HttpFetcher(settings.robots_timeout_seconds),
        parsers=default_registry(),
        browser_fetcher=browser_fetcher,
    )


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    settings = AppSettings()  # type: ignore[call-arg]
    pipeline = build_pipeline(settings)
    try:
        if args.command == "baseline":
            pipeline.run(
                source_page_id=args.source_page_id,
                force_baseline=True,
                trigger_type="manual_baseline",
            )
        elif args.command == "retry":
            pipeline.run(delivery_id=args.delivery_id, trigger_type="manual_retry")
        else:
            pipeline.run(
                source_page_id=args.source_page_id,
                dry_run=args.dry_run,
                force_baseline=args.baseline,
                delivery_id=args.delivery_id,
                trigger_type="workflow_dispatch"
                if os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch"
                else "schedule",
            )
    finally:
        pipeline.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
