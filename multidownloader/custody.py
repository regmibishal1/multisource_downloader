"""The custody log: what happened to a run's files, in order, tamper-evident (brief F29, 13.4).

custody.log sits in the run folder, one JSON object per line, appended and
never rewritten. Each line carries the SHA-256 of the line before it ("prev")
and of itself ("hash", computed with that field empty), so editing one line
breaks the chain. Someone who can edit the file could still rewrite all of it,
so the latest hash (the "head") is also recorded outside the log: in
anchors.log in the per-user data folder (A1), and in an export package's
bag-info.txt (A2). Verify compares the chain with those anchors.

Line format:

    {"seq": 41, "at": "2026-10-09T05:31:20Z", "event": "hashed", "run_id": "...",
     "detail": {"path": "x/user/2026-10-08_123_1.jpg", "sha256": "..."},
     "app": "multisource-downloader 0.5.0", "note": null,
     "prev": "sha256:<hash of line 40 without its newline>",
     "hash": "sha256:<hash of this line with "hash" set to "">"}
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import __version__

FILENAME = 'custody.log'
ANCHORS_FILENAME = 'anchors.log'
ZERO = 'sha256:' + '0' * 64
EVENTS = ('run_started', 'captured', 'hashed', 'noted', 'verified', 'exported', 'copied', 'anchored',
          'run_finished')
APP = f'multisource-downloader {__version__}'
# during a long run the head is anchored this often, whichever comes first (13.4, A1)
ANCHOR_EVERY_EVENTS = 100
ANCHOR_EVERY_S = 15 * 60


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def default_anchor_path() -> Path:
    """anchors.log in the per-user data folder, not the output folder (so a copy of the folder cannot carry it)."""
    from .appdirs import data_dir
    return data_dir() / ANCHORS_FILENAME


def _sha(text: str) -> str:
    return 'sha256:' + hashlib.sha256(text.encode('utf-8')).hexdigest()


def _serialize(entry: Dict) -> str:
    return json.dumps(entry, ensure_ascii=False, separators=(',', ':'))


def _blank_hash(line: str, value: str) -> str:
    """The line as it was hashed: its own "hash" value emptied, every other byte the same."""
    marker = f'"hash":"{value}"'
    if line.count(marker) != 1:
        raise ValueError('no single hash field')
    return line.replace(marker, '"hash":""')


class CustodyLog:
    """Appends events to one run's custody.log; thread safe."""

    def __init__(self, path, run_id: str, *, anchor_path: Optional[Path] = None,
                 clock: Callable[[], str] = utc_now, monotonic: Callable[[], float] = time.monotonic):
        self.path = Path(path)
        self.run_id = run_id
        self.anchor_path = Path(anchor_path) if anchor_path else None
        self.clock = clock
        self.monotonic = monotonic
        self._lock = threading.Lock()
        self.seq, self.head = 0, ZERO
        if self.path.exists():  # carry on after the last line (an interrupted run)
            chain = read_chain(self.path)
            if chain.broken_at is not None:
                raise ValueError(f'{self.path} is damaged at line {chain.broken_at}; it is not added to')
            if chain.entries:
                self.seq = chain.entries[-1]['seq']
                self.head = chain.heads[self.seq]  # the SHA-256 of the whole last line
        self._since_anchor = 0
        self._anchored_at = self.monotonic()

    def append(self, event: str, detail: Optional[Dict] = None, note: Optional[str] = None) -> Dict:
        if event not in EVENTS:
            raise ValueError(f'unknown custody event: {event}')
        with self._lock:
            entry = self._write(event, detail, note)
            self._since_anchor += 1
            due = (self._since_anchor >= ANCHOR_EVERY_EVENTS
                   or self.monotonic() - self._anchored_at >= ANCHOR_EVERY_S)
        if due and event != 'anchored' and self.anchor_path:
            self.anchor_local()
        return entry

    def _write(self, event, detail, note) -> Dict:
        entry = {'seq': self.seq + 1, 'at': self.clock(), 'event': event, 'run_id': self.run_id,
                 'detail': detail or {}, 'app': APP, 'note': note, 'prev': self.head, 'hash': ''}
        blank = _serialize(entry)
        entry['hash'] = _sha(blank)
        line = blank.replace('"hash":""', f'"hash":"{entry["hash"]}"')
        with open(self.path, 'a', encoding='utf-8', newline='\n') as fp:
            fp.write(line + '\n')
            fp.flush()
            os.fsync(fp.fileno())
        self.seq, self.head = entry['seq'], _sha(line)
        return entry

    def anchor_local(self) -> Optional[Dict]:
        """Record the head in anchors.log (A1), then note that in the log itself."""
        if not self.anchor_path or self.seq == 0:
            return None
        with self._lock:
            seq, head = self.seq, self.head
            record = {'run_id': self.run_id, 'seq': seq, 'head': head, 'anchored_at': self.clock(),
                      'methods': ['local'], 'ots_file': None, 'tsr_file': None, 'git_commit': None}
            self.anchor_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.anchor_path, 'a', encoding='utf-8', newline='\n') as fp:
                fp.write(_serialize(record) + '\n')
                fp.flush()
                os.fsync(fp.fileno())
            self._write('anchored', {'method': 'local', 'seq': seq, 'head': head}, None)
            self._since_anchor = 0
            self._anchored_at = self.monotonic()
        return record


