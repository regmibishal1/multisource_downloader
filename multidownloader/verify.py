"""Verify: re-hash every file and compare it with what was recorded (brief F28, 13.4).

Works on a run folder (`<download folder>/_runs/<run id>`, files checked
against its SHA256SUMS.txt) or on an export package (a BagIt bag, RFC 8493:
every payload file checked against each manifest-<algorithm>.txt, the tag
files against tagmanifest-<algorithm>.txt, plus Payload-Oxum). With
custody=True the custody log's chain and anchors are checked too, and the
recorded hashes are compared with the log's own "hashed" events, so editing a
file and its SHA256SUMS line together still shows.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import custody

HASH_CHUNK = 1024 * 1024
ALGORITHMS = ('sha512', 'sha256')  # the strongest first


@dataclass
class VerifyReport:
    kind: str                         # run | package
    checked: int = 0
    changed: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    extra: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)   # anything else that is wrong, in plain words
    custody: Optional[custody.CustodyReport] = None

    @property
    def files_ok(self) -> bool:
        return not (self.changed or self.missing or self.extra or self.problems)

    @property
    def ok(self) -> bool:
        return self.files_ok and (self.custody is None or self.custody.ok)

    def summary(self) -> str:
        noun = 'file' if self.checked == 1 else 'files'
        if self.files_ok:
            return f'All {self.checked} {noun} match.' if self.checked != 1 else 'The 1 file matches.'
        parts = [f'{len(v)} {k}' for k, v in (('changed', self.changed), ('missing', self.missing),
                                               ('extra', self.extra)) if v]
        return f'Problems found in {self.checked} {noun}: ' + (', '.join(parts) or 'see below') + '.'

    def lines(self) -> List[str]:
        out = [self.summary()]
        out += [f'  changed: {p}' for p in self.changed]
        out += [f'  missing: {p}' for p in self.missing]
        out += [f'  extra:   {p}' for p in self.extra]
        out += [f'  {p}' for p in self.problems]
        if self.custody is not None:
            out.append(('Custody log: ' if self.custody.ok else '! Custody log: ') + self.custody.message)
        return out


def file_hash(path: Path, algorithm: str = 'sha256') -> str:
    digest = hashlib.new(algorithm)
    with open(path, 'rb') as fp:
        for chunk in iter(lambda: fp.read(HASH_CHUNK), b''):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sums(text: str, bad: Optional[List[str]] = None) -> Dict[str, str]:
    """`<hash>  <path>` lines (sha256sum and BagIt manifests) -> {path: hash}.

    Lines that do not have that shape are added to bad, when given.
    """
    found = {}
    for line in text.split('\n'):  # only LF (and CRLF) end a line; any other byte is part of it
        line = line[:-1] if line.endswith('\r') else line
        if not line:
            continue
        digest, _, name = line.partition(' ')
        name = name.lstrip(' ').lstrip('*')
        if digest and name and all(c in '0123456789abcdefABCDEF' for c in digest):
            found[name] = digest.lower()
        elif bad is not None:
            bad.append(line)
    return found


def read_sums(path: Path, bad: Optional[List[str]] = None) -> Dict[str, str]:
    # bytes, not text mode: text mode would turn a stray CR into a line break and hide it
    return parse_sums(Path(path).read_bytes().decode('utf-8', errors='replace'), bad)


def _decode_bag_path(name: str) -> str:
    # RFC 8493 2.1.3: CR, LF and % in file names are percent-encoded
    return name.replace('%0D', '\r').replace('%0A', '\n').replace('%25', '%')


def is_package(path: Path) -> bool:
    return (Path(path) / 'bagit.txt').is_file()


def find_run_dir(path: Path) -> Path:
    path = Path(path)
    if (path / 'manifest.jsonl').is_file():
        return path
    raise ValueError(f'{path} is not a run folder (…/_runs/<run id>) or an export package.')


# --- run folders ----------------------------------------------------------------------------

def verify_run(run_dir: Path, *, check_custody: bool = False, anchor_path: Optional[Path] = None,
               record: bool = False) -> VerifyReport:
    run_dir = find_run_dir(run_dir)
    root = run_dir.parent.parent
    report = VerifyReport('run')
    sums_path = run_dir / 'SHA256SUMS.txt'
    manifest = _manifest_hashes(run_dir / 'manifest.jsonl')
    if sums_path.is_file():
        bad: List[str] = []
        sums = read_sums(sums_path, bad)
        if bad:
            report.problems.append(f'SHA256SUMS.txt has {len(bad)} line(s) that are not a checksum and a file name.')
        for path, digest in manifest.items():
            if path in sums and sums[path] != digest:
                report.problems.append(f'SHA256SUMS.txt and manifest.jsonl disagree about {path}.')
    else:
        sums = manifest  # an unfinished run has no SHA256SUMS.txt yet
        if not manifest:
            report.problems.append('This run has no SHA256SUMS.txt and saved no files.')
    for path, digest in sorted(sums.items()):
        report.checked += 1
        target = root / path
        if not target.is_file():
            report.missing.append(path)
        elif file_hash(target) != digest:
            report.changed.append(path)
    log = run_dir / custody.FILENAME
    if check_custody:
        report.custody = custody.check(log, anchor_path=anchor_path)
        _compare_with_log(report, sums)
    if record and log.exists():
        try:
            custody.CustodyLog(log, run_dir.name).append('verified', {
                'files': report.checked, 'changed': len(report.changed), 'missing': len(report.missing),
                'result': 'valid' if report.ok else 'not valid'})
        except ValueError:  # a damaged log is reported, never added to
            if report.custody is None:
                report.custody = custody.check(log, anchor_path=anchor_path)
    return report


def _manifest_hashes(path: Path) -> Dict[str, str]:
    found = {}
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return found
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        for f in row.get('files') or []:
            if isinstance(f, dict) and f.get('path') and f.get('sha256') and not f.get('duplicate_of'):
                found[f['path']] = f['sha256']
    return found


def _compare_with_log(report: VerifyReport, sums: Dict[str, str], prefix: str = '') -> None:
    if report.custody is None:
        return
    for path, digest in report.custody.hashed.items():
        listed = sums.get(prefix + path) if path else None
        if path and listed is not None and listed != digest:
            report.problems.append(f'{path} is listed with a different SHA-256 than the custody log recorded.')


# --- export packages (BagIt) ----------------------------------------------------------------

def read_bag_info(bag: Path) -> Dict[str, List[str]]:
    info: Dict[str, List[str]] = {}
    try:
        lines = (bag / 'bag-info.txt').read_text(encoding='utf-8').splitlines()
    except OSError:
        return info
    last = None
    for line in lines:
        if line[:1] in (' ', '\t') and last:  # a folded long value
            info[last][-1] += ' ' + line.strip()
            continue
        key, sep, value = line.partition(':')
        if sep:
            last = key.strip()
            info.setdefault(last, []).append(value.strip())
    return info


def _bag_manifests(bag: Path, prefix: str, problems: Optional[List[str]] = None) -> List[Tuple[str, Dict[str, str]]]:
    found = []
    for algorithm in ALGORITHMS:
        path = bag / f'{prefix}-{algorithm}.txt'
        if path.is_file():
            bad: List[str] = []
            entries = read_sums(path, bad)
            if bad and problems is not None:
                problems.append(f'{path.name} has {len(bad)} line(s) that are not a checksum and a file name.')
            found.append((algorithm, {_decode_bag_path(k): v for k, v in entries.items()}))
    return found


def verify_package(bag: Path, *, check_custody: bool = False, anchor_path: Optional[Path] = None) -> VerifyReport:
    """Never writes to the package: its own manifests cover every file in it."""
    bag = Path(bag)
    report = VerifyReport('package')
    declaration = (bag / 'bagit.txt').read_bytes().decode('utf-8', errors='replace')
    if 'BagIt-Version' not in declaration:
        report.problems.append('bagit.txt does not declare a BagIt version.')
    manifests = _bag_manifests(bag, 'manifest', report.problems)
    if not manifests:
        report.problems.append('The package has no manifest-sha512.txt or manifest-sha256.txt.')
        return report
    listed = set()
    for _algorithm, entries in manifests:
        listed |= set(entries)
    for path in sorted(listed):
        report.checked += 1
        target = bag / path
        if not target.is_file():
            report.missing.append(path)
            continue
        for algorithm, entries in manifests:
            if path in entries and file_hash(target, algorithm) != entries[path]:
                report.changed.append(path)
                break
    data = bag / 'data'
    for path in sorted(p for p in data.rglob('*') if p.is_file()) if data.is_dir() else []:
        rel = path.relative_to(bag).as_posix()
        if rel not in listed:
            report.extra.append(rel)
    for _algorithm, entries in _bag_manifests(bag, 'tagmanifest', report.problems):
        for path, digest in entries.items():
            target = bag / path
            if not target.is_file():
                report.missing.append(path)
            elif file_hash(target, _algorithm) != digest:
                report.changed.append(path)
    info = read_bag_info(bag)
    oxum = (info.get('Payload-Oxum') or [''])[0]
    if oxum and not report.missing:
        size = sum((bag / p).stat().st_size for p in listed if (bag / p).is_file())
        if oxum != f'{size}.{len(listed)}':
            report.problems.append(f'Payload-Oxum says {oxum}, the payload is {size}.{len(listed)}.')
    if check_custody:
        logs = sorted(data.rglob(custody.FILENAME)) if data.is_dir() else []
        package_anchor = None
        head, seq = (info.get('Custody-Log-Head') or [None])[0], (info.get('Custody-Log-Seq') or [None])[0]
        if head and seq and str(seq).isdigit():
            when = (info.get('Custody-Log-Anchored') or info.get('Bagging-Date') or [None])[0]
            package_anchor = {'seq': int(seq), 'head': head, 'at': when}
        if logs:
            report.custody = custody.check(logs[0], anchor_path=anchor_path, package_anchor=package_anchor,
                                           copy=True)
            # paths in the log are relative to the download folder, which is data/ in the package
            sums = {p: v for _a, entries in manifests if _a == 'sha256' for p, v in entries.items()}
            _compare_with_log(report, sums, prefix='data/')
        else:
            report.custody = custody.CustodyReport(False, 'There is no custody log in this package.')
    return report


def verify(path, *, check_custody: bool = False, anchor_path: Optional[Path] = None,
           record: bool = False) -> VerifyReport:
    path = Path(path)
    if is_package(path):
        return verify_package(path, check_custody=check_custody, anchor_path=anchor_path)
    return verify_run(path, check_custody=check_custody, anchor_path=anchor_path, record=record)


__all__ = ['VerifyReport', 'file_hash', 'find_run_dir', 'is_package', 'parse_sums', 'read_bag_info', 'read_sums', 'verify',
           'verify_package', 'verify_run']
