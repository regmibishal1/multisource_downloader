# Accessibility

The app aims at WCAG 2.2 AA, applied to a desktop app as WCAG2ICT describes. This page lists each
requirement from the design brief (section 8), what the app does, how it is checked, and the known
exceptions.

Status: **Pass** means the app meets it and an automated test or a code rule keeps it that way.
**Pass (manual)** means it was checked by hand. **Exception** means it is not met yet, with the reason
and what the app does instead.

| # | Criterion | Status | What the app does | How it is checked |
|---|---|---|---|---|
| A1 | 1.4.1 Use of Color | Pass | Every status has a symbol and words (✓ Saved, ✕ Failed, … Waiting, = Already saved, ■ Stopped, ⊘ Skipped); sign-in states and warnings start with a symbol or "!". | `tests/test_queue_table.py`, `tests/test_simple_view.py` |
| A2 | 1.4.3 Contrast (Minimum) | Pass | Light and dark palettes from the brief; every text colour is at least 4.5:1 on its background. | `tests/test_options_theme.py` computes every pair |
| A3 | 1.4.11 Non-text Contrast | Pass | Control borders #8A8A8A (3:1 or more on both palettes), accent-filled selection, a 2 px accent focus ring on text boxes and text buttons, accent border on focused buttons. | Contrast test; the focus ring is set in `gui/theme.py` |
| A4 | 2.1.1 Keyboard | Pass | Keyboard map in Help; Enter and Escape in every dialog, Cancel in the Instagram and security code dialogs; Tab leaves the Links box; Alt plus the underlined letter selects a button; F6 moves between regions. | `tests/test_options_theme.py` checks each listed shortcut is bound |
| A5 | 2.4.3 Focus Order | Pass | The Links box has the focus at start; each dialog sets its first focus; closing Options, Sign-ins, Stop, Keyboard shortcuts and Update help returns the focus to where it was; after a run the first failed row gets it. | Window tests in CI |
| A6 | 2.4.7 Focus Visible, 2.4.11 Focus Not Obscured | Pass | 2 px accent ring; a focused queue row is scrolled fully into view. | Code rule in `gui/queue_table.py` |
| A7 | 2.5.8 Target Size (Minimum) | Pass | Buttons are 28 px or taller; text buttons have padding so they are at least 24 px tall with their focus ring; checkboxes include their label in the target. | Sizes in `gui/theme.py` (scaled with the display) |
| A8 | 3.2.2 On Input | Pass | No control changes the layout while you use it; the source dropdown is gone and options live in their own window. | |
| A9 | 3.3.1, 3.3.3 Error Identification and Suggestion | Pass | Every failure has a plain message and an action (Fix sign-in, Retry, Change folder, How to fix). | `tests/test_errors.py` |
| A10 | 3.3.2 Labels or Instructions | Pass | Visible labels and helper text on every field. | |
| A11 | 3.3.7 Redundant Entry | Pass | The folder, view, window size and options are remembered. | `tests/test_link_box.py` (settings) |
| A12 | 3.3.8 Accessible Authentication (Minimum) | Pass | Paste is never blocked. "Use my Firefox sign-in", where your password manager works, is preselected when Firefox has a sign-in. The "Sign in here" card says "Your password manager may not fill this window. Paste works." | `tests/test_gui_signins.py`, `tests/test_accessibility.py` |
| A13 | 4.1.3 Status Messages | **Exception** | Tk 8.6 cannot announce status changes to screen readers. The final result also goes into the window title, which screen readers read on focus, and the command line (`multidownloader-batch`) is the fully accessible way to run the app. To revisit with Tk 9.1, which adds an `accessible` command for live regions. | |
| A14 | 1.4.4 Resize Text, 1.4.10 Reflow | Pass | Per-monitor DPI awareness; sizes from the brief scale with the display; fonts follow the system; the window is resizable with sensible minimums; the queue scrolls sideways instead of cutting columns off. | `tests/test_options_theme.py` (scaling) |
| A15 | 2.2.1 Timing Adjustable | Pass | The sign-in window closes after 10 minutes everywhere. A minute before, the sign-in sheet shows "Still there?" with **Keep waiting**, which gives 10 more minutes. The security code dialog waits 10 minutes too. | `tests/test_accessibility.py` |
| A16 | 3.2.6 Consistent Help | Pass | A **?** button in the top-right corner of the main window, Options, Sign-ins, the sign-in sheets, Keyboard shortcuts and Update help. They open the same bundled help page (no internet needed); F1 does the same. | `tests/test_accessibility.py` |
| A17 | 2.5.7 Dragging Movements | Pass | No action needs dragging: Import file... and Ctrl+O import links, and right-clicking a column heading offers **Fit columns** to reset widths. | `tests/test_accessibility.py` |

## Windows High Contrast

When High Contrast is on, the app does not apply its own colours: it uses the system colours
(SystemButtonFace, SystemWindowText, SystemHighlight, SystemHighlightText) and the native theme, so
the user's contrast theme wins. Symbols and words still carry every status.

## Manual test plan

Free tools, to run before a release:

- Keyboard only: paste and download, stop, retry failed, open a file, every dialog, Options, Sign-ins.
- Windows Narrator and NVDA: smoke test of the main window and each dialog. Known result: buttons,
  fields and the window title are read; status changes are not announced (A13).
- Display scaling at 100 %, 150 % and 200 %: screenshots of every window, nothing cut off.
- Windows High Contrast (Aquatic and Desert): every window readable.
- Accessibility Insights for Windows: record what each window exposes to UI Automation here.

Last manual check: not yet done for this release. Record the date, Windows version and results here.
