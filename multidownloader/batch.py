"""Batch utilities to drive downloads from loot_report_scraper outputs."""
from __future__ import annotations

import argparse
import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import detect, logs, platforms, session_store, signins
from .core import Downloader
from .history import History
from .runs import Run

SUPPORTED_SOURCES = {p.handler for p in platforms.PLATFORMS}

DEFAULT_OUTPUT_DIR = Path('downloads')


@dataclass
class ManifestItem:
    source_hint: str
    url: str


def normalize_url(url: str) -> str:
    return url.strip()


def detect_handler(source_hint: str, url: str) -> Optional[str]:
    """Return the Downloader handler name for a given item or None if unsupported.

    The URL decides. The scraper's source hint is only used when the host is
    unknown (for example a shortened link).
    """
    platform = detect.detect_platform(url, source_hint)
    return platform.handler if platform else None


def match_host(url: str) -> Optional[str]:
    platform = detect.platform_for_url(url)
    return platform.handler if platform else None


def match_alias(value: str) -> Optional[str]:
    platform = detect.platform_for_hint(value)
    return platform.handler if platform else None


def load_manifest(path: Path, fmt: Optional[str] = None) -> List[ManifestItem]:
    fmt = (fmt or path.suffix.lstrip('.')).lower()
    if fmt not in {'json', 'csv'}:
        raise ValueError(f'Unsupported manifest format: {fmt}')
    if fmt == 'json':
        return list(_from_json(path))
    return list(_from_csv(path))


def _from_json(path: Path) -> Iterable[ManifestItem]:
    data = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(data, dict):
        for source, urls in data.items():
            if isinstance(urls, dict):
                urls = urls.get('items') or []
            if not isinstance(urls, Sequence):
                continue
            for url in urls:
                if not url:
                    continue
                yield ManifestItem(source_hint=str(source), url=normalize_url(str(url)))
    elif isinstance(data, Sequence):
        for item in data:
            if isinstance(item, dict) and 'url' in item:
                yield ManifestItem(source_hint=str(item.get('source') or ''), url=normalize_url(str(item['url'])))
    else:
        raise ValueError('JSON manifest must be an object or array')


def _from_csv(path: Path) -> Iterable[ManifestItem]:
    with path.open(newline='', encoding='utf-8') as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            source = row.get('source') or row.get('Source') or ''
            items_field = row.get('items_comma_separated') or row.get('items') or ''
            if not items_field:
                continue
            # items are stored as comma-separated URLs; tolerate stray spaces
            parts = [p.strip() for p in items_field.split(',') if p.strip()]
            for part in parts:
                yield ManifestItem(source_hint=source, url=normalize_url(part))


