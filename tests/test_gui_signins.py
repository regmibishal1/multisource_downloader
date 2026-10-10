import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import session_store, signins
from multidownloader.platforms import BY_KEY

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.withdraw()
    _root.destroy()
    HAVE_DISPLAY = True
except Exception:  # no Tk or no display (headless CI)
    HAVE_DISPLAY = False


def texts(widget):
    found = []
    for child in widget.winfo_children():
        try:
            text = child.cget('text')
        except Exception:
            text = ''
        if text:
            found.append(text)
        found.extend(texts(child))
    return found


@unittest.skipUnless(HAVE_DISPLAY, 'needs a display')
class SignInWindowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(session_store, 'SESSION_ROOT', Path(tmp.name) / '.sessions')
        patcher.start()
        self.addCleanup(patcher.stop)
        # never read a real browser from tests
        finder = mock.patch.object(signins, 'find_firefox_signins', return_value=[])
        finder.start()
        self.addCleanup(finder.stop)
        profiles = mock.patch.object(signins, 'firefox_profiles', return_value=[])
        profiles.start()
        self.addCleanup(profiles.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def pump(self, widget, seconds=0.5):
        end = time.time() + seconds
        while time.time() < end:
            widget.update()

    def wait_for(self, widget, condition, timeout=5.0):
        """Run the Tk loop until condition() is true; fail after timeout."""
        end = time.time() + timeout
        while time.time() < end:
            widget.update()
            if condition():
                return
            time.sleep(0.02)
        self.fail('timed out waiting for the window to update')

    def test_manager_lists_every_platform_with_status_and_actions(self):
        from multidownloader.gui.signin_sheet import SignInsWindow
        x = BY_KEY['x']
        signins.import_cookies_text(x, '.x.com\tTRUE\t/\tTRUE\t0\tauth_token\tA\n', terms_version=x.terms.version)
        window = SignInsWindow(self.root)
        shown = texts(window)
        window.destroy()
        for platform in BY_KEY.values():
            self.assertIn(platform.name, shown)
        self.assertIn('✓ Signed in via cookies file', shown)
        self.assertIn('Not set up', shown)  # Instagram
        self.assertIn('Remove all sign-ins...', shown)
        self.assertEqual(1, shown.count('Remove'))

    def test_sheet_shows_terms_and_blocks_continue_until_acknowledged(self):
        from multidownloader.gui.signin_sheet import SignInSheet
        ig = BY_KEY['instagram']
        sheet = SignInSheet(self.root, ig)
        self.pump(sheet)
        shown = texts(sheet)
        self.assertIn(ig.terms.notice, shown)
        self.assertIn("Read Instagram's terms", shown)
        self.assertNotIn('Recommended', ' '.join(shown))
        self.assertFalse(sheet.ack_var.get())
        sheet.file_text = '.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tS\n'
        sheet.method_var.set('file')
        sheet._update_continue()
        self.assertIn('disabled', sheet.continue_btn.state())
        sheet.ack_var.set(True)
        sheet._update_continue()
        self.assertNotIn('disabled', sheet.continue_btn.state())

        sheet._continue()
        self.wait_for(sheet, lambda: '✓ Signed in to Instagram.' in texts(sheet))
        self.assertEqual('ready', signins.status(ig).state)
        sheet.destroy()

    def test_sheet_shows_the_files_error_message(self):
        from multidownloader.gui.signin_sheet import SignInSheet
        x = BY_KEY['x']
        sheet = SignInSheet(self.root, x)
        sheet.ack_var.set(True)
        sheet.file_text = '.x.com\tTRUE\t/\tTRUE\t0\tguest_id\tg\n'
        sheet.method_var.set('file')
        sheet._update_continue()
        sheet._continue()
        self.wait_for(sheet, lambda: sheet.message_var.get().startswith(
            '✕ This file does not contain an X (Twitter) sign-in'))
        self.assertFalse(signins.cookie_path(x).exists())
        sheet.destroy()

    def test_sign_in_here_card(self):
        from multidownloader.gui.signin_sheet import SignInSheet
        with mock.patch.object(signins, 'webview_available', return_value=(True, '')):
            sheet = SignInSheet(self.root, BY_KEY['instagram'])
            self.pump(sheet)
            self.assertIn('Sign in here', texts(sheet))
            # Firefox has nothing, so the in-app window is preselected
            self.assertEqual('webview', sheet.method_var.get())
            sheet.destroy()
            sheet = SignInSheet(self.root, BY_KEY['youtube'])
            self.assertNotIn('Sign in here', texts(sheet))  # Google blocks embedded sign-in
            sheet.destroy()
        with mock.patch.object(signins, 'webview_available', return_value=(False, signins.WEBVIEW2_MESSAGE)):
            sheet = SignInSheet(self.root, BY_KEY['x'])
            shown = texts(sheet)
            self.assertIn(signins.WEBVIEW2_MESSAGE, shown)
            self.assertIn('Get it', shown)
            self.assertIn('disabled', sheet.webview_radio.state())
            sheet.destroy()

    def test_companion_shows_the_page_and_saves_the_result(self):
        import json

        from multidownloader.gui.signin_sheet import SignInSheet
        ig = BY_KEY['instagram']
        with mock.patch.object(signins, 'webview_available', return_value=(True, '')):
            sheet = SignInSheet(self.root, ig)
        sheet._show_companion()
        sheet._show_page('https://www.instagram.com/accounts/login/')
        self.assertEqual('Page: www.instagram.com, secure (https)', sheet.page_var.get())
        sheet._show_page('http://phish.example/login')
        self.assertEqual('! Unexpected page: phish.example', sheet.page_var.get())

        out = signins.folder(ig) / '.signin-test.json'
        out.write_text(json.dumps({'cookies': [{'name': 'sessionid', 'value': 'S', 'domain': '.instagram.com'}],
                                   'user_agent': 'UA'}), encoding='utf-8')
        sheet._webview_out = str(out)
        sheet._webview_done = True
        sheet.ack_var.set(True)
        sheet._webview_finished()
        self.wait_for(sheet, lambda: '✓ Signed in to Instagram.' in texts(sheet))
        self.assertFalse(out.exists())  # the hand-off file is deleted
        self.assertEqual('UA', signins.read_meta(ig)['user_agent'])
        sheet.destroy()

    def test_closed_window_returns_to_the_cards(self):
        from multidownloader.gui.signin_sheet import SignInSheet
        with mock.patch.object(signins, 'webview_available', return_value=(True, '')):
            sheet = SignInSheet(self.root, BY_KEY['x'])
        sheet._show_companion()
        sheet._webview_done = False
        sheet._webview_finished()
        self.assertTrue(sheet.cards.winfo_ismapped() or sheet.cards.grid_info())
        self.assertIn('closed or timed out', sheet.message_var.get())
        sheet.destroy()

    def test_cancel_is_not_an_error(self):
        from multidownloader.gui.signin_sheet import SignInSheet
        with mock.patch.object(signins, 'webview_available', return_value=(True, '')):
            sheet = SignInSheet(self.root, BY_KEY['x'])
        sheet._show_companion()
        proc = mock.Mock()
        proc.poll.return_value = None
        sheet._webview_proc = proc
        sheet._webview_done = False
        sheet._webview_cancelled = False
        sheet._cancel_webview()
        proc.terminate.assert_called_once()
        sheet._webview_finished()  # what the reader thread triggers after exit
        self.assertEqual('', sheet.message_var.get())
        sheet.destroy()

    def test_drive_sheet_offers_the_cloud_project(self):
        from multidownloader.gui.signin_sheet import SignInSheet
        sheet = SignInSheet(self.root, BY_KEY['gdrive'])
        shown = texts(sheet)
        self.assertIn('Connect with your own Google Cloud project', shown)
        self.assertNotIn('Sign in here', shown)
        sheet.method_var.set('oauth')
        sheet._update_continue()
        self.assertNotIn('disabled', sheet.continue_btn.state())  # Drive has no terms notice
        sheet.destroy()

    def test_password_option_only_for_instagram(self):
        from multidownloader.gui.signin_sheet import SignInSheet
        for key, expected in (('instagram', True), ('x', False), ('facebook', False)):
            sheet = SignInSheet(self.root, BY_KEY[key])
            self.assertEqual(expected, 'More ways to sign in' in texts(sheet), key)
            sheet.destroy()


if __name__ == '__main__':
    unittest.main()
