"""
Simple YouTube Downloader - Portable Edition
A standalone YouTube video downloader with a simple GUI.

License: MIT (see LICENSE file in project root)
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import threading
import os
import shutil
import webbrowser
import ctypes
import logging
import queue
from core_downloader import PortableDownloader

# Enable high-DPI awareness on Windows for crisp UI
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class QueueHandler(logging.Handler):
    """Custom logging handler that sends logs to a queue for thread-safe UI updates."""
    
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(self.format(record))


class SimpleDownloaderUI:
    """Main application window for the YouTube downloader."""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Simple YouTube Downloader")
        self.root.geometry("650x550")
        self.root.resizable(True, True)
        
        self.log_queue = queue.Queue()
        self._setup_ui()
        self._setup_logging()
        self._check_existing_cookies()
        self._poll_log_queue()

    def _setup_ui(self):
        """Initialize all UI components."""
        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # URL Input
        ttk.Label(main_frame, text="Enter YouTube URL:").pack(anchor=tk.W)
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(main_frame, textvariable=self.url_var, width=70)
        self.url_entry.pack(fill=tk.X, pady=(5, 10))
        
        # Authentication Section
        auth_frame = ttk.LabelFrame(main_frame, text="Authentication", padding=8)
        auth_frame.pack(fill=tk.X, pady=(0, 10))
        
        cookie_row = ttk.Frame(auth_frame)
        cookie_row.pack(fill=tk.X)
        
        ttk.Button(cookie_row, text="Import cookies.txt", command=self._import_cookies).pack(side=tk.LEFT)
        self.cookie_status_var = tk.StringVar(value="No cookies loaded")
        self.cookie_status = ttk.Label(cookie_row, textvariable=self.cookie_status_var, foreground="gray")
        self.cookie_status.pack(side=tk.LEFT, padx=(10, 0))
        
        ttk.Button(
            cookie_row, 
            text="Get Extension",
            command=lambda: webbrowser.open("https://microsoftedge.microsoft.com/addons/search/cookies.txt")
        ).pack(side=tk.RIGHT)
        
        # Output Directory
        dir_frame = ttk.Frame(main_frame)
        dir_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(dir_frame, text="Save to:").pack(side=tk.LEFT)
        self.dir_var = tk.StringVar(value=os.path.join(os.getcwd(), "downloads"))
        self.dir_entry = ttk.Entry(dir_frame, textvariable=self.dir_var, width=50)
        self.dir_entry.pack(side=tk.LEFT, padx=(10, 5), fill=tk.X, expand=True)
        ttk.Button(dir_frame, text="Browse", command=self._browse_dir).pack(side=tk.LEFT)
        
        # Download Button
        self.download_btn = ttk.Button(main_frame, text="Download", command=self._start_download)
        self.download_btn.pack(pady=8)
        
        # Log Display
        log_frame = ttk.LabelFrame(main_frame, text="Log Output", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, width=70, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.config(state=tk.DISABLED)
        
        # Status Label
        self.status_var = tk.StringVar(value="Ready")
        self.status_label = ttk.Label(main_frame, textvariable=self.status_var, foreground="gray")
        self.status_label.pack(pady=5)
        
        self.downloader = None
        self.cookie_file = None

    def _setup_logging(self):
        """Configure logging to display in the UI."""
        handler = QueueHandler(self.log_queue)
        handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S'))
        
        logger = logging.getLogger("PortableDownloader")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

    def _poll_log_queue(self):
        """Poll the log queue and update the UI with new messages."""
        while True:
            try:
                msg = self.log_queue.get_nowait()
                self.log_text.config(state=tk.NORMAL)
                self.log_text.insert(tk.END, msg + "\n")
                self.log_text.see(tk.END)
                self.log_text.config(state=tk.DISABLED)
            except queue.Empty:
                break
        self.root.after(100, self._poll_log_queue)

    def _log(self, message):
        """Add a message to the log display."""
        self.log_queue.put(message)

    def _check_existing_cookies(self):
        """Check if cookies.txt exists in app directory."""
        local_cookie = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
        if os.path.exists(local_cookie):
            self.cookie_file = local_cookie
            self.cookie_status_var.set("✓ cookies.txt loaded")
            self.cookie_status.config(foreground="green")
            self._log("Found existing cookies.txt file")

    def _import_cookies(self):
        """Import a cookies.txt file from user selection."""
        path = filedialog.askopenfilename(
            title="Select cookies.txt file",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if path:
            try:
                dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
                shutil.copy2(path, dest)
                self.cookie_file = dest
                self.cookie_status_var.set("✓ cookies.txt imported!")
                self.cookie_status.config(foreground="green")
                self._log(f"Imported cookies from: {path}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to import cookies: {e}")

    def _browse_dir(self):
        """Open directory browser for output selection."""
        directory = filedialog.askdirectory()
        if directory:
            self.dir_var.set(directory)

    def _start_download(self):
        """Start the download process in a background thread."""
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Input Error", "Please enter a valid URL.")
            return
        
        # Clear log
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        
        output_dir = self.dir_var.get().strip() or "downloads"
        self.downloader = PortableDownloader(output_dir)
        
        if self.cookie_file:
            self.downloader.set_cookie_file(self.cookie_file)
        
        self.download_btn.config(state=tk.DISABLED)
        self.status_var.set("Downloading...")
        self.status_label.config(foreground="blue")
        self._log(f"Starting download: {url}")
        
        thread = threading.Thread(target=self._download_task, args=(url,), daemon=True)
        thread.start()

    def _download_task(self, url):
        """Background task for downloading (runs in separate thread)."""
        try:
            result = self.downloader.download_url(url)
            if result['status'] == 'success':
                title = result['info'].get('title', 'Video') if result['info'] else 'Video'
                self.root.after(0, lambda: self._on_success(title))
            else:
                self.root.after(0, lambda: self._on_error(result['error']))
        except Exception as e:
            self.root.after(0, lambda: self._on_error(str(e)))

    def _on_success(self, title):
        """Handle successful download completion."""
        self.status_var.set(f"Downloaded: {title}")
        self.status_label.config(foreground="green")
        self.download_btn.config(state=tk.NORMAL)
        self._log(f"SUCCESS: Downloaded {title}")
        messagebox.showinfo("Success", f"Successfully downloaded:\n{title}")
        self.url_var.set("")

    def _on_error(self, error_msg):
        """Handle download failure."""
        self.status_var.set("Error occurred")
        self.status_label.config(foreground="red")
        self.download_btn.config(state=tk.NORMAL)
        self._log(f"ERROR: {error_msg}")
        messagebox.showerror("Download Failed", f"Error:\n{error_msg}")


def main():
    """Application entry point."""
    root = tk.Tk()
    SimpleDownloaderUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