def execute_batch(
    items: Iterable[ManifestItem],
    out_dir: Path,
    *,
    limit: Optional[int] = None,
    per_source_limit: Optional[int] = None,
    dry_run: bool = False,
    logger: Optional[logging.Logger] = None,
    downloader: Optional[Downloader] = None,
    history: Optional[History] = None,
    force: bool = False,
    include_title: bool = False,
) -> Dict[str, object]:
    """Download items. With a history, links saved before are recorded as already saved, unless force."""
    logger = logger or logging.getLogger('multidownloader.batch')
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if downloader is None:
        downloader = Downloader(str(out_dir), logger=logger)
    # a dry run only plans; a real run records every link in a manifest
    run = None if dry_run else Run(out_dir, history=history, force=force)

    counts: Dict[str, int] = {}
    attempted = 0
    completed: List[Tuple[str, str]] = []
    already: List[Tuple[str, str]] = []
    skipped: List[Tuple[str, str, str]] = []
    errors: List[Tuple[str, str, str]] = []
    seen = set()

    def skip(item, reason, handler=None):
        skipped.append((item.source_hint, item.url, reason))
        if run:
            run.skip(item.url, reason, handler=handler, hint=item.source_hint)

    for item in items:
        # the same post shared in different link formats is downloaded once
        key = detect.normalize_url(item.url)
        if key in seen:
            skip(item, 'duplicate')
            continue
        seen.add(key)

        handler = detect_handler(item.source_hint, item.url)
        if not handler or handler not in SUPPORTED_SOURCES:
            skip(item, 'unsupported')
            continue

        if limit is not None and attempted >= limit:
            skip(item, 'global-limit', handler)
            continue

        used = counts.get(handler, 0)
        if per_source_limit is not None and used >= per_source_limit:
            skip(item, 'per-source-limit', handler)
            continue

        counts[handler] = used + 1
        attempted += 1

        logger.info('Processing %s via %s', item.url, handler)
        if dry_run:
            completed.append((handler, item.url))
            continue

        opts = {'include_title': True} if include_title else {}
        if handler == 'Instagram':
            opts['auth'] = 'auto'
        elif handler != 'Google Drive':
            # yt-dlp based handlers reuse session cookies when available
            opts['use_session'] = True

        result = run.download(downloader, handler, item.url, opts, hint=item.source_hint)
        if result.status == 'saved':
            completed.append((handler, item.url))
            logger.info('Saved %s: %s file(s)', item.url, len(result.files))
        elif result.status == 'already_saved':
            already.append((handler, item.url))
            logger.info('Already saved %s on %s', item.url, (result.previous or {}).get('saved_at', '?')[:10])
        else:
            logger.error('Download failed for %s (%s): %s', item.url, handler, result.error_message)
            errors.append((handler, item.url, result.error_message or ''))

    if run:
        run.finish()
    return {
        'attempted': attempted,
        'completed': completed,
        'already_saved': already,
        'skipped': skipped,
        'errors': errors,
        'run_dir': str(run.dir) if run else None,
        'results': run.results if run else [],
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Download items listed by loot_report_scraper outputs.')
    parser.add_argument('input', type=Path, help='Path to grouped_by_source.json or grouped_by_source.csv')
    parser.add_argument('--format', choices=['json', 'csv'], help='Override input format')
    parser.add_argument('--out-dir', type=Path, default=DEFAULT_OUTPUT_DIR, help='Download destination directory')
    parser.add_argument('--limit', type=int, help='Maximum total items to attempt')
    parser.add_argument('--per-source-limit', type=int, help='Maximum items per source to attempt')
    parser.add_argument('--dry-run', action='store_true', help='Only print actions without downloading')
    parser.add_argument('--include-title', action='store_true',
                        help='Add the post title (first 60 characters) to file names')
    parser.add_argument('--force', action='store_true',
                        help='Download links again even if an earlier run saved them')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logs.setup(logging.DEBUG if args.verbose else logging.INFO)
    session_store.migrate_legacy_sessions()
    logger = logging.getLogger('multidownloader.batch')

    manifest = load_manifest(args.input, fmt=args.format)
    unique, duplicates = detect.unique_urls(item.url for item in manifest)
    logger.info(detect.summarize(unique, duplicates))
    # informational only: the run goes ahead without a sign-in
    for platform in signins.platforms_missing_signin(unique):
        logger.warning('No %s sign-in, so some links may fail. Run: multidownloader-signin import %s --browser firefox',
                       platform.name, platform.key)
    result = execute_batch(
        manifest,
        args.out_dir,
        limit=args.limit,
        per_source_limit=args.per_source_limit,
        dry_run=args.dry_run,
        logger=logger,
        history=None if args.dry_run else History(),
        force=args.force,
        include_title=args.include_title,
    )

    logger.info('Attempted: %s, completed: %s, already saved: %s, skipped: %s, errors: %s', result['attempted'],
                len(result['completed']), len(result['already_saved']), len(result['skipped']), len(result['errors']))
    if result['run_dir']:
        logger.info('Report: %s', Path(result['run_dir']) / 'manifest.csv')

    if result['errors']:
        logger.error('Some downloads failed. See log for details.')
        return 1
    unsupported = sum(1 for *_rest, reason in result['skipped'] if reason == 'unsupported')
    not_attempted = sum(1 for *_rest, reason in result['skipped'] if reason in ('unsupported', 'duplicate'))
    if result['attempted'] == 0 and not_attempted == len(result['skipped']):
        logger.error('Nothing was attempted: %s link(s) are from unsupported sites.', unsupported)
        return 2
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
