"""Batch utilities to drive downloads from loot_report_scraper outputs."""
from __future__ import annotations

import argparse
import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import detect, errors, logs, platforms, runs, session_store, signins
from .awake import keep_awake
from .core import Downloader
from .history import History
from .runner import DEFAULT_PARALLEL, Job, Runner
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
    last_run_pointer: Optional[Path] = None,
    stay_awake: bool = False,
    parallel: int = DEFAULT_PARALLEL,
    on_event=None,
) -> Dict[str, object]:
    """Download items. With a history, links saved before are recorded as already saved, unless force.

    last_run_pointer: record the run there so it can be resumed if it stops.
    stay_awake: hold off system sleep while downloading.
    parallel: downloads at once (1 to 6); one per platform at a time, with polite pauses.
    on_event: receives runner events (progress, waits) from worker threads.
    """
    logger = logger or logging.getLogger('multidownloader.batch')
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    items = list(items)

    if downloader is None:
        downloader = Downloader(str(out_dir), logger=logger)
    # a dry run only plans; a real run records every link in a manifest
    run = None if dry_run else Run(out_dir, history=history, force=force, last_run_pointer=last_run_pointer)
    if run:
        run.plan({'url': item.url, 'handler': detect_handler(item.source_hint, item.url), 'hint': item.source_hint}
                 for item in items)
    with keep_awake(stay_awake and run is not None):
        return _execute(items, run, downloader, logger, limit=limit, per_source_limit=per_source_limit,
                        dry_run=dry_run, include_title=include_title, parallel=parallel, on_event=on_event)


def _execute(items, run, downloader, logger, *, limit, per_source_limit, dry_run, include_title, parallel,
             on_event):
    counts: Dict[str, int] = {}
    attempted = 0
    completed: List[Tuple[str, str]] = []
    already: List[Tuple[str, str]] = []
    skipped: List[Tuple[str, str, str]] = []
    errors: List[Tuple[str, str, str]] = []
    seen = set()
    jobs: List[Job] = []

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

        jobs.append(Job(item.url, handler, opts, item.source_hint))

    stopped = False
    if run and jobs:
        runner = Runner(run, downloader, max_parallel=parallel, on_event=on_event)
        for result in runner.run(jobs):
            handler = next((j.handler for j in jobs if j.url == result.input_url), '')
            if result.status == 'saved':
                completed.append((handler, result.input_url))
                logger.info('Saved %s: %s file(s)', result.input_url, len(result.files))
            elif result.status == 'already_saved':
                already.append((handler, result.input_url))
                logger.info('Already saved %s on %s', result.input_url,
                            (result.previous or {}).get('saved_at', '?')[:10])
            elif result.status == 'stopped':
                logger.warning('Stopped %s', result.input_url)
            else:
                logger.error('Not saved %s: %s', result.input_url, result.error_message)
                logger.debug('Details for %s: %s', result.input_url, result.error_detail)
                errors.append((handler, result.input_url, result.error_message or ''))
        stopped = runner.stopped

    if run:
        run.finish()
    return {
        'stopped': stopped,
        'attempted': attempted,
        'completed': completed,
        'already_saved': already,
        'skipped': skipped,
        'errors': errors,
        'run_dir': str(run.dir) if run else None,
        'results': run.results if run else [],
    }


def next_step(info: errors.ErrorInfo, platform_key: str = '') -> str:
    """The command or step that fixes a group of failures, for the CLI."""
    key = platform_key or 'PLATFORM'
    if info.action == errors.FIX_SIGNIN:
        return f'Run: multidownloader-signin import {key} --browser firefox'
    if info.action == errors.RECONNECT:
        return 'Reconnect Google Drive in Sign-ins (window), Connect with your own Google Cloud project.'
    if info.action == errors.OPEN_PLATFORM:
        return 'Open the site in your browser, then run the list again.'
    if info.action == errors.HOW_TO_UPDATE:
        return 'Update: python -m pip install -U yt-dlp gallery-dl instaloader gdown'
    if info.action == errors.HOW_TO_FIX:
        if 'Deno' in info.message:
            return 'Install Deno: winget install DenoLand.Deno (or see docs.deno.com)'
        if 'gallery-dl' in info.message:
            return 'Install gallery-dl: python -m pip install gallery-dl'
        if 'ffmpeg' in info.message:
            return 'Install ffmpeg: winget install Gyan.FFmpeg (or your package manager)'
        return 'See the README, Troubleshooting.'
    if info.action == errors.SLOW_DOWN:
        return 'Wait an hour, then run again with fewer links.'
    if info.action in (errors.WAIT, errors.RETRY):
        return 'Run the same list again later; saved links are skipped.'
    if info.action == errors.CHANGE_FOLDER:
        return 'Choose another folder with --out-dir.'
    return ''


