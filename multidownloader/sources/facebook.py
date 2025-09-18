"""Facebook handler via yt-dlp."""
from __future__ import annotations

from .yt_dlp_base import YtDlpHandler


class FacebookHandler(YtDlpHandler):
    def __init__(self, logger=None):
        super().__init__('Facebook', logger=logger, outtmpl='facebook/%(uploader)s/%(title)s [%(id)s].%(ext)s')

