"""Colours, fonts, sizes and the ttk style from the design brief (section 5).

apply(root, mode) picks the light or dark palette ("system" follows the
operating system's app mode) and styles ttk with it. It runs once, before
the window's widgets are made, because widgets take their colours from
COLORS when they are created; a theme change applies on the next start.
In Windows High Contrast mode the palette is not applied at all, so the
user's contrast colours win (brief U23).

Status is never shown by colour alone: every status has a glyph and text.
All text colours meet 4.5:1 and control borders 3:1 against their
background (contrast pairs in the brief, section 5.3).
"""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

LIGHT = {
    'bg': '#F3F3F3', 'surface': '#FFFFFF', 'text': '#1B1B1B', 'text-2': '#5D5D5D', 'border': '#8A8A8A',
    'divider': '#E0E0E0', 'accent': '#005FB8', 'accent-hover': '#00539F', 'on-accent': '#FFFFFF',
    'success': '#0F7B0F', 'error': '#C42B1C', 'warning': '#8A5300',
    'disabled-bg': '#C8C8C8', 'disabled-text': '#6E6E6E', 'bar': '#FFF4CE', 'bar-border': '#8A5300',
}
DARK = {
    'bg': '#202020', 'surface': '#2B2B2B', 'text': '#FFFFFF', 'text-2': '#C5C5C5', 'border': '#8A8A8A',
    'divider': '#3A3A3A', 'accent': '#60CDFF', 'accent-hover': '#4CC2F7', 'on-accent': '#000000',
    'success': '#6CCB5F', 'error': '#FF99A4', 'warning': '#FCE100',
    'disabled-bg': '#3A3A3A', 'disabled-text': '#9A9A9A', 'bar': '#433519', 'bar-border': '#FCE100',
}
# Windows High Contrast: Tk's names for the user's own system colours
HIGH_CONTRAST = {
    'bg': 'SystemButtonFace', 'surface': 'SystemWindow', 'text': 'SystemWindowText', 'text-2': 'SystemWindowText',
    'border': 'SystemWindowText', 'divider': 'SystemWindowText', 'accent': 'SystemHighlight',
    'accent-hover': 'SystemHighlight', 'on-accent': 'SystemHighlightText', 'success': 'SystemWindowText',
    'error': 'SystemWindowText', 'warning': 'SystemWindowText', 'disabled-bg': 'SystemButtonFace',
    'disabled-text': 'SystemGrayText', 'bar': 'SystemWindow', 'bar-border': 'SystemWindowText',
}

# The palette in use. Widgets read it when they are made; apply() fills it.
COLORS = dict(LIGHT)
STATE = {'mode': 'light', 'high_contrast': False, 'scale': 1.0}

_FONTS = {}


# --- what the system wants -------------------------------------------------------------

