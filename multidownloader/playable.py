"""Make a playable copy of a saved file (brief F30).

With "Keep files exactly as the platform served them" on, yt-dlp does not
repair containers after download, so the saved file is the platform's bytes.
If such a file does not play, this writes a second file next to it, remuxed
by ffmpeg with the streams copied as they are (no re-encoding), and records
it: a line in SHA256SUMS.txt, a "derived" row in manifest.jsonl and a
"copied" line in the custody log. The original is never changed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from . import custody, verify
from .runs import sha256_file, utc_now

HOW = 'ffmpeg remux with the streams copied as they are (-c copy), no re-encoding'
VIDEO_SUFFIXES = ('.mp4', '.m4v', '.mkv', '.webm', '.mov', '.ts', '.flv', '.m4a', '.mp3', '.opus', '.ogg')


class PlayableError(Exception):
    """The copy could not be made; the message says why in plain words."""


def ffmpeg_path() -> Optional[str]:
    return shutil.which('ffmpeg')


def copy_name(src: Path) -> Path:
    target = src.with_name(f'{src.stem} (playable){src.suffix}')
    n = 2
    while target.exists():
        target = src.with_name(f'{src.stem} (playable {n}){src.suffix}')
        n += 1
    return target


def make_playable_copy(run_dir, rel_path: str, *, url: Optional[str] = None, ffmpeg: Optional[str] = None,
                       anchor_path: Optional[Path] = None, runner=subprocess.run) -> Path:
    run_dir = verify.find_run_dir(Path(run_dir))
    root = run_dir.parent.parent
    src = root / rel_path
    if not src.is_file():
        raise PlayableError(f'{rel_path} is not in the download folder any more.')
    if src.suffix.lower() not in VIDEO_SUFFIXES:
        raise PlayableError('Only video and audio files can get a playable copy.')
    sums_path = run_dir / 'SHA256SUMS.txt'
    sums = verify.read_sums(sums_path) if sums_path.is_file() else {}
    if sums.get(rel_path) and sha256_file(src) != sums[rel_path]:
        raise PlayableError(f'{rel_path} has changed since it was saved, so no copy was made. Run Verify files.')
    ffmpeg = ffmpeg or ffmpeg_path()
    if not ffmpeg:
        raise PlayableError('ffmpeg is not installed. Install it (winget install Gyan.FFmpeg), then try again.')
    target = copy_name(src)
    partial = target.with_name(target.name + '.partial' + target.suffix)
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        done = runner([ffmpeg, '-nostdin', '-loglevel', 'error', '-i', str(src), '-map', '0', '-c', 'copy',
                       '-n', str(partial)], capture_output=True, text=True, timeout=3600, creationflags=flags)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PlayableError(f'ffmpeg could not run: {exc}') from exc
    if done.returncode != 0 or not partial.is_file() or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        reason = (done.stderr or '').strip().splitlines()
        raise PlayableError('ffmpeg could not make a playable copy' + (f': {reason[-1]}' if reason else '.'))
    os.replace(partial, target)
    digest, size = sha256_file(target), target.stat().st_size
    rel_target = target.relative_to(root).as_posix()
    with open(sums_path, 'a', encoding='utf-8', newline='\n') as fp:
        fp.write(f'{digest}  {rel_target}\n')
    row = {'run_id': run_dir.name, 'input_url': url, 'status': 'derived', 'derived_from': rel_path, 'how': HOW,
           'files': [{'path': rel_target, 'bytes': size, 'sha256': digest, 'duplicate_of': None}],
           'finished_at': utc_now()}
    with open(run_dir / 'manifest.jsonl', 'a', encoding='utf-8', newline='\n') as fp:
        fp.write(json.dumps(row, ensure_ascii=False) + '\n')
        fp.flush()
        os.fsync(fp.fileno())
    log_path = run_dir / custody.FILENAME
    if log_path.exists():
        log = custody.CustodyLog(log_path, run_dir.name, anchor_path=anchor_path)
        log.append('copied', {'from': rel_path, 'to': rel_target, 'sha256': digest, 'bytes': size, 'how': HOW})
        log.append('hashed', {'path': rel_target, 'sha256': digest, 'bytes': size})
        if anchor_path:
            log.anchor_local()
    return target


__all__ = ['HOW', 'PlayableError', 'VIDEO_SUFFIXES', 'copy_name', 'ffmpeg_path', 'make_playable_copy']
