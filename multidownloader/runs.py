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
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import __version__, detect, errors, signins
from .custody import FILENAME as CUSTODY_FILENAME, CustodyLog

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
    'Bluesky': 'gallery-dl',
}

CSV_FIELDS = (
    'run_id', 'status', 'platform', 'input_url', 'normalized_url', 'backend', 'files', 'bytes', 'sha256',
    'error_kind', 'error_message', 'started_at', 'finished_at', 'app_version', 'backend_version', 'note',
)
# run notes (brief F27, after WITNESS: what, where, who, why)
NOTE_FIELDS = ('topic', 'where', 'collected_by', 'notes')
NOTES_FILENAME = 'notes.json'


def clean_notes(notes: Optional[Dict]) -> Dict[str, str]:
    """Only the known fields, trimmed; empty ones left out."""
    return {k: str(notes[k]).strip() for k in NOTE_FIELDS if notes and str(notes.get(k) or '').strip()}


def describe_notes(notes: Optional[Dict]) -> str:
    """One line for a package's External-Description, for example "Topic: Rally. Where: Kathmandu."."""
    labels = {'topic': 'Topic', 'where': 'Where', 'collected_by': 'Collected by', 'notes': 'Notes'}
    parts = []
    for key, value in clean_notes(notes).items():
        text = ' '.join(value.split())  # one line: bag-info values cannot hold line breaks
        parts.append(f'{labels[key]}: {text}' + ('' if text.endswith(('.', '!', '?')) else '.'))
    return ' '.join(parts)


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
    """The error kind for an error text (see errors.classify for the message and action)."""
    return errors.classify(message).kind


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
    error_message: Optional[str] = None   # plain words for the user (errors.py)
    error_detail: Optional[str] = None    # the library's own text, for the log
    error_action: Optional[str] = None    # one next step, e.g. fix_signin, retry
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
    notes: Optional[Dict] = None     # {"run": {...}, "link": "..."}: the collector's notes (brief F27)

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
    if 'category' in data and 'subcategory' in data:  # gallery-dl
        author = data.get('author') if isinstance(data.get('author'), dict) else {}
        user = data.get('user') if isinstance(data.get('user'), dict) else {}
        uploader = (author.get('name') or author.get('handle') or author.get('uniqueId') or user.get('name')
                    or data.get('username') or (data.get('author') if isinstance(data.get('author'), str) else None))
        posted = data.get('date') or data.get('post_date')
        if isinstance(posted, str) and len(posted) >= 19 and posted[10] == ' ':
            posted = posted[:10] + 'T' + posted[11:19] + 'Z'  # gallery-dl writes UTC "YYYY-MM-DD HH:MM:SS"
        text = data.get('content') or data.get('text') or data.get('description') or data.get('title')
        return {'id': str(data.get('tweet_id') or data.get('post_shortcode') or data.get('post_id') or
                          data.get('shortcode') or data.get('id') or '') or None,
                'uploader': uploader, 'posted_at': posted if isinstance(posted, str) else None, 'title': text}
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

    def __init__(self, out_root, *, run_id: Optional[str] = None, history=None, force: bool = False,
                 last_run_pointer: Optional[Path] = None, anchor_path: Optional[Path] = None,
                 notes: Optional[Dict] = None):
        """last_run_pointer: where plan() records this run for resuming; only the app's own entry
        points pass one (runs.default_last_run_pointer()), so tests never touch user data.
        anchor_path: anchors.log for the custody log's head (custody.default_anchor_path()), same rule."""
        self.last_run_pointer = last_run_pointer
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
        # several download threads share one run (runner.py)
        self._lock = threading.Lock()       # job numbers, manifest lines, results
        self._move_lock = threading.Lock()  # choosing free names and moving files into place
        self._jsonl = open(self.dir / 'manifest.jsonl', 'a', encoding='utf-8', newline='\n')
        self.finished = False
        self._planned = False
        # what happened to the files, in order, with a hash chain (brief F29)
        self.custody = CustodyLog(self.dir / CUSTODY_FILENAME, self.run_id, anchor_path=anchor_path)
        self.notes = {'run': clean_notes(notes), 'links': {}}
        started = {'app': f'multisource-downloader {__version__}'}
        if self.notes['run']:
            started['notes'] = self.notes['run']
            self._write_notes()
        self.custody.append('run_started', started)
        clean_old_staging(self.root)

    def plan(self, links: Iterable[Dict]) -> None:
        """Record the links this run will try, so a run that stops can be resumed.

        links: dicts with 'url' and optionally 'handler' and 'hint'. Also points
        last_run_pointer (when set) at this run.
        """
        links = [{'url': link['url'], 'handler': link.get('handler'), 'hint': link.get('hint') or ''}
                 for link in links]
        _write_json_atomic(self.dir / PLAN_FILENAME, {
            'run_id': self.run_id, 'output_root': str(self.root), 'created_at': utc_now(),
            'finished': False, 'links': links,
        })
        self._planned = True
        if self.last_run_pointer is None:
            return
        try:
            _write_json_atomic(Path(self.last_run_pointer), {'run_dir': str(self.dir)})
        except OSError as exc:
            _log.warning('Could not record the last run: %s', exc)

    # --- notes (brief F27) ---------------------------------------------------
    def _write_notes(self) -> None:
        _write_json_atomic(self.dir / NOTES_FILENAME, self.notes)

    def set_link_note(self, url: str, text: Optional[str]) -> None:
        """A note for one link, at any time: before it runs (it goes into its manifest row) or after."""
        text = (text or '').strip()
        with self._lock:
            if text:
                self.notes['links'][url] = text
            else:
                self.notes['links'].pop(url, None)
            self._write_notes()
        self.custody.append('noted', {'url': url}, note=text or None)
        if self.finished:
            self.custody.anchor_local()  # the run's head moved after it was anchored

    def _notes_for(self, url: str) -> Optional[Dict]:
        link = self.notes['links'].get(url)
        if not (self.notes['run'] or link):
            return None
        return {'run': dict(self.notes['run']) or None, 'link': link}

    # --- recording -----------------------------------------------------------
    def _record(self, result: JobResult) -> JobResult:
        result.notes = self._notes_for(result.input_url)
        line = json.dumps(result.to_dict(), ensure_ascii=False) + '\n'
        with self._lock:
            self._jsonl.write(line)
            self._jsonl.flush()
            os.fsync(self._jsonl.fileno())
            self.results.append(result)
        return result

    def record(self, result: JobResult) -> JobResult:
        """Record a result produced by download(..., record=False)."""
        return self._record(result)

    def stopped(self, url: str, *, handler: Optional[str] = None, hint: str = '') -> JobResult:
        """Record a link that was not started because the run was stopped."""
        result = self._base(url, handler, hint)
        result.status = 'stopped'
        result.error_message = 'Stopped before it started.'
        result.started_at = result.finished_at = utc_now()
        return self._record(result)

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
                 hint: str = '', record: bool = True) -> JobResult:
        """Download one link through staging and record the result. Never raises for download errors.

        record=False returns the result without writing it, for a caller that
        may retry the link (runner.py) and records the final result itself.
        """
        with self._lock:
            self._jobs += 1
            job_number = self._jobs
        result = self._base(url, handler, hint)
        result.started_at = utc_now()
        if self.history is not None and not self.force:
            entry = self.history.find(url)
            if entry:
                result.status = 'already_saved'
                result.previous = {'saved_at': entry.first_saved_at, 'output_root': entry.output_root,
                                   'manifest_path': entry.manifest_path, 'files': entry.files}
                result.finished_at = utc_now()
                return self._record(result) if record else result
        stage = self.root / STAGING_DIRNAME / f'{self.run_id}_{job_number:04d}'
        stage.mkdir(parents=True, exist_ok=True)
        try:
            outcome = downloader.download(handler, url, options or {}, out_dir=str(stage))
            if isinstance(outcome, dict) and outcome.get('backend'):
                # another backend did the work (gallery-dl for image posts)
                result.backend = outcome['backend']
                result.backend_version = backend_version(result.backend)
            self.custody.append('captured', {'url': url, 'backend': result.backend,
                                             'backend_version': result.backend_version})
            files = self._collect(stage)
            if not files:
                result.status = 'failed'
                result.error_kind = errors.NO_MEDIA
                result.error_message = 'The download finished but no file was saved.'
            else:
                result.status = 'saved'
                result.files = files
                self._read_post(result)
        except Exception as exc:  # recorded, not raised: every link gets a result
            result.status = 'failed'
            info = self.explain(exc, url)
            result.error_kind = info.kind
            result.error_message = info.message
            result.error_action = info.action
            result.error_detail = str(exc) or type(exc).__name__
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        result.finished_at = utc_now()
        if result.status == 'saved' and self.history is not None:
            self.history.add(url, saved_at=result.finished_at, output_root=self.root,
                             manifest_path=self.dir / 'manifest.jsonl', files=[f.path for f in result.files])
        return self._record(result) if record else result

    def explain(self, exc: BaseException, url: str) -> 'errors.ErrorInfo':
        """Plain message and next step for a failed link."""
        platform = detect.platform_for_url(url)
        try:
            signed_in = bool(platform and signins.cookie_file_for_download(platform.handler))
        except Exception:
            signed_in = False
        return errors.classify(exc, platform=platform.name if platform else '', signed_in=signed_in)

    def _read_post(self, result: JobResult) -> None:
        """Fill metadata_path and post from the metadata file the backend wrote, if any."""
        for f in result.files:
            name = f.path.rsplit('/', 1)[-1]
            if name.endswith(('.info.json', '.drive.json')) or (
                name.endswith('.json') and result.backend in ('instaloader', 'gallery-dl')):
                post = post_from_metadata(self.root / f.path)
                if post:
                    # the folder date is the post's when known, else the download date
                    post['date_source'] = 'posted' if post.get('posted_at') else 'downloaded'
                    result.metadata_path = f.path
                    result.post = post
                    return

    def _collect(self, stage: Path) -> List[FileRecord]:
        """Hash every file in stage, then move it into the output root at the same relative path."""
        records = []
        for src in sorted(p for p in stage.rglob('*') if p.is_file()):
            if src.name.endswith(_TEMP_SUFFIXES):
                continue
            digest = sha256_file(src)  # hashing runs in parallel; only the move is serialized
            size = src.stat().st_size
            with self._move_lock:
                target = self.root / src.relative_to(stage)
                if target.exists() and target.is_file() and sha256_file(target) == digest:
                    # the same bytes are already there: keep that file, record both
                    records.append(FileRecord(_rel(target, self.root), size, digest,
                                              duplicate_of=_rel(target, self.root)))
                    continue
                target = _free_name(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, target)  # same drive: atomic, and never over an existing file
            records.append(FileRecord(_rel(target, self.root), size, digest))
            self.custody.append('hashed', {'path': _rel(target, self.root), 'sha256': digest, 'bytes': size})
        return records

    # --- finishing -----------------------------------------------------------
    def finish(self) -> Dict:
        """Write manifest.csv and SHA256SUMS.txt; return counts by status."""
        if self.finished:
            return self.counts()
        self._jsonl.close()
        self.finished = True
        if self._planned:
            plan_path = self.dir / PLAN_FILENAME
            try:
                plan = json.loads(plan_path.read_text(encoding='utf-8'))
                plan.update({'finished': True, 'finished_at': utc_now()})
                _write_json_atomic(plan_path, plan)
            except (OSError, ValueError) as exc:
                _log.warning('Could not mark the run finished: %s', exc)
        with open(self.dir / 'manifest.csv', 'w', encoding='utf-8-sig', newline='') as fp:
            writer = csv.DictWriter(fp, fieldnames=CSV_FIELDS, extrasaction='ignore')
            writer.writeheader()
            for r in self.results:
                row = r.to_dict()
                row['files'] = ' | '.join(f.path for f in r.files)
                row['bytes'] = sum(f.bytes for f in r.files) if r.files else ''
                row['sha256'] = ' | '.join(f.sha256 for f in r.files)
                row['note'] = self.notes['links'].get(r.input_url, '')
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
        try:
            self.custody.append('run_finished', {'counts': self.counts(), 'files': len(lines)})
            self.custody.anchor_local()  # the head goes outside the run folder (13.4, A1)
        except OSError as exc:
            _log.warning('Could not finish the custody log: %s', exc)
        return self.counts()

    def counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        with self._lock:
            results = list(self.results)
        for r in results:
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
        if c.get('stopped'):
            parts.append(f'{c["stopped"]} not started')
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


