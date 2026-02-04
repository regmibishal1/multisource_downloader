"""
Core Downloader Module
Handles video downloading using yt-dlp with cookie authentication support.

License: MIT (see LICENSE file in project root)
"""
import os
import sys
import logging
from typing import Dict, Optional, Callable

try:
    import yt_dlp
except ImportError:
    yt_dlp = None


class PortableDownloader:
    """
    A portable video downloader using yt-dlp.
    
    Supports cookie-based authentication for private/age-restricted videos.
    
    Example:
        >>> dl = PortableDownloader("downloads")
        >>> dl.set_cookie_file("cookies.txt")
        >>> result = dl.download_url("https://youtube.com/watch?v=...")
        >>> print(result['status'])
        'success'
    """
    
    def __init__(self, output_dir: str = "downloads"):
        """
        Initialize the downloader.
        
        Args:
            output_dir: Directory to save downloaded files.
        """
        self.output_dir = output_dir
        self.logger = self._setup_logger()
        self.cookie_file: Optional[str] = None
        
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def _setup_logger(self) -> logging.Logger:
        """Configure and return the logger instance."""
        logger = logging.getLogger("PortableDownloader")
        logger.setLevel(logging.DEBUG)
        logger.handlers = []
        
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S'))
        logger.addHandler(handler)
        
        return logger

    def set_cookie_file(self, path: str) -> bool:
        """
        Set a cookies.txt file for authentication.
        
        Args:
            path: Path to the Netscape-format cookies.txt file.
            
        Returns:
            True if the file exists and was set, False otherwise.
        """
        if path and os.path.exists(path):
            self.cookie_file = path
            self.logger.info(f"Cookie file set: {path}")
            return True
        return False

    def download_url(self, url: str, progress_hook: Optional[Callable] = None) -> Dict:
        """
        Download a video from the given URL.
        
        Args:
            url: The video URL to download.
            progress_hook: Optional callback for progress updates.
            
        Returns:
            A dict with 'status' ('success' or 'error') and 
            either 'info' (video metadata) or 'error' (error message).
        """
        if not yt_dlp:
            return {'status': 'error', 'error': 'yt-dlp module not found.'}

        self.logger.info(f"URL: {url}")
        self.logger.info(f"Output: {self.output_dir}")

        ydl_opts = {
            'outtmpl': os.path.join(self.output_dir, '%(title)s.%(ext)s'),
            'noplaylist': True,
            'quiet': False,
            'no_warnings': False,
            'format': 'best',
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            },
        }

        if self.cookie_file and os.path.exists(self.cookie_file):
            ydl_opts['cookiefile'] = self.cookie_file
            self.logger.info(f"Using cookies: {self.cookie_file}")

        def my_hook(d):
            if d['status'] == 'downloading':
                percent = d.get('_percent_str', '?%')
                speed = d.get('_speed_str', '?')
                self.logger.info(f"Downloading: {percent} at {speed}")
            elif d['status'] == 'finished':
                self.logger.info("Download finished, processing...")

        ydl_opts['progress_hooks'] = [my_hook]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                self.logger.info("Extracting video info...")
                info = ydl.extract_info(url, download=True)
                
                if info:
                    title = info.get('title', 'Unknown')
                    self.logger.info(f"Completed: {title}")
                    return {'status': 'success', 'info': info}
                else:
                    return {'status': 'error', 'error': 'No video info returned'}
                
        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            self.logger.error(f"Download error: {error_msg}")
            return {'status': 'error', 'error': error_msg}
        except Exception as e:
            self.logger.error(f"Error: {e}")
            return {'status': 'error', 'error': str(e)}
