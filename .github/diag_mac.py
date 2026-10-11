# Temporary: find where the sign-in success screen hangs Tk on macOS. Removed before merging.
# visible: the root window is shown, as in the app. onebyone: hidden root, widgets destroyed one at a time.
import faulthandler
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from unittest import mock

faulthandler.dump_traceback_later(45, exit=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multidownloader import session_store, signins  # noqa: E402
from multidownloader.platforms import BY_KEY  # noqa: E402

MODE = sys.argv[1]


def step(name, widget):
    t = time.time()
    print('step:', name, '...', end=' ', flush=True)
    widget.update()
    print(f'ok ({time.time() - t:.2f}s)', flush=True)


def leaves_first(widget):
    for child in widget.winfo_children():
        yield from leaves_first(child)
        yield child


print('== mode', MODE, flush=True)
tmp = tempfile.mkdtemp()
with mock.patch.object(session_store, 'SESSION_ROOT', Path(tmp) / '.sessions'), \
        mock.patch.object(signins, 'find_firefox_signins', return_value=[]), \
        mock.patch.object(signins, 'firefox_profiles', return_value=[]):
    root = tk.Tk()
    if MODE != 'visible':
        root.withdraw()
    from multidownloader.gui.signin_sheet import SignInSheet
    ig = BY_KEY['instagram']
    with mock.patch.object(signins, 'webview_available', return_value=(True, '')):
        sheet = SignInSheet(root, ig)
    step('sheet built', sheet)
    sheet._show_companion()
    sheet._show_page('https://www.instagram.com/accounts/login/')
    step('companion and page', sheet)
    print('state:', sheet.state(), 'mapped:', sheet.winfo_ismapped(), flush=True)
    result = signins.save_webview_signin(ig, [{'name': 'sessionid', 'value': 'S', 'domain': '.instagram.com'}],
                                         user_agent='UA', terms_version=sheet._terms_version())
    if MODE == 'onebyone':
        for w in list(leaves_first(sheet.body)):
            label = f'{w.winfo_class()} {w} mapped={w.winfo_ismapped()} manager={w.winfo_manager()}'
            w.destroy()
            step('destroyed ' + label, sheet)
    sheet._success(result)
    step('success screen', sheet)
    sheet.destroy()
    step('sheet destroyed', root)
    root.destroy()
print('== finished', MODE, flush=True)
