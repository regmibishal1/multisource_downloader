"""Whether the tests that open real Tk windows run.

They run in CI (GitHub sets CI=true) or when MSDL_GUI_TESTS=1. On a working
computer they stay off: a test window takes the keyboard focus for a moment,
so typing elsewhere lands in it and Windows plays its invalid-input sound.
"""
import os


def gui_tests_enabled() -> bool:
    if not (os.environ.get('CI') or os.environ.get('MSDL_GUI_TESTS') == '1'):
        return False
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except Exception:  # no Tk or no display
        return False


HAVE_DISPLAY = gui_tests_enabled()
REASON = 'opens windows: runs in CI or with MSDL_GUI_TESTS=1'
