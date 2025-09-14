"""TikTok handler using yt-dlp."""
import os
import logging

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except Exception:
    YTDLP_AVAILABLE = False


class TikTokHandler:
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger(__name__)

    def download(self, url, out_dir, options):
        if not YTDLP_AVAILABLE:
            raise RuntimeError('yt-dlp not available')
        os.makedirs(out_dir, exist_ok=True)
        # Allow caller to pass additional ytdlp options
        base_opts = {'outtmpl': os.path.join(out_dir, '%(title)s.%(ext)s')}
        ytdlp_opts = options.get('ytdlp_opts', {})
        base_opts.update(ytdlp_opts)

        attempts = 2
        for attempt in range(1, attempts + 1):
            try:
                with yt_dlp.YoutubeDL(base_opts) as ydl:
                    ydl.download([url])
                self.logger.info(f'Downloaded TikTok: {url}')
                return True
            except Exception as ex:
                self.logger.warning(f'Attempt {attempt} failed for TikTok {url}: {ex}')
                if attempt == attempts:
                    raise RuntimeError(f'yt-dlp failed: {ex}')
                continue