# --- resuming a run that stopped (brief T20) ------------------------------------

PLAN_FILENAME = 'run.json'
LAST_RUN_FILENAME = 'last_run.json'


def _write_json_atomic(path: Path, data: Dict) -> None:
    from .session_store import _atomic_write

    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))


def default_last_run_pointer() -> Path:
    """last_run.json in the per-user data folder."""
    from .appdirs import data_dir

    return data_dir() / LAST_RUN_FILENAME


@dataclass
class UnfinishedRun:
    run_dir: Path
    output_root: Path
    run_id: str
    remaining: List[Dict]  # {'url', 'handler', 'hint'} for links with no result yet

    def message(self) -> str:
        n = len(self.remaining)
        noun = 'link was' if n == 1 else 'links were'
        return f'Your last run stopped before it finished. {n} {noun} not done.'

    def close(self, how: str, by_run: Optional[str] = None) -> None:
        """Mark the old run handled ("resumed" or "dismissed") so it is not offered again."""
        path = self.run_dir / PLAN_FILENAME
        try:
            plan = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            plan = {}
        plan.update({'closed': how, 'closed_at': utc_now(), 'resumed_by': by_run})
        _write_json_atomic(path, plan)


def last_unfinished_run(pointer: Optional[Path] = None) -> Optional[UnfinishedRun]:
    """The most recent run that stopped with links not done, unless already resumed or dismissed."""
    pointer = Path(pointer) if pointer else default_last_run_pointer()
    try:
        run_dir = Path(json.loads(pointer.read_text(encoding='utf-8'))['run_dir'])
        plan = json.loads((run_dir / PLAN_FILENAME).read_text(encoding='utf-8'))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if plan.get('closed'):
        return None
    # Not done: every planned link without a result other than "stopped". That
    # covers a run that never finished (crash, power cut), links the user
    # stopped, and a link that was mid-download when the run was interrupted.
    lines = read_manifest(run_dir / 'manifest.jsonl') if (run_dir / 'manifest.jsonl').exists() else []
    done = {line.get('input_url') for line in lines if line.get('status') != 'stopped'}
    remaining = [link for link in plan.get('links', []) if link.get('url') not in done]
    if not remaining:
        return None
    return UnfinishedRun(run_dir, Path(plan.get('output_root') or run_dir.parents[1]), plan.get('run_id', ''),
                         remaining)


__all__ = ['FileRecord', 'JobResult', 'Run', 'UnfinishedRun', 'classify', 'clean_old_staging',
           'default_last_run_pointer', 'last_unfinished_run', 'post_from_metadata', 'read_manifest', 'sha256_file',
           'verify_sums']
