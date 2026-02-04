# Simple YouTube Downloader (Portable)

A standalone, portable YouTube video downloader with a simple GUI. Build it yourself and share with anyone - no Python installation required for end users!

## Features

- ✅ **Portable** - Builds to a single exe file, no installation needed
- ✅ **Simple UI** - Just paste URL and click Download
- ✅ **Cookie Support** - Download private/age-restricted videos
- ✅ **Live Progress** - See download progress in real-time
- ✅ **High-DPI** - Crisp UI on high-resolution displays

## Building the Executable

### Requirements
- Python 3.10+
- pip

### Steps

```bash
# Navigate to this directory
cd portable_manager

# Create virtual environment (recommended)
python -m venv venv
venv\Scripts\activate  # On Windows

# Install dependencies
pip install -r requirements.txt

# Build the executable
python build_app.py
```

The exe will be created at `dist/SimpleYoutubeDownloader.exe`

## Usage

1. **Run** `SimpleYoutubeDownloader.exe` (Windows may show a security warning - click "More info" → "Run anyway")
2. **Paste** a YouTube URL
3. **Click** Download!

### For Private/Age-Restricted Videos

If you get "Sign in" errors, import your YouTube cookies:

1. Install **"Get cookies.txt LOCALLY"** extension ([Edge](https://microsoftedge.microsoft.com/addons/search/cookies.txt) | [Chrome](https://chrome.google.com/webstore/search/cookies.txt))
2. Go to **YouTube** (logged in) → Click extension → **Export All Cookies**
3. In the app, click **"Import cookies.txt"** → Select your file
4. Download again!

## License

MIT License - See the main project LICENSE file.
