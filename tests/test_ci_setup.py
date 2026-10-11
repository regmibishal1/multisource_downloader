"""On CI every test must run: a missing display or test tool fails here instead of skipping quietly."""
import importlib.util
import os
import unittest

from gui_gate import HAVE_DISPLAY


@unittest.skipUnless(os.environ.get('CI'), 'checks the CI setup')
class CiRunsEverythingTests(unittest.TestCase):
    def test_window_tests_run(self):
        self.assertTrue(HAVE_DISPLAY, 'no display: the window tests were skipped')

    def test_test_tools_are_installed(self):
        for name in ('bagit', 'packaging'):
            with self.subTest(tool=name):
                self.assertIsNotNone(importlib.util.find_spec(name), f'{name} is missing: install ".[dev]"')


if __name__ == '__main__':
    unittest.main()
