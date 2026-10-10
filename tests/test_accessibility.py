import re
import tempfile
import threading
import time
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from multidownloader import settings
from multidownloader.gui import help as help_menu, signin_window

from gui_gate import HAVE_DISPLAY, REASON as GUI_REASON, hidden_root


ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / 'multidownloader' / 'gui'


class _Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.sources = set(), []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        for key in ('src', 'href'):
            if tag in ('script', 'img', 'link') and attrs.get(key):
                self.sources.append(attrs[key])


class HelpPageTests(unittest.TestCase):
    def setUp(self):
        self.html = help_menu.HELP_FILE.read_text(encoding='utf-8')

    def test_every_section_and_no_internet_needed(self):
        parser = _Ids()
        parser.feed(self.html)
        self.assertEqual(set(help_menu.SECTIONS), parser.ids & set(help_menu.SECTIONS))
        self.assertEqual([], parser.sources)  # nothing loaded from elsewhere

    def test_every_shortcut_is_explained(self):
        for keys, action in help_menu.SHORTCUTS:
            with self.subTest(keys=keys):
                self.assertIn(action.lower(), self.html.lower())

    def test_plain_text_rules(self):
        self.assertIsNone(re.search('[' + chr(0x2013) + chr(0x2014) + ']| -- ', self.html))

    def test_help_url_lands_on_the_section(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(help_menu.tempfile, 'gettempdir',
                                                                    return_value=tmp):
            url = help_menu.help_url('signins')
            forward = Path(tmp) / 'multisource-downloader-help-signins.html'
            self.assertEqual(forward.resolve().as_uri(), url)
            self.assertIn('help.html#signins', forward.read_text(encoding='utf-8'))
            help_menu.help_url('nonsense')
            self.assertIn('#start', (Path(tmp) / 'multisource-downloader-help-start.html').read_text(encoding='utf-8'))

    def test_every_window_has_the_help_button(self):
        # A16: the (?) in the same corner of every window that has more than a yes or no
        for name, count in (('options.py', 1), ('signin_sheet.py', 3), ('instagram_login.py', 1), ('help.py', 2)):
            with self.subTest(file=name):
                self.assertGreaterEqual((GUI / name).read_text(encoding='utf-8').count("help_button(self")
                                        + (GUI / name).read_text(encoding='utf-8').count('help_button(dialog'), count)

    def test_paste_note_on_the_in_app_sign_in(self):
        self.assertIn('Your password manager may not fill this window. Paste works.',
                      (GUI / 'signin_sheet.py').read_text(encoding='utf-8'))


class _FakeWindow:
    def __init__(self):
        self.destroyed = False

    def get_current_url(self):
        return 'https://x.com/i/flow/login'

    def get_cookies(self):
        return []

    def destroy(self):
        self.destroyed = True


class SignInTimeoutTests(unittest.TestCase):
    """A15: the window closes after the time limit, and Keep waiting moves the limit."""

    def run_watch(self, timeout, touch_after=None):
        from multidownloader.platforms import BY_KEY
        events = []
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / 'out.json')
            if touch_after is not None:
                def touch():
                    time.sleep(touch_after)
                    Path(out + '.more').write_text('1', encoding='utf-8')
                threading.Thread(target=touch, daemon=True).start()
            window, state = _FakeWindow(), {}
            started = time.monotonic()
            with mock.patch.object(signin_window, 'POLL_SECONDS', 0.02), \
                    mock.patch.object(signin_window, 'emit', lambda event, **kw: events.append(event)):
                signin_window.watch(window, BY_KEY['x'], out, timeout, state)
            return events, time.monotonic() - started, state, window

    def test_times_out(self):
        events, elapsed, state, window = self.run_watch(0.2)
        self.assertEqual('timeout', events[-1])
        self.assertTrue(state['timed_out'] and window.destroyed)
        self.assertLess(elapsed, 1.5)

    def test_keep_waiting_extends(self):
        events, elapsed, _state, _window = self.run_watch(0.4, touch_after=0.25)
        self.assertIn('extended', events)
        self.assertEqual('timeout', events[-1])
        self.assertGreaterEqual(elapsed, 0.6)  # 0.25 s, then a new 0.4 s limit

    def test_same_limit_everywhere(self):
        self.assertEqual(600, signin_window.TIMEOUT_SECONDS)
        self.assertIn('timeout=600', (GUI / 'instagram_login.py').read_text(encoding='utf-8'))


class ShortcutTests(unittest.TestCase):
    def test_f1_is_help(self):
        self.assertIn(('F1', 'Help'), help_menu.SHORTCUTS)


@unittest.skipUnless(HAVE_DISPLAY, GUI_REASON)
class AccessibilityWindowTests(unittest.TestCase):
    """Hidden windows only: nothing appears or takes the focus."""

    def setUp(self):
        self.root = hidden_root(self)

    def test_fit_columns_and_sideways_scroll(self):
        from multidownloader.gui.queue_table import COLUMNS, QueueTable
        table = QueueTable(self.root)
        table.tree.column('link', width=900)
        table.fit_columns()
        widths = {key: int(table.tree.column(key, 'width')) for key, *_ in COLUMNS}
        self.assertLess(widths['link'], 900)
        self.assertTrue(str(table.tree.cget('xscrollcommand')))

    def test_options_has_the_help_button_top_right(self):
        from multidownloader.gui.options import OptionsWindow
        win = OptionsWindow(self.root, settings.defaults())
        win.withdraw()
        buttons = [w for w in win.winfo_children() if w.winfo_manager() == 'place']
        self.assertEqual(['?'], [b.cget('text') for b in buttons])
        self.assertEqual(1.0, float(buttons[0].place_info()['relx']))
        win.destroy()


if __name__ == '__main__':
    unittest.main()
