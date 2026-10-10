"""A run's report as one HTML file, plus its CSV (brief F17 export, 9.4).

The HTML has no external assets: it opens anywhere and can be sent by e-mail.
Account names of the people in the posts are left out by default, because a
shared report could put them at risk (brief 9.4); include_accounts=True keeps
them.
"""
from __future__ import annotations

import html
import shutil
from pathlib import Path
from typing import Iterable

from . import __version__

STATUS_TEXT = {
    'saved': '✓ Saved', 'already_saved': '= Already saved', 'failed': '✕ Failed',
    'stopped': '■ Stopped', 'skipped': '⊘ Skipped',
}


def _cell(text) -> str:
    return html.escape('' if text is None else str(text))


def html_report(results: Iterable, *, run_id: str, include_accounts: bool = False) -> str:
    results = list(results)
    counts = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    summary = ', '.join(f'{n} {STATUS_TEXT.get(status, status).split(" ", 1)[-1].lower()}'
                        for status, n in sorted(counts.items(), key=lambda item: -item[1]))
    rows = []
    for r in results:
        post = r.post or {}
        files = '<br>'.join(f'{_cell(f.path)}<br><small>SHA-256 {_cell(f.sha256)}</small>' for f in r.files)
        detail = r.error_message or (r.skip_reason or '')
        account = _cell(post.get('uploader')) if include_accounts else ''
        rows.append(
            '<tr>'
            f'<td>{_cell(STATUS_TEXT.get(r.status, r.status))}</td>'
            f'<td>{_cell(r.platform)}</td>'
            f'<td><a href="{_cell(r.input_url)}">{_cell(r.input_url)}</a></td>'
            + (f'<td>{account}</td>' if include_accounts else '') +
            f'<td>{_cell(post.get("posted_at"))}</td>'
            f'<td>{files}</td>'
            f'<td>{_cell(detail)}</td>'
            '</tr>')
    account_head = '<th>Account</th>' if include_accounts else ''
    note = ('' if include_accounts else
            '<p class="note">Account names are left out of this report. They are in the run folder\'s manifest.</p>')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Run report {_cell(run_id)}</title>
<style>
body {{ font: 15px/1.5 system-ui, sans-serif; color: #1B1B1B; background: #FFFFFF; margin: 24px; }}
h1 {{ font-size: 20px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border-bottom: 1px solid #E0E0E0; padding: 6px 8px; text-align: left; vertical-align: top; }}
th {{ background: #F3F3F3; }}
small, .note {{ color: #5D5D5D; }}
a {{ color: #005FB8; word-break: break-all; }}
</style>
</head>
<body>
<h1>Run report</h1>
<p>Run {_cell(run_id)}: {_cell(summary)}. Made with multisource-downloader {_cell(__version__)}; times are UTC.</p>
{note}
<table>
<thead><tr><th>Status</th><th>Platform</th><th>Link</th>{account_head}<th>Posted</th><th>Files</th><th>Notes</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody>
</table>
</body>
</html>
"""


def export(run_dir, results, target_html, *, include_accounts: bool = False) -> Path:
    """Write the HTML report to target_html and the run's CSV next to it; returns the HTML path."""
    run_dir = Path(run_dir)
    target_html = Path(target_html)
    target_html.write_text(html_report(results, run_id=run_dir.name, include_accounts=include_accounts),
                           encoding='utf-8')
    csv_source = run_dir / 'manifest.csv'
    if csv_source.exists():
        shutil.copyfile(csv_source, target_html.with_suffix('.csv'))
    return target_html


__all__ = ['export', 'html_report']
