"""
Build script for creating the portable executable.

This script uses PyInstaller to package the application into a 
single standalone .exe file that can be distributed.

Usage:
    python build_app.py

License: MIT (see LICENSE file in project root)
"""
import os
import shutil
import PyInstaller.__main__


def build():
    """Build the portable executable using PyInstaller."""
    print("Starting build process...")
    
    # Clean previous builds
    for folder in ['dist', 'build']:
        if os.path.exists(folder):
            try:
                shutil.rmtree(folder)
            except PermissionError:
                print(f"Warning: Could not remove {folder}/ - files may be in use")

    PyInstaller.__main__.run([
        'simple_ui.py',
        '--name=SimpleYoutubeDownloader',
        '--windowed',   # Hide console window
        '--onefile',    # Single executable file
        '--clean',
        '--noconfirm',
    ])
    
    print("Build complete. Check the 'dist/' directory for SimpleYoutubeDownloader.exe")


if __name__ == "__main__":
    build()
