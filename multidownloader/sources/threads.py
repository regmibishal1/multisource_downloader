"""Threads handler via yt-dlp.

yt-dlp (2026.8.19) and gallery-dl (1.32.16) have no reader for Threads, so
yt-dlp's generic page reader would fail with "Unsupported URL", which the app
would report as "Threads changed something". Instead the handler checks for a
real Threads reader first and says plainly that Threads is not supported yet;
it starts working by itself once a yt-dlp release adds one.
"""
from __future__ import annotations

from .yt_dlp_base import YTDLP_AVAILABLE, YtDlpHandler

THREADS_NOT_SUPPORTED = ('Threads posts cannot be downloaded yet: yt-dlp and gallery-dl, the programs the app '
                         'uses, do not read Threads. Save the link to try again after an update.')


def ytdlp_reads(url: str) -> bool:
    """Whether yt-dlp has a reader for this link other than its generic page reader."""
    if not YTDLP_AVAILABLE:
        return False
    from yt_dlp.extractor import gen_extractor_classes
    return any(ie.ie_key() != 'Generic' and ie.suitable(url) for ie in gen_extractor_classes())


class ThreadsHandler(YtDlpHandler):
    def __init__(self, logger=None):
        super().__init__('Threads', logger=logger)

    def download(self, url, out_dir, options=None):
        if YTDLP_AVAILABLE and not ytdlp_reads(url):
            raise RuntimeError(THREADS_NOT_SUPPORTED)
        return super().download(url, out_dir, options)
