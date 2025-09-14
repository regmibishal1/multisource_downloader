Multi-source Downloader

This project provides a small Tkinter UI that delegates downloads from multiple sources to a modular `multidownloader` package.

Supported sources (optional packages):
- Google Drive (public: `gdown`; authenticated: `PyDrive2`)
- Instagram (`instaloader`)
- TikTok (`yt-dlp`)

Quick start

1. Create and activate a virtual environment (recommended):

```bash
python -m venv ./penv
source ./penv/Scripts/activate   # Windows (bash)
```

2. Install required packages you need (from `requirements.txt`):

```bash
pip install -r requirements.txt
```

3. Run the UI:

```bash
python multisource_downloader_ui.py
```

Notes

- The UI delegates all download logic to `multidownloader.core.Downloader`.
- Authentication dialogs for sources with interactive auth (Instagram) are handled by the handler's `interactive_auth` method and invoked via `Downloader.authenticate(source_name, root=your_tk_root)`.
- Optional packages are only required for the sources you intend to use.

Instaloader session examples

You can save and reuse Instaloader session files so you don't need to enter credentials every time.

1. Save a session from the UI

- Choose Source: Instagram and Auth Type: Authenticated.
- When prompted, enter your username/password (or pick an existing session file).
- After successful login the UI offers to save a session file; save it somewhere safe (e.g. `~/.config/instaloader/your_user.session`).

2. Use a saved session file

- When the authentication dialog appears, click "Select Session File" and choose the `.session` file you saved earlier.
- The handler will call `Instaloader.load_session_from_file(username, session_path)` and re-use that session for authenticated downloads.

Programmatic example (handler-level)

If you want to use the downloader core programmatically you can authenticate using the core's proxy:

```python
from multidownloader.core import Downloader
import tkinter as tk

root = tk.Tk()
d = Downloader('/path/to/outdir')
# This will open the handler's interactive auth dialog (Toplevel) and return an Instaloader client
client = d.authenticate('Instagram', root=root)
if client:
    d.download(
        'Instagram',
        'https://instagram.com/p/SHORTCODE',
        {
            'instaloader_client': client,
            'auth': 'authenticated',
        },
    )
```

Security note

- Session files allow access under your account; keep them secure and don't commit them to source control.
- If you use shared machines, store session files in user-specific directories with restrictive permissions.
