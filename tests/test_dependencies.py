"""requirements.txt, requirements-lock.txt and pyproject.toml must agree, and the public check must work."""
import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_public  # noqa: E402


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def pins(path):
    found = {}
    for line in (ROOT / path).read_text(encoding='utf-8').splitlines():
        line = line.split(';')[0].strip()
        if line and not line.startswith('#') and '==' in line:
            name, version = line.split('==', 1)
            found[canonical(name)] = version.strip()
    return found


class AgreementTests(unittest.TestCase):
    def test_requirements_match_the_snapshot(self):
        lock = pins('requirements-lock.txt')
        for name, version in pins('requirements.txt').items():
            with self.subTest(package=name):
                self.assertIn(name, lock, 'pinned in requirements.txt but missing from the snapshot')
                self.assertEqual(lock[name], version, 'requirements.txt and requirements-lock.txt disagree')

    def test_snapshot_records_tools_and_no_machine_details(self):
        text = (ROOT / 'requirements-lock.txt').read_text(encoding='utf-8')
        for field in ('# Snapshot taken:', '# Python:', '# ffmpeg:', '# Deno:'):
            self.assertIn(field, text)
        self.assertEqual([], list(check_public.scan_text('requirements-lock.txt', text)))

    @unittest.skipUnless(importlib.util.find_spec('packaging'), 'packaging not installed')
    def test_pinned_versions_meet_pyproject_minimums(self):
        from packaging.requirements import Requirement
        try:
            import tomllib
        except ImportError:  # Python 3.10
            self.skipTest('tomllib needs Python 3.11')
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
        required = list(project['dependencies'])
        for extra, items in project['optional-dependencies'].items():
            required += [r for r in items if not r.startswith('multisource-downloader')]
        pinned = pins('requirements.txt')
        for text in required:
            req = Requirement(text)
            with self.subTest(requirement=text):
                if canonical(req.name) == 'ruff':
                    continue  # a development tool, not pinned for penv
                self.assertIn(canonical(req.name), pinned, 'used by the app but not pinned in requirements.txt')
                self.assertTrue(req.specifier.contains(pinned[canonical(req.name)], prereleases=True),
                                f'{req.name} {pinned[canonical(req.name)]} does not satisfy {req.specifier}')

    def test_dependencies_log_mentions_every_pinned_backend(self):
        log = (ROOT / 'DEPENDENCIES.md').read_text(encoding='utf-8')
        for name in ('yt-dlp', 'gdown', 'pywebview', 'gallery-dl'):
            self.assertIn(f'`{name}`', log)


class PublicCheckTests(unittest.TestCase):
    """The checker must catch these; the samples are built so this file itself stays clean."""

    def problems(self, text):
        return list(check_public.scan_text('sample', text))

    def test_catches_private_and_unwanted_text(self):
        samples = {
            'a cookies.txt line': '.instagram.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tabc123',
            'an e-mail address': 'contact ' + 'someone' + '@' + 'gmail.com',
            'a Windows home folder with a user name': 'C:' + '\\Users\\' + 'bob\\Downloads',
            'a home folder path': '/home/' + 'bob/archive',
            'a GitHub token': 'gh' + 'p_' + 'a' * 30,
            'an AI mention or attribution': 'Co-' + 'Authored-By: someone',
            'an en or em dash': 'one ' + '\u2014' + ' two',
            'a double hyphen used as a dash': 'one -' + '- two',
        }
        for what, text in samples.items():
            with self.subTest(what=what):
                found = self.problems(text)
                self.assertTrue(any(what in p for p in found), found)

    def test_allows_ordinary_text(self):
        for text in ('Run `--resume` to finish it.', 'C:\\Users\\you\\Downloads', '%LOCALAPPDATA%\\x',
                     'see https://example.org/a', 'auth_token=[redacted]', 'x.com/i/status/1',
                     'mail@example.com', 'python -m pip install -U yt-dlp'):
            with self.subTest(text=text):
                self.assertEqual([], self.problems(text))

    def test_marker_allows_a_deliberate_fake(self):
        self.assertEqual([], self.problems('fake ' + 'a' + '@' + 'gmail.com  # public-check: ok'))

    def test_forbidden_files(self):
        root = check_public.ROOT
        names = ['multidownloader/.sessions/x/cookies.txt', 'cookies.txt', 'archiver.session', 'client_secrets.json',
                 'downloader.log']
        problems = check_public.scan_files([root / n for n in names])
        flagged = {p.split(':', 1)[0] for p in problems if 'must never be committed' in p}
        self.assertEqual(set(names), flagged)

    def test_repository_is_clean(self):
        self.assertEqual([], check_public.scan_files(check_public.tracked_files()))


if __name__ == '__main__':
    unittest.main()
