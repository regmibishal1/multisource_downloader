import re
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import settings
from multidownloader.gui import help as help_menu, options, theme

from gui_gate import HAVE_DISPLAY, REASON as GUI_REASON

if HAVE_DISPLAY:
    import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]


def luminance(color: str) -> float:
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    light, dark = sorted((luminance(a), luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


class PaletteTests(unittest.TestCase):
    def test_same_tokens_in_both_palettes(self):
        self.assertEqual(set(theme.LIGHT), set(theme.DARK))
        self.assertEqual(set(theme.LIGHT), set(theme.HIGH_CONTRAST))

    def test_contrast(self):
        # WCAG 1.4.3 text 4.5:1 and 1.4.11 control borders 3:1 (brief 5.3)
        for name, palette in (('light', theme.LIGHT), ('dark', theme.DARK)):
            for background in ('bg', 'surface'):
                for token in ('text', 'text-2', 'accent', 'success', 'error', 'warning'):
                    with self.subTest(palette=name, token=token, background=background):
                        self.assertGreaterEqual(contrast(palette[token], palette[background]), 4.5)
                with self.subTest(palette=name, token='border', background=background):
                    self.assertGreaterEqual(contrast(palette['border'], palette[background]), 3.0)
            with self.subTest(palette=name, token='on-accent'):
                self.assertGreaterEqual(contrast(palette['on-accent'], palette['accent']), 4.5)
            with self.subTest(palette=name, token='warning on bar'):
                self.assertGreaterEqual(contrast(palette['warning'], palette['bar']), 4.5)

    def test_resolve_mode(self):
        self.assertEqual('dark', theme.resolve_mode('dark'))
        self.assertEqual('light', theme.resolve_mode('light'))
        with mock.patch.object(theme, 'system_prefers_dark', return_value=True):
            self.assertEqual('dark', theme.resolve_mode('system'))
        with mock.patch.object(theme, 'system_prefers_dark', return_value=False):
            self.assertEqual('light', theme.resolve_mode('system'))

    def test_px(self):
        with mock.patch.dict(theme.STATE, scale=1.5):
            self.assertEqual(36, theme.px(24))
            self.assertEqual(12, theme.px(8))
        with mock.patch.dict(theme.STATE, scale=1.0):
            self.assertEqual(24, theme.px(24))

    def test_system_checks_never_raise(self):
        self.assertIsInstance(theme.system_prefers_dark(), bool)
        self.assertIsInstance(theme.high_contrast_on(), bool)


class OptionsLogicTests(unittest.TestCase):
    def test_updated_keeps_good_values(self):
        prefs = settings.defaults()
        new = options.updated(prefs, {'quality': '720', 'layout': 'flat', 'parallel': '5', 'polite': False,
                                      'theme': 'dark', 'subtitles': True, 'output_root': 'ignored'})
        self.assertEqual(('720', 'flat', 5, False, 'dark', True),
                         (new['quality'], new['layout'], new['parallel'], new['polite'], new['theme'],
                          new['subtitles']))
        self.assertEqual(prefs['output_root'], new['output_root'])  # not this window's setting
        self.assertEqual('best', prefs['quality'])  # the input is not changed

    def test_updated_ignores_bad_values(self):
        prefs = settings.defaults()
        new = options.updated(prefs, {'quality': '4k', 'layout': 'nested', 'parallel': 'many', 'theme': 'blue'})
        self.assertEqual(prefs, new)
        self.assertEqual(6, options.updated(prefs, {'parallel': 40})['parallel'])  # clamped to the runner's limit

    def test_every_choice_is_a_valid_setting(self):
        for value, _text in options.QUALITY_LABELS:
            self.assertTrue(settings.valid('quality', value))
        for value, _text, _example in options.LAYOUT_LABELS:
            self.assertTrue(settings.valid('layout', value))
        for value, _text in options.THEME_LABELS:
            self.assertTrue(settings.valid('theme', value))
        self.assertEqual(set(settings.QUALITIES), {v for v, _t in options.QUALITY_LABELS})
        self.assertEqual(set(settings.LAYOUTS), {v for v, _t, _e in options.LAYOUT_LABELS})
        self.assertEqual(set(settings.THEMES), {v for v, _t in options.THEME_LABELS})
        self.assertFalse(settings.valid('surprise', 1))

    def test_download_options_follow_prefs(self):
        prefs = dict(settings.defaults(), quality='audio', thumbnails=False, layout='platform', include_title=True)
        opts = settings.download_options(prefs, 'YouTube')
        self.assertEqual(('audio', False, 'platform', True, True),
                         (opts['quality'], opts['thumbnails'], opts['layout'], opts['include_title'],
                          opts['use_session']))
        self.assertEqual('auto', settings.download_options(prefs, 'Instagram')['auth'])
        self.assertNotIn('use_session', settings.download_options(prefs, 'Google Drive'))


class RunUsesOptionsTests(unittest.TestCase):
    """The window's download thread takes parallel, polite, keep awake and the per-link options from prefs."""

    def run_with(self, prefs):
        import multisource_downloader_ui as ui
        fake = mock.Mock()
        fake._events = mock.Mock()
        run = mock.Mock(results=[])
        with mock.patch.object(ui, 'Runner') as runner_cls, mock.patch.object(ui, 'keep_awake') as awake:
            runner_cls.return_value.run.return_value = []
            runner_cls.return_value.stopped = False
            ui.DownloaderUI._download_with_core(fake, mock.Mock(), run,
                                                [('https://youtu.be/aaaaaaaaaaa', 'YouTube')], None, prefs)
        jobs = runner_cls.return_value.run.call_args.args[0]
        return runner_cls.call_args.kwargs, awake.call_args.args, jobs

    def test_prefs_reach_the_runner(self):
        prefs = dict(settings.defaults(), parallel=2, polite=False, keep_awake=False, quality='720')
        kwargs, awake_args, jobs = self.run_with(prefs)
        self.assertEqual(2, kwargs['max_parallel'])
        self.assertEqual((0.0, 0.0), kwargs['default_delay'])
        self.assertEqual((False,), awake_args)
        self.assertEqual('720', jobs[0].options['quality'])

    def test_polite_keeps_the_default_pause(self):
        kwargs, awake_args, _jobs = self.run_with(settings.defaults())
        self.assertIsNone(kwargs['default_delay'])
        self.assertEqual((True,), awake_args)


class ShortcutListTests(unittest.TestCase):
    def test_listed_shortcuts_are_bound(self):
        source = (ROOT / 'multisource_downloader_ui.py').read_text(encoding='utf-8')
        bound = {'Ctrl+L': '<Control-l>', 'Ctrl+O': '<Control-o>', 'Ctrl+Enter': '<Control-Return>',
                 'Ctrl+.': '<Control-period>', 'F5': '<F5>', 'Ctrl+Shift+O': '<Control-O>',
                 'Ctrl+,': '<Control-comma>', 'F6': '<F6>', 'F1': '<F1>', 'Delete': '<Delete>'}
        listed = {keys for keys, _action in help_menu.SHORTCUTS}
        for keys, sequence in bound.items():
            with self.subTest(keys=keys):
                self.assertIn(keys, listed)
                self.assertIn(f"'{sequence}'", source)

    def test_no_dashes_in_window_text(self):
        for path in ('multidownloader/gui/options.py', 'multidownloader/gui/help.py'):
            text = (ROOT / path).read_text(encoding='utf-8')
            self.assertIsNone(re.search('[' + chr(0x2013) + chr(0x2014) + ']', text), path)


@unittest.skipUnless(HAVE_DISPLAY, GUI_REASON)
class OptionsWindowTests(unittest.TestCase):
    """Hidden windows only: nothing appears or takes the focus."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def window(self, prefs, on_save=None):
        win = options.OptionsWindow(self.root, prefs, on_save=on_save)
        win.withdraw()
        return win

    def test_save_cancel_and_restore(self):
        prefs = settings.defaults()
        saved = []
        win = self.window(prefs, on_save=saved.append)
        self.assertEqual('best', win.vars['quality'].get())
        self.assertEqual('3', win.vars['parallel'].get())
        win.vars['quality'].set('1080')
        win.vars['subtitles'].set(True)
        win.vars['parallel'].set('4')
        win.save()
        self.assertEqual(('1080', True, 4), (prefs['quality'], prefs['subtitles'], prefs['parallel']))
        self.assertEqual([prefs], saved)

        win = self.window(prefs, on_save=saved.append)
        win.vars['quality'].set('audio')
        win.destroy()  # Cancel
        self.assertEqual('1080', prefs['quality'])

        win = self.window(prefs)
        win.restore_defaults()
        self.assertEqual('best', win.vars['quality'].get())
        win.destroy()
        self.assertEqual('1080', prefs['quality'])  # restore only fills the window until Save

    def test_main_window_regions_and_mnemonics(self):
        import multisource_downloader_ui as ui
        with mock.patch.object(ui.signins, 'summary', return_value=''):
            app = ui.DownloaderUI(self.root, offer_resume=False, prefs=settings.defaults())
        self.addCleanup(lambda: __import__('logging').getLogger().removeHandler(app._log_handler))
        self.assertEqual(0, app.download_btn.cget('underline'))
        self.assertEqual([app.links, app.table.tree, app.download_btn, app.signins_btn], app.regions())
        menu = self.root.nametowidget(app.help_btn['menu'])
        self.assertEqual('Options...', menu.entrycget(0, 'label'))

    def test_dark_theme_builds_the_main_window(self):
        import multisource_downloader_ui as ui
        with mock.patch.dict(theme.COLORS), mock.patch.dict(theme.STATE), \
                mock.patch.object(theme, 'high_contrast_on', return_value=False):
            self.assertEqual('dark', theme.apply(self.root, 'dark'))
            self.assertEqual(theme.DARK['bg'], theme.COLORS['bg'])
            with mock.patch.object(ui.signins, 'summary', return_value=''):
                app = ui.DownloaderUI(self.root, offer_resume=False, prefs=settings.defaults())
            self.addCleanup(lambda: __import__('logging').getLogger().removeHandler(app._log_handler))
            self.assertEqual(theme.DARK['surface'], str(app.links.cget('background')).upper())
            self.window(settings.defaults()).destroy()


if __name__ == '__main__':
    unittest.main()