# --- reading and checking ------------------------------------------------------------------

@dataclass
class Chain:
    entries: List[Dict] = field(default_factory=list)
    heads: Dict[int, str] = field(default_factory=dict)   # seq -> SHA-256 of that line
    broken_at: Optional[int] = None                        # first line number that does not fit
    reason: str = ''


def read_chain(path) -> Chain:
    """Check the chain from line 1; stops at the first line that does not fit."""
    chain = Chain()
    try:
        raw = Path(path).read_bytes().decode('utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        chain.broken_at, chain.reason = 1, f'cannot be read ({exc})'
        return chain
    prev = ZERO
    for number, line in enumerate(raw.split('\n'), start=1):
        if line == '':
            continue
        try:
            entry = json.loads(line)
            ok = (isinstance(entry, dict) and entry.get('seq') == len(chain.entries) + 1
                  and entry.get('prev') == prev and entry.get('hash') == _sha(_blank_hash(line, entry['hash'])))
        except (ValueError, KeyError, TypeError):
            ok = False
        if not ok:
            chain.broken_at, chain.reason = number, 'does not match the line before it'
            return chain
        chain.entries.append(entry)
        prev = _sha(line)
        chain.heads[entry['seq']] = prev
    return chain


def read_anchors(run_id: str, anchor_path: Optional[Path]) -> List[Dict]:
    if not anchor_path:
        return []
    found = []
    try:
        lines = Path(anchor_path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get('run_id') == run_id:
            found.append(record)
    return found


def _when(iso: Optional[str]) -> str:
    """2026-10-09T05:40:02Z -> 9 Oct 2026 05:40 UTC"""
    try:
        moment = datetime.strptime(iso or '', '%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        return iso or 'an unknown time'
    return f'{moment.day} {moment:%b %Y %H:%M} UTC'


@dataclass
class CustodyReport:
    ok: bool
    message: str
    lines: int = 0
    anchors_checked: int = 0
    hashed: Dict[str, str] = field(default_factory=dict)  # path -> sha256 from the log's hashed events


def check(log_path, run_id: Optional[str] = None, *, anchor_path: Optional[Path] = None,
          package_anchor: Optional[Dict] = None) -> CustodyReport:
    """The strongest true statement about the log (brief 13.4, Verify).

    package_anchor: {"seq": int, "head": str, "at": iso} from an export package's bag-info.txt (A2).
    """
    log_path = Path(log_path)
    if not log_path.exists():
        return CustodyReport(False, 'There is no custody log for this run.')
    chain = read_chain(log_path)
    run_id = run_id or (chain.entries[0]['run_id'] if chain.entries else None)
    hashed = {e['detail'].get('path'): e['detail'].get('sha256') for e in chain.entries
              if e.get('event') == 'hashed' and isinstance(e.get('detail'), dict)}
    anchors = [dict(a, kind='local') for a in read_anchors(run_id, anchor_path)] if run_id else []
    if package_anchor:
        anchors.append(dict(package_anchor, kind='package', anchored_at=package_anchor.get('at')))
    # every anchor must match the chain at its seq; the earliest one that does not is reported
    for anchor in sorted(anchors, key=lambda a: a.get('seq') or 0):
        seq = anchor.get('seq')
        if chain.heads.get(seq) != anchor.get('head'):
            # where the chain breaks, if that is before the anchored line; else the anchored line
            line = chain.broken_at if chain.broken_at is not None and chain.broken_at <= (seq or 0) else seq
            return CustodyReport(False, f'Changed after {_when(anchor.get("anchored_at"))}: line {line} does not '
                                        'match the anchored record.', len(chain.entries), len(anchors), hashed)
    if chain.broken_at is not None:
        return CustodyReport(False, f'The custody log was changed: line {chain.broken_at} {chain.reason}.',
                             len(chain.entries), len(anchors), hashed)
    package = [a for a in anchors if a['kind'] == 'package']
    local = [a for a in anchors if a['kind'] == 'local']
    if package:
        return CustodyReport(True, f'Unchanged since the package was exported on '
                                   f'{_when(package[-1].get("anchored_at"))}.', len(chain.entries), len(anchors),
                             hashed)
    if local:
        latest = max(local, key=lambda a: a.get('seq') or 0)
        tail = len(chain.entries) - latest['seq']
        extra = f' {tail} later line{"s are" if tail != 1 else " is"} not anchored yet.' if tail > 1 else ''
        return CustodyReport(True, "The log's own checks pass, but it is only anchored on this computer. Someone "
                                   'with access to this computer could have changed it.' + extra,
                             len(chain.entries), len(anchors), hashed)
    return CustodyReport(True, "The log's own checks pass, but it has no anchor, so a rewrite of the whole log "
                               'would not show.', len(chain.entries), 0, hashed)


__all__ = ['ANCHORS_FILENAME', 'Chain', 'CustodyLog', 'CustodyReport', 'EVENTS', 'FILENAME', 'ZERO', 'check',
           'default_anchor_path', 'read_anchors', 'read_chain']
