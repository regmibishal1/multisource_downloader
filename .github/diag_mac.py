# Temporary: find where the sign-in success screen hangs Tk on macOS. Removed before merging.
import faulthandler
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from unittest import mock

faulthandler.dump_traceback_later(60, exit=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multidownloader import session_store, signins  # noqa: E402
from multidownloader.platforms import BY_KEY  # noqa: E402


def step(name, widget):
    t = time.time()
    print('step:', name, '...', end=' ', flush=True)
    widget.update()
    print(f'ok ({time.time() - t:.2f}s)', flush=True)


tmp = tempfile.mkdtemp()
with mock.patch.object(session_store, 'SESSION_ROOT', Path(tmp) / '.sessions'), \
        mock.patch.object(signins, 'find_firefox_signins', return_value=[]), \
        mock.patch.object(signins, 'firefox_profiles', return_value=[]):
    root = tk.Tk()
    root.withdraw()
    print('tk', root.tk.call('info', 'patchlevel'), root.tk.call('tk', 'windowingsystem'), flush=True)
    from multidownloader.gui.signin_sheet import SignInSheet
    ig = BY_KEY['instagram']
    with mock.patch.object(signins, 'webview_available', return_value=(True, '')):
        sheet = SignInSheet(root, ig)
    step('sheet built', sheet)
    sheet._show_companion()
    step('companion', sheet)
    sheet._show_page('https://www.instagram.com/accounts/login/')
    step('page shown', sheet)
    print('focus:', sheet.focus_get(), 'state:', sheet.state(), 'mapped:', sheet.winfo_ismapped(), flush=True)
    result = signins.save_webview_signin(ig, [{'name': 'sessionid', 'value': 'S', 'domain': '.instagram.com'}],
                                         user_agent='UA', terms_version=sheet._terms_version())
    print('saved on the main thread', flush=True)
    for child in sheet.body.winfo_children():
        child.destroy()
    step('children destroyed', sheet)
    sheet._success(result)
    step('success screen', sheet)
    step('second update', sheet)
    sheet.destroy()
    step('sheet destroyed', root)
    root.destroy()
print('diagnostic finished', flush=True)
