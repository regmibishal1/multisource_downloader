# Simple YouTube Downloader (Portable)

A standalone, portable YouTube video downloader with a simple GUI. No installation required - just download and run!

## Download

Get the latest release from the [Releases page](../../releases).

## Features

- ✅ **Portable** - Single exe file, no installation needed
- ✅ **Simple UI** - Just paste URL and click Download
- ✅ **Cookie Support** - Download private/age-restricted videos
- ✅ **Live Progress** - See download progress in real-time
- ✅ **High-DPI** - Crisp UI on high-resolution displays

## Usage

1. **Download** `SimpleYoutubeDownloader.exe`
2. **Run** the application (Windows may show a security warning - click "More info" → "Run anyway")
3. **Paste** a YouTube URL
4. **Click** Download!

### For Private/Age-Restricted Videos

If you get "Sign in" errors, you need to import your YouTube cookies:

1. Install the **"Get cookies.txt LOCALLY"** browser extension ([Edge](https://microsoftedge.microsoft.com/addons/search/cookies.txt) | [Chrome](https://chrome.google.com/webstore/search/cookies.txt))
2. Go to **YouTube** and make sure you're logged in
3. Click the extension icon → **Export All Cookies**
4. Save the file
5. In the app, click **"Import cookies.txt"** and select your file
6. Try downloading again!

## Building from Source

Requirements:
- Python 3.10+
- pip

```bash
# Install dependencies
pip install -r requirements.txt

# Build the executable
python build_app.py
```

The exe will be created in `dist/SimpleYoutubeDownloader.exe`

## License

MIT License - See the main project LICENSE file.
