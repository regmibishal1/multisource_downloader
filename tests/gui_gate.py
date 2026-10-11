"""Whether the tests that open real Tk windows run.

They run in CI (GitHub sets CI=true) or when MSDL_GUI_TESTS=1. On a working
computer they stay off: a test window takes the keyboard focus for a moment,
so typing elsewhere lands in it and Windows plays its invalid-input sound.
"""
import faulthandler
import os

if os.environ.get('CI'):
    # the suite takes about a minute: a hang ends after 10 with every thread's stack
    faulthandler.dump_traceback_later(600, exit=True)


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


def hidden_root(test):
    """A withdrawn Tk root for one test; nothing appears or takes the focus.

    When the test ends the root is destroyed, the test's own attributes (the app,
    its widgets and Tk variables) are dropped, and garbage is collected, all on
    the main thread. Otherwise a Tk object left in a reference cycle could be
    collected later from a worker thread of another test, which makes Tcl abort
    the whole test run ("Tcl_AsyncDelete: async handler deleted by the wrong thread").
    """
    import gc
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()

    def release():
        for name in [n for n in vars(test) if not n.startswith('_')]:
            delattr(test, name)
        gc.collect()

    test.addCleanup(release)  # cleanups run last-in first-out: this runs after destroy
    test.addCleanup(root.destroy)
    return root
