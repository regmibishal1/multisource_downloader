"""Export a run as an archive package: a BagIt bag (RFC 8493), brief F26 and 13.4 A2.

The package is a folder that archives, libraries and lawyers can check with
any BagIt tool:

    package-<run id>/
      bagit.txt
      bag-info.txt            Bagging-Date, Payload-Oxum, External-Identifier (the run id),
                              External-Description (the run notes), Bag-Software-Agent,
                              Source-Organization (optional), Custody-Log-Head and -Seq
      manifest-sha512.txt     every payload file
      manifest-sha256.txt
      tagmanifest-sha512.txt  the files above
      tagmanifest-sha256.txt
      data/                   the run's files at the same paths as in the download folder,
        x/someone/...         and its run folder:
        _runs/<run id>/       manifest.jsonl, manifest.csv, SHA256SUMS.txt, custody.log, ...

Before copying, an "exported" line is added to the run's custody log; the
package records the log's head after that line (A2). Every file is re-hashed
as it is copied and must match what the run recorded, and the whole package
is verified once it is written. It is written under a temporary name and only
renamed when complete, so a half-written package is never mistaken for one.
"""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import __version__, custody, verify
from .runs import NOTES_FILENAME, PLAN_FILENAME, describe_notes, read_manifest

RUN_FILES = ('manifest.jsonl', 'manifest.csv', 'SHA256SUMS.txt', PLAN_FILENAME, NOTES_FILENAME, custody.FILENAME)
CHUNK = 1024 * 1024


class PackageError(Exception):
    """The package could not be made; the message says why in plain words."""


@dataclass
class PackageResult:
    path: Path
    files: int
    bytes: int
    zip_path: Optional[Path] = None
    report: Optional[verify.VerifyReport] = None

    def message(self) -> str:
        where = self.zip_path or self.path
        return f'Package created and verified: {self.files} files in {where}.'


def _encode(path: str) -> str:
    # RFC 8493 2.1.3: CR, LF and % in names are percent-encoded in manifests
    return path.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')


def _copy_hashing(src: Path, dst: Path) -> Tuple[str, str, int]:
    """Copy src to dst and return the SHA-256 and SHA-512 of the bytes written."""
    sha256, sha512, size = hashlib.sha256(), hashlib.sha512(), 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, 'rb') as fin, open(dst, 'wb') as fout:
        for chunk in iter(lambda: fin.read(CHUNK), b''):
            sha256.update(chunk)
            sha512.update(chunk)
            fout.write(chunk)
            size += len(chunk)
    shutil.copystat(src, dst)
    return sha256.hexdigest(), sha512.hexdigest(), size


def _payload(run_dir: Path) -> List[Tuple[str, Path, Optional[str]]]:
    """(path in data/, source file, SHA-256 the run recorded or None) for everything to package."""
    root = run_dir.parent.parent
    sums_path = run_dir / 'SHA256SUMS.txt'
    if not sums_path.is_file():
        raise PackageError('This run is not finished yet, so it has no SHA256SUMS.txt. Finish or resume it first.')
    bad: List[str] = []
    sums = verify.read_sums(sums_path, bad)
    if bad:
        raise PackageError('SHA256SUMS.txt is damaged. Run Verify files to see what changed.')
    items = [(rel, root / rel, digest) for rel, digest in sorted(sums.items())]
    run_rel = run_dir.relative_to(root).as_posix()
    for name in RUN_FILES:
        if (run_dir / name).is_file():
            items.append((f'{run_rel}/{name}', run_dir / name, None))
    return items