def system_prefers_dark() -> bool:
    if sys.platform == 'win32':
        try:
            import winreg
            key = r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                return winreg.QueryValueEx(handle, 'AppsUseLightTheme')[0] == 0
        except OSError:
            return False
    if sys.platform == 'darwin':
        import subprocess
        try:
            out = subprocess.run(['defaults', 'read', '-g', 'AppleInterfaceStyle'], capture_output=True, text=True,
                                 timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return out.strip().lower() == 'dark'
    return False


def high_contrast_on() -> bool:
    """Windows High Contrast (SPI_GETHIGHCONTRAST, HCF_HIGHCONTRASTON)."""
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class HIGHCONTRAST(ctypes.Structure):
            _fields_ = [('cbSize', wintypes.UINT), ('dwFlags', wintypes.DWORD), ('lpszDefaultScheme', wintypes.LPWSTR)]

        hc = HIGHCONTRAST()
        hc.cbSize = ctypes.sizeof(HIGHCONTRAST)
        if not ctypes.windll.user32.SystemParametersInfoW(0x0042, hc.cbSize, ctypes.byref(hc), 0):
            return False
        return bool(hc.dwFlags & 0x1)
    except Exception:
        return False


def enable_dpi_awareness() -> None:
    """Per-monitor DPI awareness on Windows, before the first window (sharp text at 150 % and 200 %)."""
    if sys.platform != 'win32':
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def resolve_mode(mode: str) -> str:
    if mode in ('light', 'dark'):
        return mode
    return 'dark' if system_prefers_dark() else 'light'


# --- sizes -------------------------------------------------------------------------------

def px(n: float) -> int:
    """A size from the brief (at 96 DPI) in this screen's pixels. Tk scales fonts itself, not these."""
    return int(round(n * STATE['scale']))


# --- applying ----------------------------------------------------------------------------

def apply(root: tk.Misc, mode: str = 'system') -> str:
    """Pick the palette, scale and ttk style for root. Returns the mode used ("high-contrast" too)."""
    try:
        STATE['scale'] = max(1.0, root.winfo_fpixels('1i') / 96.0)
    except tk.TclError:
        STATE['scale'] = 1.0
    if high_contrast_on():
        COLORS.clear()
        COLORS.update(HIGH_CONTRAST)
        STATE.update(mode='high-contrast', high_contrast=True)
        return 'high-contrast'  # the system's colours and the native theme stay
    resolved = resolve_mode(mode)
    COLORS.clear()
    COLORS.update(DARK if resolved == 'dark' else LIGHT)
    STATE.update(mode=resolved, high_contrast=False)
    _style(root)
    if resolved == 'dark':
        # every window gets a dark title bar when it first appears
        root.bind_class('Toplevel', '<Map>', lambda e: dark_title_bar(e.widget), add='+')
        root.bind('<Map>', lambda e: dark_title_bar(e.widget) if e.widget is root else None, add='+')
    return resolved


def dark_title_bar(window) -> None:
    """Windows 10 20H1 and later: a dark title bar to match the dark palette."""
    if sys.platform != 'win32' or getattr(window, '_dark_bar', False):
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        window._dark_bar = True
    except Exception:
        pass


def _style(root: tk.Misc) -> None:
    c = COLORS
    style = ttk.Style(root)
    style.theme_use('clam')  # the one built-in theme whose colours can all be set
    f = fonts(root)
    root.configure(background=c['bg'])
    root.option_add('*Background', c['bg'])
    root.option_add('*Foreground', c['text'])
    root.option_add('*Text.Background', c['surface'])
    root.option_add('*Text.Foreground', c['text'])
    root.option_add('*Text.insertBackground', c['text'])
    root.option_add('*Text.selectBackground', c['accent'])
    root.option_add('*Text.selectForeground', c['on-accent'])
    root.option_add('*Menu.Background', c['surface'])
    root.option_add('*Menu.Foreground', c['text'])
    root.option_add('*Menu.activeBackground', c['accent'])
    root.option_add('*Menu.activeForeground', c['on-accent'])
    style.configure('.', background=c['bg'], foreground=c['text'], fieldbackground=c['surface'],
                    bordercolor=c['border'], lightcolor=c['bg'], darkcolor=c['bg'], troughcolor=c['divider'],
                    focuscolor=c['accent'], selectbackground=c['accent'], selectforeground=c['on-accent'],
                    insertcolor=c['text'], font=f['body'], focusthickness=2)
    style.map('.', foreground=[('disabled', c['disabled-text'])])
    style.configure('TButton', padding=(px(12), px(5)), background=c['surface'], bordercolor=c['border'])
    style.map('TButton', background=[('disabled', c['disabled-bg']), ('pressed', c['divider']),
                                     ('active', c['divider'])],
              foreground=[('disabled', c['disabled-text'])], bordercolor=[('focus', c['accent'])])
    style.configure('Accent.TButton', background=c['accent'], foreground=c['on-accent'], bordercolor=c['accent'],
                    font=f['bold'], padding=(px(16), px(5)))
    style.map('Accent.TButton', background=[('disabled', c['disabled-bg']), ('pressed', c['accent-hover']),
                                            ('active', c['accent-hover'])],
              foreground=[('disabled', c['disabled-text'])])
    style.configure('TEntry', fieldbackground=c['surface'], bordercolor=c['border'])
    style.map('TEntry', bordercolor=[('focus', c['accent'])], fieldbackground=[('readonly', c['surface'])])
    style.configure('TCombobox', fieldbackground=c['surface'], background=c['surface'], arrowcolor=c['text'])
    style.map('TCombobox', fieldbackground=[('readonly', c['surface'])], bordercolor=[('focus', c['accent'])])
    for name in ('TCheckbutton', 'TRadiobutton'):
        # the tick and the dot in the accent colour, with a visible border: clam draws them black by default,
        # which disappears on the dark palette
        style.configure(name, background=c['bg'], indicatorbackground=c['surface'], indicatorforeground=c['accent'],
                        upperbordercolor=c['border'], lowerbordercolor=c['border'])
        style.map(name, indicatorbackground=[('disabled', c['disabled-bg']), ('pressed', c['divider'])],
                  indicatorforeground=[('disabled', c['disabled-text'])])
    style.configure('TSpinbox', fieldbackground=c['surface'], arrowcolor=c['text'])
    style.configure('Treeview', background=c['surface'], fieldbackground=c['surface'], foreground=c['text'],
                    bordercolor=c['border'])
    style.configure('Treeview.Heading', background=c['bg'], foreground=c['text'], bordercolor=c['divider'])
    style.configure('Horizontal.TProgressbar', background=c['accent'], troughcolor=c['divider'],
                    thickness=px(6))
    style.configure('TLabelframe', background=c['bg'], bordercolor=c['divider'])
    style.configure('TLabelframe.Label', background=c['bg'], foreground=c['text'], font=f['section'])


# --- fonts and text buttons ------------------------------------------------------------

def fonts(root: tk.Misc) -> dict:
    """Named fonts derived from Tk's defaults, so OS text-size settings apply."""
    key = str(root.winfo_toplevel())
    if key in _FONTS:
        return _FONTS[key]
    base = tkfont.nametofont('TkDefaultFont')
    family = base.actual('family')
    heading = tkfont.nametofont('TkHeadingFont').actual('family')
    made = {
        'title': tkfont.Font(root=root, family=heading, size=14, weight='bold'),
        'section': tkfont.Font(root=root, family=family, size=11, weight='bold'),
        'body': tkfont.Font(root=root, family=family, size=10),
        'bold': tkfont.Font(root=root, family=family, size=10, weight='bold'),
        'caption': tkfont.Font(root=root, family=family, size=9),
        'mono': tkfont.Font(root=root, family=tkfont.nametofont('TkFixedFont').actual('family'), size=9),
    }
    _FONTS[key] = made
    return made


def link_label(parent, text, command, **kw) -> tk.Label:
    """A text button: accent color, underlined on hover and focus, Enter/Space activate."""
    f = fonts(parent)
    if not STATE['high_contrast']:
        kw.setdefault('bg', COLORS['bg'])
    kw.setdefault('pady', px(2))  # with the 2 px focus ring, at least 24 px tall (WCAG 2.5.8)
    label = tk.Label(parent, text=text, fg=COLORS['accent'], cursor='hand2', font=f['body'],
                     takefocus=1, highlightthickness=2, highlightcolor=COLORS['accent'], **kw)
    underline = tkfont.Font(font=f['body'])
    underline.configure(underline=True)

    def on(_e=None):
        label.configure(font=underline)

    def off(_e=None):
        label.configure(font=f['body'])

    label.bind('<Enter>', on)
    label.bind('<Leave>', off)
    label.bind('<FocusIn>', on)
    label.bind('<FocusOut>', off)
    label.bind('<Button-1>', lambda _e: command())
    label.bind('<Return>', lambda _e: command())
    label.bind('<space>', lambda _e: command())
    return label


__all__ = ['COLORS', 'DARK', 'HIGH_CONTRAST', 'LIGHT', 'STATE', 'apply', 'dark_title_bar', 'enable_dpi_awareness',
           'fonts', 'high_contrast_on', 'link_label', 'px', 'resolve_mode', 'system_prefers_dark']
