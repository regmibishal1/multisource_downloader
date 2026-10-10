"""Smoke test: real downloads, one or more per platform, to see what still works.

Platforms change without warning and break the downloaders the app uses (yt-dlp,
Instaloader, gallery-dl, gdown); unit tests cannot see that, only real downloads
can. This runs every link in scripts/smoke_links.json through the app's own code
(Run, Downloader, the custody log and verify) and compares each result with what
is expected.

Nothing of yours is touched: the app's data folder (history, anchors, logs) points
at a temporary folder for the whole run, and so does the download folder. With
--with-signins your sign-ins are copied into that temporary folder first, so even
the cookies yt-dlp writes back never reach the real ones.

    python scripts/smoke.py                  # public links, no sign-ins (what CI runs)
    python scripts/smoke.py --with-signins   # with a copy of your sign-ins
    python scripts/smoke.py --only tiktok,instagram --keep

Exit status: 0 every link as expected, 1 some link not, 2 the test could not run.
"""
from __future__ import annotations

import argparse
import json
import os
import platform as platform_mod
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
LINKS_FILE = Path(__file__).with_name('smoke_links.json')
LINK_TIMEOUT_S = 300   # one link may not take longer than this
PAUSE_S = 3            # between links
SAME_PLATFORM_PAUSE_S = 10  # between two links of one platform (Instagram is strict)
PACKAGES = ('yt-dlp', 'yt-dlp-ejs', 'instaloader', 'gdown', 'google-auth-oauthlib', 'gallery-dl')
# the app's own folders (appdirs.py); not APPDATA, where Firefox keeps the profiles TikTok's User-Agent comes from
ENV_KEYS = ('LOCALAPPDATA', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME')


@dataclass
class Outcome:
    platform: str
    url: str
    expect: str
    status: str = 'not run'       # saved | already_saved | failed | skipped | stopped | not run | timed out
    kind: Optional[str] = None    # the app's error kind for a failure
    message: str = ''
    backend: Optional[str] = None
    files: int = 0
    seconds: float = 0.0
    note: str = ''
    ok: bool = False


@dataclass
class Report:
    started_at: str
    mode: str
    environment: Dict[str, str] = field(default_factory=dict)
    outcomes: List[Outcome] = field(default_factory=list)
    verify: str = ''

    @property
    def failures(self) -> List[Outcome]:
        return [o for o in self.outcomes if not o.ok and o.status != 'not run']

    @property
    def ok(self) -> bool:
        return not self.failures and self.verify.startswith('ok')


# --- expectations -------------------------------------------------------------------------

def load_links(path: Path = LINKS_FILE) -> List[Dict]:
    data = json.loads(path.read_text(encoding='utf-8'))
    links = data['links'] if isinstance(data, dict) else data
    for link in links:
        if not link.get('url') or not link.get('expect'):
            raise ValueError(f'every link needs "url" and "expect": {link}')
        if link['expect'] not in ('saved', 'skipped') and not link['expect'].startswith('failed:'):
            raise ValueError(f'unknown expectation {link["expect"]!r} for {link["url"]}')
    return links


def meets(expect: str, status: str, kind: Optional[str]) -> bool:
    if expect == 'saved':
        return status in ('saved', 'already_saved')
    if expect == 'skipped':
        return status == 'skipped'
    return status == 'failed' and kind == expect.split(':', 1)[1]


def selected(links: List[Dict], only: Optional[List[str]], with_signins: bool, ci: bool) -> List[Dict]:
    picked = []
    for link in links:
        if only and (link.get('platform') or 'other') not in only:
            continue
        reason = ''
        if link.get('signin') == 'required' and not with_signins:
            reason = 'needs a sign-in (run with --with-signins)'
        elif ci and link.get('ci') is False:
            reason = 'not run on CI: ' + (link.get('ci_note') or 'the platform blocks CI networks')
        picked.append(dict(link, skip_reason=reason))
    return picked


# --- isolation ---------------------------------------------------------------------------------

def isolate(tmp: Path, with_signins: bool) -> Optional[Path]:
    """Point every per-user folder at tmp before the app's modules are imported. Returns the copied
    sign-ins folder, if any."""
    sys.path.insert(0, str(ROOT))
    from multidownloader.appdirs import data_dir  # reads the environment when called, imports nothing else
    real_sessions = data_dir() / 'sessions'
    for name in ENV_KEYS:
        os.environ[name] = str(tmp / 'data')
    copied = None
    if with_signins:
        if not real_sessions.is_dir():
            raise SystemExit('No sign-ins found to copy. Run without --with-signins.')
        copied = data_dir() / 'sessions'
        shutil.copytree(real_sessions, copied)
    return copied


def environment(mode: str) -> Dict[str, str]:
    from multidownloader.backends import gallerydl
    found = {'python': platform_mod.python_version(), 'system': platform_mod.platform(), 'mode': mode}
    for name in PACKAGES:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            found[name] = 'not installed'
    if found['gallery-dl'] == 'not installed' and gallerydl.command():
        try:
            out = subprocess.run(gallerydl.command() + ['--version'], capture_output=True, text=True, timeout=30)
            found['gallery-dl'] = out.stdout.strip() or 'installed'
        except (OSError, subprocess.SubprocessError):
            found['gallery-dl'] = 'installed'
    for tool in ('ffmpeg', 'deno'):
        found[tool] = 'found' if shutil.which(tool) else 'not found'
    from multidownloader import signins
    found['firefox user agent'] = 'found' if signins.own_firefox_user_agent() else 'not found'
    return found


# --- running -----------------------------------------------------------------------------------

def run_link(run, downloader, link: Dict, timeout: float = LINK_TIMEOUT_S) -> Outcome:
    from multidownloader import detect, settings

    outcome = Outcome(link.get('platform') or 'other', link['url'], link['expect'], note=link.get('note', ''))
    if link.get('skip_reason'):
        outcome.message = link['skip_reason']
        return outcome
    platform = detect.platform_for_url(link['url'])
    started = time.monotonic()
    if platform is None:
        result = run.skip(link['url'], 'unsupported')
    else:
        options = settings.download_options(settings.defaults(), platform.handler)
        cancel = threading.Event()
        options['cancel_event'] = cancel
        box = {}
        worker = threading.Thread(target=lambda: box.update(result=run.download(
            downloader, platform.handler, link['url'], options)), daemon=True)
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            cancel.set()  # yt-dlp and Instaloader stop at their next check
            worker.join(30)
            outcome.status, outcome.seconds = 'timed out', round(time.monotonic() - started, 1)
            outcome.message = f'no result after {int(timeout)} s'
            return outcome
        result = box['result']
    outcome.seconds = round(time.monotonic() - started, 1)
    outcome.status, outcome.kind = result.status, result.error_kind
    outcome.message = result.error_message or (result.skip_reason or '')
    outcome.backend, outcome.files = result.backend, len(result.files)
    outcome.ok = meets(link['expect'], outcome.status, outcome.kind)
    return outcome


def run_all(links: List[Dict], out_root: Path, anchors: Path, on_outcome=None) -> Report:
    from multidownloader import verify
    from multidownloader.core import Downloader
    from multidownloader.runs import Run, utc_now

    report = Report(started_at=utc_now(), mode='')
    run = Run(out_root, anchor_path=anchors)
    downloader = Downloader(str(out_root))
    previous = None
    try:
        for link in links:
            if previous is not None and not link.get('skip_reason'):
                time.sleep(SAME_PLATFORM_PAUSE_S if link.get('platform') == previous else PAUSE_S)
            outcome = run_link(run, downloader, link)
            report.outcomes.append(outcome)
            if on_outcome:
                on_outcome(outcome)
            if not link.get('skip_reason'):
                previous = link.get('platform')
    finally:
        run.finish()
    checked = verify.verify(run.dir, check_custody=True, anchor_path=anchors)
    report.verify = ('ok: ' if checked.ok else 'FAILED: ') + ' '.join(checked.lines())
    return report


# --- reporting ---------------------------------------------------------------------------------

def mark(outcome: Outcome) -> str:
    if outcome.status == 'not run':
        return 'not run'
    return 'ok' if outcome.ok else 'BROKEN'


def table(report: Report) -> str:
    rows = [f'{"result":8} {"platform":10} {"status":14} {"time":>7}  details']
    for o in report.outcomes:
        details = o.message or (f'{o.files} file(s) by {o.backend}' if o.files else '')
        expect = '' if o.ok or o.status == 'not run' else f'  (expected {o.expect})'
        rows.append(f'{mark(o):8} {o.platform:10} {o.status:14} {o.seconds:6.1f}s  {details[:90]}{expect}')
    rows.append(f'verify: {report.verify[:160]}')
    return '\n'.join(rows)


def markdown(report: Report) -> str:
    broken = report.failures
    lines = [f'Smoke test {report.started_at} ({report.mode}): '
             + ('every link worked as expected.' if report.ok else f'{len(broken)} link(s) not as expected.'), '',
             '| Result | Platform | Status | Details | Link |', '|---|---|---|---|---|']
    for o in report.outcomes:
        details = (o.message or (f'{o.files} file(s) by {o.backend}' if o.files else '')).replace('|', '/')
        if not o.ok and o.status != 'not run':
            details += f' (expected {o.expect})'
        lines.append(f'| {mark(o)} | {o.platform} | {o.status} | {details[:150]} | {o.url} |')
    lines += ['', f'Verify: {report.verify}', '', 'Versions: ' + ', '.join(f'{k} {v}' for k, v in
                                                                         report.environment.items())]
    return '\n'.join(lines) + '\n'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--with-signins', action='store_true', help='Use a temporary copy of your sign-ins')
    parser.add_argument('--only', help='Comma-separated platforms, for example tiktok,instagram')
    parser.add_argument('--ci', action='store_true', help='Skip links marked "ci": false (platforms that block CI)')
    parser.add_argument('--links', type=Path, default=LINKS_FILE, help='Another links file')
    parser.add_argument('--json', type=Path, help='Write the report as JSON here')
    parser.add_argument('--markdown', type=Path, help='Write the report as Markdown here (for an issue)')
    parser.add_argument('--keep', action='store_true', help='Keep the temporary folder and say where it is')
    args = parser.parse_args(argv)
    try:
        links = selected(load_links(args.links), args.only.split(',') if args.only else None, args.with_signins,
                         args.ci)
    except (OSError, ValueError, KeyError) as exc:
        print(f'Could not read the links: {exc}')
        return 2
    tmp = Path(tempfile.mkdtemp(prefix='msdl-smoke-'))
    saved_env = {name: os.environ.get(name) for name in ENV_KEYS}
    try:
        isolate(tmp, args.with_signins)
        from multidownloader import session_store
        if tmp.resolve() not in Path(session_store.SESSION_ROOT).resolve().parents:
            print('Stopped: the sign-ins folder is not the temporary one, so nothing was run.')
            return 2
        mode = 'with a copy of your sign-ins' if args.with_signins else 'no sign-ins'
        env = environment(mode + (', CI' if args.ci else ''))
        print('Smoke test, ' + ', '.join(f'{k} {v}' for k, v in env.items()), flush=True)

        def show(o):
            print(f'  {mark(o):8} {o.platform:10} {o.status:14} {o.seconds:6.1f}s  {(o.message or "")[:80]}',
                  flush=True)

        report = run_all(links, tmp / 'downloads', tmp / 'data' / 'anchors.log', on_outcome=show)
        report.mode, report.environment = env['mode'], env
    finally:
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if args.keep:
            print(f'Kept: {tmp}')
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    print()
    print(table(report))
    if args.json:
        args.json.write_text(json.dumps(dict(asdict(report), ok=report.ok), indent=2), encoding='utf-8')
    if args.markdown:
        args.markdown.write_text(markdown(report), encoding='utf-8')
    print('\n' + ('Every link worked as expected.' if report.ok else
                  f'{len(report.failures)} link(s) not as expected.'))
    return 0 if report.ok else 1


if __name__ == '__main__':
    sys.exit(main())