def export(run_dir, out_dir, *, source_organization: str = '', make_zip: bool = False,
           anchor_path: Optional[Path] = None, now: Optional[datetime] = None) -> PackageResult:
    run_dir = verify.find_run_dir(Path(run_dir))
    out_dir = Path(out_dir)
    run_id = run_dir.name
    target = out_dir / f'package-{run_id}'
    if target.exists():
        raise PackageError(f'{target.name} already exists in {out_dir}. Choose another folder.')
    if target.resolve().is_relative_to(run_dir.parent.parent.resolve()):
        raise PackageError('Choose a folder outside the download folder for the package.')
    now = now or datetime.now(timezone.utc)
    items = _payload(run_dir)
    missing = [rel for rel, src, _d in items if not src.is_file()]
    if missing:
        raise PackageError(f'{len(missing)} file(s) are missing, for example {missing[0]}. '
                           'Run Verify files to see which.')
    # the export goes into the custody log first, so the package carries that line (A2)
    log = custody.CustodyLog(run_dir / custody.FILENAME, run_id) if (run_dir / custody.FILENAME).exists() else None
    if log is not None:
        log.append('exported', {'package': target.name, 'files': len(items)})
    partial = out_dir / f'{target.name}.partial'
    if partial.exists():
        shutil.rmtree(partial)
    try:
        lines256, lines512, total = [], [], 0
        for rel, src, recorded in items:
            d256, d512, size = _copy_hashing(src, partial / 'data' / rel)
            if recorded and d256 != recorded:
                raise PackageError(f'{rel} has changed since it was saved, so it was not packaged. '
                                   'Run Verify files to see what changed.')
            lines256.append(f'{d256}  data/{_encode(rel)}\n')
            lines512.append(f'{d512}  data/{_encode(rel)}\n')
            total += size
        _write(partial / 'bagit.txt', 'BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n')
        _write(partial / 'manifest-sha256.txt', ''.join(lines256))
        _write(partial / 'manifest-sha512.txt', ''.join(lines512))
        _write(partial / 'bag-info.txt', _bag_info(run_dir, run_id, total, len(items), source_organization, log,
                                                   now))
        tags = ['bagit.txt', 'bag-info.txt', 'manifest-sha256.txt', 'manifest-sha512.txt']
        for algorithm in ('sha256', 'sha512'):
            _write(partial / f'tagmanifest-{algorithm}.txt', ''.join(
                f'{verify.file_hash(partial / t, algorithm)}  {t}\n' for t in tags))
        report = verify.verify_package(partial, check_custody=log is not None)
        if not report.ok:
            raise PackageError('The package did not verify after it was written: ' + ' '.join(report.lines()))
        partial.rename(target)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    if log is not None and anchor_path:
        log.anchor_path = Path(anchor_path)
        log.anchor_local()  # the run's own log on this computer moves on; the package keeps its copy
    result = PackageResult(target, len(items), total, report=report)
    if make_zip:
        result.zip_path = Path(shutil.make_archive(str(target), 'zip', root_dir=out_dir, base_dir=target.name))
    return result


def _write(path: Path, text: str) -> None:
    with open(path, 'w', encoding='utf-8', newline='\n') as fp:
        fp.write(text)


def _bag_info(run_dir: Path, run_id: str, total: int, count: int, source_organization: str,
              log: Optional[custody.CustodyLog], now: datetime) -> str:
    notes = {}
    plan_notes = run_dir / NOTES_FILENAME
    if plan_notes.is_file():
        import json
        try:
            notes = json.loads(plan_notes.read_text(encoding='utf-8')).get('run') or {}
        except ValueError:
            notes = {}
    description = describe_notes(notes) or f'Files collected with multisource-downloader, run {run_id}.'
    rows: List[Tuple[str, str]] = []
    if source_organization.strip():
        rows.append(('Source-Organization', ' '.join(source_organization.split())))
    rows += [
        ('Bagging-Date', now.strftime('%Y-%m-%d')),
        ('Payload-Oxum', f'{total}.{count}'),
        ('External-Identifier', run_id),
        ('External-Description', description),
        ('Bag-Software-Agent', f'multisource-downloader {__version__}'),
        ('Bag-Count', '1 of 1'),
    ]
    links = len(read_manifest(run_dir / 'manifest.jsonl')) if (run_dir / 'manifest.jsonl').is_file() else 0
    rows.append(('Internal-Sender-Description', f'{links} links from one run of multisource-downloader.'))
    if log is not None:
        rows += [('Custody-Log-Head', log.head), ('Custody-Log-Seq', str(log.seq)),
                 ('Custody-Log-Anchored', now.strftime('%Y-%m-%dT%H:%M:%SZ'))]
    return ''.join(f'{key}: {value}\n' for key, value in rows)


def list_runs(root: Path) -> Dict[str, Path]:
    """Run folders in a download folder, newest first."""
    runs_dir = Path(root) / '_runs'
    found = sorted((p for p in runs_dir.iterdir() if (p / 'manifest.jsonl').is_file()), reverse=True) \
        if runs_dir.is_dir() else []
    return {p.name: p for p in found}


__all__ = ['PackageError', 'PackageResult', 'export', 'list_runs']
