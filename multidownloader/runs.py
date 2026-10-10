"""One run: staging, hashing, and the run manifest (brief F10, F11, T5, T17).

Every link in a run ends with one recorded result. Each download goes into
its own staging folder, ``<root>/_staging/<run>_<job>/``. On success every
file is hashed (SHA-256) and moved into the output root at the same
relative path, never overwriting an existing file; the staging folder is
removed either way.

Per run, ``<root>/_runs/<run_id>/`` holds:

- manifest.jsonl: one JSON object per link, appended and flushed to disk as
  each link finishes, so a crash loses at most the link in progress.
- manifest.csv: the same, one row per link, written when the run ends.
- SHA256SUMS.txt: ``<sha256>  <path>`` for every saved file, paths relative to
  the output root, so ``sha256sum -c _runs/<run_id>/SHA256SUMS.txt`` run from
  the root checks them.

Times are UTC ISO 8601 with Z. Paths are relative to the output root with
forward slashes, so the folder can be moved or copied.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import secrets
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import __version__, detect, signins

_log = logging.getLogger(__name__)

RUNS_DIRNAME = '_runs'
STAGING_DIRNAME = '_staging'
STAGING_MAX_AGE_S = 24 * 3600
HASH_CHUNK = 1024 * 1024
# partial or helper files a backend may leave behind; never kept as media
_TEMP_SUFFIXES = ('.part', '.ytdl', '.tmp', '.temp')

BACKENDS = {
    'Instagram': 'instaloader',
    'Google Drive': 'gdown',
}

CSV_FIELDS = (
    'run_id', 'status', 'platform', 'input_url', 'normalized_url', 'backend', 'files', 'bytes', 'sha256',
    'error_kind', 'error_message', 'started_at', 'finished_at', 'app_version', 'backend_version',
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%SZ') + '_' + secrets.token_hex(2)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as fp:
        for chunk in iter(lambda: fp.read(HASH_CHUNK), b''):
            digest.update(chunk)
    return digest.hexdigest()


def backend_for(handler: str) -> str:
    return BACKENDS.get(handler, 'yt-dlp')


def backend_version(backend: str) -> Optional[str]:
    from importlib import metadata

    try:
        return f'{backend} {metadata.version(backend)}'
    except metadata.PackageNotFoundError:
        return None


def classify(message: str) -> str:
    """A first error kind for the manifest; the full taxonomy (T9) comes later."""
    if signins.looks_like_block(message):
        return 'rate_limited'
    if signins.looks_like_auth_error(message):
        return 'auth_required'
    lowered = (message or '').lower()
    if any(hint in lowered for hint in ('not found', '404', 'unavailable', 'has been removed', 'deleted')):
        return 'not_found'
    if any(hint in lowered for hint in ('timed out', 'timeout', 'connection', 'network', 'getaddrinfo')):
        return 'network'
    return 'unknown'


@dataclass
class FileRecord:
    path: str      # relative to the output root, forward slashes
    bytes: int
    sha256: str
    duplicate_of: Optional[str] = None  # an identical file was already at this path


@dataclass
class JobResult:
    run_id: str
    input_url: str
    normalized_url: str
    platform: Optional[str]
    backend: Optional[str]
    status: str                    # saved | already_saved | failed | skipped | stopped
    error_kind: Optional[str] = None
    error_message: Optional[str] = None
    error_detail: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    files: List[FileRecord] = field(default_factory=list)
    metadata_path: Optional[str] = None
    post: Optional[Dict] = None
    wayback_url: Optional[str] = None
    app_version: str = f'multisource-downloader {__version__}'
    backend_version: Optional[str] = None
    source_hint: Optional[str] = None
    skip_reason: Optional[str] = None
    previous: Optional[Dict] = None  # for already_saved: when and where it was saved before

    def to_dict(self) -> Dict:
        return asdict(self)


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _free_name(target: Path) -> Path:
    """target, or "name (2).ext", "name (3).ext"... for the first unused name."""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    n = 2
    while True:
        candidate = target.with_name(f'{stem} ({n}){suffix}')
        if not candidate.exists():
            return candidate
        n += 1


def clean_old_staging(root: Path, max_age: float = STAGING_MAX_AGE_S, now: Optional[float] = None) -> int:
    """Remove staging folders left by runs that stopped more than max_age ago."""
    staging = root / STAGING_DIRNAME
    if not staging.is_dir():
        return 0
    now = time.time() if now is None else now
    removed = 0
    for entry in staging.iterdir():
        try:
            if entry.is_dir() and now - entry.stat().st_mtime > max_age:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed



def _iso_from_ts(value) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def post_from_metadata(path: Path) -> Optional[Dict]:
    """Read id, uploader, post time and title from a metadata file a backend wrote."""
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if 'node' in data and isinstance(data['node'], dict):  # Instaloader
        node = data['node']
        captions = ((node.get('edge_media_to_caption') or {}).get('edges') or [{}])
        caption = ((captions[0] or {}).get('node') or {}).get('text') if captions else None
        return {'id': node.get('shortcode'), 'uploader': (node.get('owner') or {}).get('username'),
                'posted_at': _iso_from_ts(node.get('taken_at_timestamp')), 'title': caption}
    if path.name.endswith('.drive.json'):
        return {'id': data.get('id'), 'uploader': ', '.join(data.get('owners') or []) or None,
                'posted_at': data.get('modified'), 'title': data.get('name')}
    if 'extractor' in data or 'webpage_url' in data:  # yt-dlp
        posted = _iso_from_ts(data.get('timestamp'))
        if not posted and data.get('upload_date'):
            d = str(data['upload_date'])
            posted = f'{d[:4]}-{d[4:6]}-{d[6:8]}'
        return {'id': data.get('id'), 'uploader': data.get('uploader') or data.get('channel'),
                'posted_at': posted, 'title': data.get('title')}
    return None


class Run:
    """Downloads for one batch of links, with staging and a manifest."""

    def __init__(self, out_root, *, run_id: Optional[str] = None, history=None, force: bool = False):
        """history: a History to skip links saved before (None: no skipping). force: download them anyway."""
        self.history = history
        self.force = force
        self.root = Path(out_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or new_run_id()
        self.dir = self.root / RUNS_DIRNAME / self.run_id
        self.dir.mkdir(parents=True, exist_ok=False)
        self.results: List[JobResult] = []
        self._jobs = 0
        self._jsonl = open(self.dir / 'manifest.jsonl', 'a', encoding='utf-8', newline='\n')
        self.finished = False
        clean_old_staging(self.root)

    # --- recording -----------------------------------------------------------
    def _record(self, result: JobResult) -> JobResult:
        self._jsonl.write(json.dumps(result.to_dict(), ensure_ascii=False) + '\n')
        self._jsonl.flush()
        os.fsync(self._jsonl.fileno())
        self.results.append(result)
        return result

    def _base(self, url: str, handler: Optional[str], hint: str) -> JobResult:
        platform = detect.platform_for_url(url) or (detect.platform_for_hint(hint) if hint else None)
        backend = backend_for(handler) if handler else None
        return JobResult(
            run_id=self.run_id,
            input_url=url,
            normalized_url=detect.normalize_url(url),
            platform=platform.key if platform else None,
            backend=backend,
            status='skipped',
            backend_version=backend_version(backend) if backend else None,
            source_hint=hint or None,
        )

    def skip(self, url: str, reason: str, *, handler: Optional[str] = None, hint: str = '') -> JobResult:
        result = self._base(url, handler, hint)
        result.skip_reason = reason
        result.started_at = result.finished_at = utc_now()
        return self._record(result)

    # --- downloading ---------------------------------------------------------
    def download(self, downloader, handler: str, url: str, options: Optional[Dict] = None, *,
                 hint: str = '') -> JobResult:
        """Download one link through staging and record the result. Never raises for download errors."""
        self._jobs += 1
        result = self._base(url, handler, hint)
        result.started_at = utc_now()
        if self.history is not None and not self.force:
            entry = self.history.find(url)
            if entry:
                result.status = 'already_saved'
                result.previous = {'saved_at': entry.first_saved_at, 'output_root': entry.output_root,
                                   'manifest_path': entry.manifest_path, 'files': entry.files}
                result.finished_at = utc_now()
                return self._record(result)
        stage = self.root / STAGING_DIRNAME / f'{self.run_id}_{self._jobs:04d}'
        stage.mkdir(parents=True, exist_ok=True)
        try:
            downloader.download(handler, url, options or {}, out_dir=str(stage))
            files = self._collect(stage)
            if not files:
                result.status = 'failed'
                result.error_kind = 'no_media'
                result.error_message = 'The download finished but no file was saved.'
            else:
                result.status = 'saved'
                result.files = files
                self._read_post(result)
        except Exception as exc:  # recorded, not raised: every link gets a result
            result.status = 'failed'
            result.error_detail = str(exc)
            result.error_message = str(exc).splitlines()[0][:500] if str(exc) else type(exc).__name__
            result.error_kind = classify(str(exc))
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        result.finished_at = utc_now()
        if result.status == 'saved' and self.history is not None:
            self.history.add(url, saved_at=result.finished_at, output_root=self.root,
                             manifest_path=self.dir / 'manifest.jsonl', files=[f.path for f in result.files])
        return self._record(result)

    def _read_post(self, result: JobResult) -> None:
        """Fill metadata_path and post from the metadata file the backend wrote, if any."""
        for f in result.files:
            name = f.path.rsplit('/', 1)[-1]
            if name.endswith(('.info.json', '.drive.json')) or (name.endswith('.json') and result.backend == 'instaloader'):
                post = post_from_metadata(self.root / f.path)
                if post:
                    result.metadata_path = f.path
                    result.post = post
                    return

    def _collect(self, stage: Path) -> List[FileRecord]:
        """Hash every file in stage, then move it into the output root at the same relative path."""
        records = []
        for src in sorted(p for p in stage.rglob('*') if p.is_file()):
            if src.name.endswith(_TEMP_SUFFIXES):
                continue
            digest = sha256_file(src)
            size = src.stat().st_size
            target = self.root / src.relative_to(stage)
            if target.exists() and target.is_file() and sha256_file(target) == digest:
                # the same bytes are already there: keep that file, record both
                records.append(FileRecord(_rel(target, self.root), size, digest, duplicate_of=_rel(target, self.root)))
                continue
            target = _free_name(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, target)  # same drive: atomic, and never over an existing file
            records.append(FileRecord(_rel(target, self.root), size, digest))
        return records

    # --- finishing -----------------------------------------------------------
    def finish(self) -> Dict:
        """Write manifest.csv and SHA256SUMS.txt; return counts by status."""
        if self.finished:
            return self.counts()
        self._jsonl.close()
        self.finished = True
        with open(self.dir / 'manifest.csv', 'w', encoding='utf-8-sig', newline='') as fp:
            writer = csv.DictWriter(fp, fieldnames=CSV_FIELDS, extrasaction='ignore')
            writer.writeheader()
            for r in self.results:
                row = r.to_dict()
                row['files'] = ' | '.join(f.path for f in r.files)
                row['bytes'] = sum(f.bytes for f in r.files) if r.files else ''
                row['sha256'] = ' | '.join(f.sha256 for f in r.files)
                if r.status == 'skipped' and not r.error_message:
                    row['error_message'] = r.skip_reason
                writer.writerow(row)
        seen = set()
        lines = []
        for r in self.results:
            for f in r.files:
                if f.path not in seen:
                    seen.add(f.path)
                    lines.append(f'{f.sha256}  {f.path}\n')
        with open(self.dir / 'SHA256SUMS.txt', 'w', encoding='utf-8', newline='\n') as fp:
            fp.writelines(lines)
        return self.counts()

    def counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self.results:
            counts[r.status] = counts.get(r.status, 0) + 1
        return counts

    def summary_line(self) -> str:
        c = self.counts()
        parts = [f'{c.get("saved", 0)} saved']
        if c.get('already_saved'):
            parts.append(f'{c["already_saved"]} already saved')
        if c.get('failed'):
            parts.append(f'{c["failed"]} failed')
        if c.get('skipped'):
            parts.append(f'{c["skipped"]} skipped')
        return 'Done. ' + ', '.join(parts) + '.'

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.finish()
        return False


def read_manifest(path) -> List[Dict]:
    """Read a manifest.jsonl, ignoring a last line cut short by a crash."""
    results = []
    with open(path, encoding='utf-8') as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except ValueError:
                _log.warning('Ignoring an incomplete line in %s', path)
    return results


def verify_sums(root, sums_file) -> Iterable[str]:
    """Yield a message for each file in SHA256SUMS.txt that is missing or changed."""
    root = Path(root)
    with open(sums_file, encoding='utf-8') as fp:
        for line in fp:
            digest, _, rel = line.rstrip('\n').partition('  ')
            target = root / rel
            if not target.is_file():
                yield f'missing: {rel}'
            elif sha256_file(target) != digest:
                yield f'changed: {rel}'


__all__ = ['FileRecord', 'JobResult', 'Run', 'classify', 'clean_old_staging', 'post_from_metadata',
           'read_manifest', 'sha256_file', 'verify_sums']