def report_failures(results, logger) -> None:
    """Failures grouped by cause, the largest group last so it is what stays on screen."""
    failed = [r for r in results if r.status == 'failed']
    if not failed:
        return
    infos = [(r.input_url, errors.ErrorInfo(r.error_kind or errors.UNKNOWN, r.error_message or '',
                                            r.error_action, r.error_detail or '')) for r in failed]
    platform_of = {r.input_url: r.platform or '' for r in failed}
    for info, urls in reversed(errors.group(infos)):
        noun = 'link' if len(urls) == 1 else 'links'
        step = next_step(info, platform_of[urls[0]])
        logger.error('%s %s: %s%s', len(urls), noun, info.message, f' {step}' if step else '')


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Download items listed by loot_report_scraper outputs.')
    parser.add_argument('input', type=Path, nargs='?',
                        help='Path to grouped_by_source.json or grouped_by_source.csv')
    parser.add_argument('--format', choices=['json', 'csv'], help='Override input format')
    parser.add_argument('--out-dir', type=Path, default=DEFAULT_OUTPUT_DIR, help='Download destination directory')
    parser.add_argument('--limit', type=int, help='Maximum total items to attempt')
    parser.add_argument('--per-source-limit', type=int, help='Maximum items per source to attempt')
    parser.add_argument('--dry-run', action='store_true', help='Only print actions without downloading')
    parser.add_argument('--include-title', action='store_true',
                        help='Add the post title (first 60 characters) to file names')
    parser.add_argument('--resume', action='store_true',
                        help='Finish the last run that stopped before it was done, in the same output folder')
    parser.add_argument('--parallel', type=int, default=DEFAULT_PARALLEL, metavar='N',
                        help='Downloads at once, 1 to 6 (default 3); always one at a time per platform')
    parser.add_argument('--no-keep-awake', action='store_true',
                        help='Let the computer sleep during the run')
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

    unfinished = runs.last_unfinished_run()
    out_dir = args.out_dir
    if args.resume:
        if not unfinished:
            logger.error('There is no unfinished run to resume.')
            return 2
        logger.info('%s Resuming it in %s.', unfinished.message(), unfinished.output_root)
        manifest = [ManifestItem(source_hint=link.get('hint') or '', url=link['url']) for link in unfinished.remaining]
        out_dir = unfinished.output_root
    elif args.input is None:
        parser.error('give a manifest file, or --resume')
    else:
        if unfinished:
            logger.warning('%s Run with --resume to finish it.', unfinished.message())
        manifest = load_manifest(args.input, fmt=args.format)
    unique, duplicates = detect.unique_urls(item.url for item in manifest)
    logger.info(detect.summarize(unique, duplicates))
    # informational only: the run goes ahead without a sign-in
    for platform in signins.platforms_missing_signin(unique):
        logger.warning('No %s sign-in, so some links may fail. Run: multidownloader-signin import %s --browser firefox',
                       platform.name, platform.key)
    result = execute_batch(
        manifest,
        out_dir,
        limit=args.limit,
        per_source_limit=args.per_source_limit,
        dry_run=args.dry_run,
        logger=logger,
        history=None if args.dry_run else History(),
        force=args.force,
        include_title=args.include_title,
        last_run_pointer=None if args.dry_run else runs.default_last_run_pointer(),
        stay_awake=not args.no_keep_awake,
        parallel=args.parallel,
    )
    if args.resume and unfinished and result['run_dir']:
        unfinished.close('resumed', by_run=Path(result['run_dir']).name)

    logger.info('Attempted: %s, completed: %s, already saved: %s, skipped: %s, errors: %s', result['attempted'],
                len(result['completed']), len(result['already_saved']), len(result['skipped']), len(result['errors']))
    if result['run_dir']:
        logger.info('Report: %s', Path(result['run_dir']) / 'manifest.csv')

    if result['errors']:
        report_failures(result['results'], logger)
        logger.error('Details are in %s', logs.log_path())
    if result.get('stopped'):
        logger.warning('Stopped. Run with --resume to finish the links that were not done.')
        return 130
    if result['errors']:
        return 1
    unsupported = sum(1 for *_rest, reason in result['skipped'] if reason == 'unsupported')
    not_attempted = sum(1 for *_rest, reason in result['skipped'] if reason in ('unsupported', 'duplicate'))
    if result['attempted'] == 0 and not_attempted == len(result['skipped']):
        logger.error('Nothing was attempted: %s link(s) are from unsupported sites.', unsupported)
        return 2
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
