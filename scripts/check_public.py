"""Check that nothing private or unwanted goes into the public repository.

    python scripts/check_public.py                  # tracked files
    python scripts/check_public.py --commits RANGE  # also commit messages, e.g. origin/master..HEAD
    python scripts/check_public.py --text FILE      # also a PR description before posting it

It fails (exit 1) on:
- secrets: cookies.txt lines, session or credential files, private keys, tokens;
- personal data: e-mail addresses, home folder paths with a user name;
- AI attribution or mentions in the project's text and commits;
- em and en dashes (and " -- " used as one) in prose.

Run it before every push; CI runs it on every pull request.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
BINARY_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.exe', '.zip', '.pyc', '.sqlite'}

FORBIDDEN_FILES = (
    (re.compile(r'(^|/)\.sessions/'), 'a sign-in folder'),
    (re.compile(r'(^|/)cookies[^/]*\.txt$', re.IGNORECASE), 'a cookies file'),
    (re.compile(r'\.session$'), 'an Instaloader session'),
    (re.compile(r'(^|/)(client_secrets|credentials)\.json$'), 'Google credentials'),
    (re.compile(r'(^|/)\.env$'), 'an environment file'),
    (re.compile(r'(^|/)(app|downloader)\.log$'), 'a log file'),
)

CONTENT_RULES: Tuple[Tuple[str, re.Pattern, str], ...] = (
    ('secret', re.compile(r'^(#HttpOnly_)?\.?[\w.-]+\.[a-z]{2,}\t(TRUE|FALSE)\t/[^\t]*\t(TRUE|FALSE)\t\d+\t\S+\t\S+',
                          re.IGNORECASE | re.MULTILINE), 'a cookies.txt line'),
    ('secret', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'), 'a private key'),
    ('secret', re.compile(r'\b(ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}'), 'a GitHub token'),
    ('secret', re.compile(r'\bAKIA[0-9A-Z]{16}\b'), 'an AWS key'),
    ('secret', re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b'), 'a Google API key'),
    ('secret', re.compile(r'"client_secret"\s*:\s*"(?!x"|s"|SECRET")[^"]{8,}"'), 'an OAuth client secret'),
    ('personal', re.compile(r'[A-Za-z0-9._%+-]+@(?!example\.(com|org)\b)[A-Za-z0-9.-]+\.(com|net|org|io|dev|np)\b'),
     'an e-mail address'),
    ('personal', re.compile(r'(?i)\b[A-Z]:[\\/]+Users[\\/]+(?!u[\\/]|you[\\/]|<|\{|%|NAME|USER)[^\\/\s"\']+'),
     'a Windows home folder with a user name'),
    ('personal', re.compile(r'/(home|Users)/(?!u/|you/|runner/|<|\$|USER|user/)[a-z][\w.-]+/'), 'a home folder path'),
    ('ai', re.compile(r'(?i)\b(claude|anthropic|chatgpt|openai|copilot)\b|co-authored-by|generated (with|by) ai'),
     'an AI mention or attribution'),
    ('dash', re.compile('[–—]'), 'an en or em dash'),
    ('dash', re.compile(r'[A-Za-z,.)] -- [A-Za-z(]'), 'a double hyphen used as a dash'),
)


def tracked_files() -> List[Path]:
    out = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True).stdout
    return [ROOT / name for name in out.decode('utf-8').split('\0') if name]


ALLOW_MARKER = 'public-check: ok'  # on a line: a deliberate fake (test data), not a real value


def scan_text(label: str, text: str) -> Iterable[str]:
    lines = text.splitlines()
    for _kind, pattern, what in CONTENT_RULES:
        for match in pattern.finditer(text):
            line_no = text.count('\n', 0, match.start()) + 1
            if line_no <= len(lines) and ALLOW_MARKER in lines[line_no - 1]:
                continue
            snippet = match.group(0)[:40].replace('\t', '\\t')
            yield f'{label}:{line_no}: {what} ({snippet!r})'


def scan_files(files: Iterable[Path]) -> List[str]:
    problems = []
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        for pattern, what in FORBIDDEN_FILES:
            if pattern.search(rel):
                problems.append(f'{rel}: {what} must never be committed')
        if path.resolve() == SELF or path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        problems.extend(scan_text(rel, text))
    return problems


def scan_commits(rev_range: str) -> List[str]:
    out = subprocess.run(['git', 'log', '--format=%H%x00%an <%ae>%x00%B%x01', rev_range], cwd=ROOT,
                         capture_output=True, text=True, encoding='utf-8', check=True).stdout
    problems = []
    for record in filter(None, (r.strip() for r in out.split('\x01'))):
        sha, _author, body = record.split('\x00', 2)
        problems.extend(p for p in scan_text(f'commit {sha[:7]}', body) if 'e-mail' not in p)
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--commits', metavar='RANGE', help='also check commit messages in this git range')
    parser.add_argument('--text', metavar='FILE', action='append', default=[],
                        help='also check this text file (e.g. a PR description)')
    args = parser.parse_args(argv)
    problems = scan_files(tracked_files())
    if args.commits:
        problems += scan_commits(args.commits)
    for name in args.text:
        problems += list(scan_text(name, Path(name).read_text(encoding='utf-8')))
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f'{len(problems)} problem(s): fix them before pushing.', file=sys.stderr)
        return 1
    print('Public check passed: no secrets, personal data, AI mentions or dashes found.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
