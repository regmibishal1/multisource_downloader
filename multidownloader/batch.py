"""Batch utilities to drive downloads from loot_report_scraper outputs."""
from __future__ import annotations

import argparse
import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from .core import Downloader

_log = logging.getLogger('multidownloader.batch')

SUPPORTED_SOURCES = {
    'Google Drive',
    'Instagram',
    'TikTok',
    'Threads',
    'Twitter',
    'Reddit',
    'Facebook',
    'YouTube',
}

# Hostnames are matched exactly or as a dot-suffix (www.x.com matches x.com,
# dropbox.com does not).
HOST_MAP: Tuple[Tuple[str, str], ...] = (
    ('drive.google.com', 'Google Drive'),
    ('docs.google.com', 'Google Drive'),
    ('googleusercontent.com', 'Google Drive'),
    ('instagram.com', 'Instagram'),
    ('instagr.am', 'Instagram'),
    ('ddinstagram.com', 'Instagram'),
    ('threads.net', 'Threads'),
    ('threads.com', 'Threads'),
    ('tiktok.com', 'TikTok'),
    ('douyin.com', 'TikTok'),
    ('twitter.com', 'Twitter'),
    ('x.com', 'Twitter'),
    ('fxtwitter.com', 'Twitter'),
    ('vxtwitter.com', 'Twitter'),
    ('fixupx.com', 'Twitter'),
    ('fixvx.com', 'Twitter'),
    ('reddit.com', 'Reddit'),
    ('redd.it', 'Reddit'),
    ('facebook.com', 'Facebook'),
    ('fb.com', 'Facebook'),
    ('fb.watch', 'Facebook'),
    ('fbcdn.net', 'Facebook'),
    ('youtube.com', 'YouTube'),
    ('youtu.be', 'YouTube'),
    ('youtube-nocookie.com', 'YouTube'),
    ('youtubekids.com', 'YouTube'),
)

# Source hints come from the scraper's group names ("twitter", "Google Drive"),
# not from URLs, so a substring match is fine here.
HINT_ALIASES: Tuple[Tuple[str, str], ...] = (
    ('googledrive', 'Google Drive'),
    ('google drive', 'Google Drive'),
    ('gdrive', 'Google Drive'),
    ('instagram', 'Instagram'),
    ('threads', 'Threads'),
    ('tiktok', 'TikTok'),
    ('douyin', 'TikTok'),
    ('twitter', 'Twitter'),
    ('reddit', 'Reddit'),
    ('facebook', 'Facebook'),
    ('youtube', 'YouTube'),
)

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
    from_url = match_host(url)
    from_hint = match_alias(source_hint)
    if from_url:
        if from_hint and from_hint != from_url:
            _log.warning('Source hint %r disagrees with URL %s; using %s', source_hint, url, from_url)
        return from_url
    return from_hint


def match_host(url: str) -> Optional[str]:
    try:
        host = (urlparse(url).hostname or '').lower().rstrip('.')
    except ValueError:
        return None
    if not host:
        return None
    for domain, handler in HOST_MAP:
        if host == domain or host.endswith('.' + domain):
            return handler
    return None


def match_alias(value: str) -> Optional[str]:
    value = (value or '').strip().lower()
    if '.' in value and ' ' not in value:
        # domain-shaped hint such as "x.com": match it like a host
        return match_host(value if '//' in value else 'https://' + value)
    for alias, handler in HINT_ALIASES:
        if alias in value:
            return handler
    return None


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
) -> Dict[str, object]:
    logger = logger or logging.getLogger('multidownloader.batch')
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if downloader is None:
        downloader = Downloader(str(out_dir), logger=logger)

    counts: Dict[str, int] = {}
    attempted = 0
    completed: List[Tuple[str, str]] = []
    skipped: List[Tuple[str, str, str]] = []
    errors: List[Tuple[str, str, str]] = []

    for item in items:
        handler = detect_handler(item.source_hint, item.url)
        if not handler or handler not in SUPPORTED_SOURCES:
            skipped.append((item.source_hint, item.url, 'unsupported'))
            continue

        if limit is not None and attempted >= limit:
            skipped.append((item.source_hint, item.url, 'global-limit'))
            continue

        used = counts.get(handler, 0)
        if per_source_limit is not None and used >= per_source_limit:
            skipped.append((item.source_hint, item.url, 'per-source-limit'))
            continue

        counts[handler] = used + 1
        attempted += 1

        logger.info('Processing %s via %s', item.url, handler)
        if dry_run:
            completed.append((handler, item.url))
            continue

        opts = {}
        if handler == 'Instagram':
            opts['auth'] = 'auto'
        elif handler != 'Google Drive':
            # yt-dlp based handlers reuse session cookies when available
            opts['use_session'] = True

        try:
            downloader.download(handler, item.url, opts)
            completed.append((handler, item.url))
        except Exception as exc:  # pragma: no cover - delegates to handlers
            logger.error('Download failed for %s (%s): %s', item.url, handler, exc)
            errors.append((handler, item.url, str(exc)))

    return {
        'attempted': attempted,
        'completed': completed,
        'skipped': skipped,
        'errors': errors,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Download items listed by loot_report_scraper outputs.')
    parser.add_argument('input', type=Path, help='Path to grouped_by_source.json or grouped_by_source.csv')
    parser.add_argument('--format', choices=['json', 'csv'], help='Override input format')
    parser.add_argument('--out-dir', type=Path, default=DEFAULT_OUTPUT_DIR, help='Download destination directory')
    parser.add_argument('--limit', type=int, help='Maximum total items to attempt')
    parser.add_argument('--per-source-limit', type=int, help='Maximum items per source to attempt')
    parser.add_argument('--dry-run', action='store_true', help='Only print actions without downloading')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    logger = logging.getLogger('multidownloader.batch')

    manifest = load_manifest(args.input, fmt=args.format)
    result = execute_batch(
        manifest,
        args.out_dir,
        limit=args.limit,
        per_source_limit=args.per_source_limit,
        dry_run=args.dry_run,
        logger=logger,
    )

    logger.info('Attempted: %s, completed: %s, skipped: %s, errors: %s', result['attempted'], len(result['completed']), len(result['skipped']), len(result['errors']))

    if result['errors']:
        logger.error('Some downloads failed. See log for details.')
        return 1
    unsupported = sum(1 for *_rest, reason in result['skipped'] if reason == 'unsupported')
    if result['attempted'] == 0 and unsupported == len(result['skipped']):
        logger.error('Nothing was attempted: %s link(s) are from unsupported sites.', unsupported)
        return 2
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
