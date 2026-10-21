"""Turn a Result into what people take away: an HTML report and a CSV.

The HTML report is one self-contained file. Its styles, its data and its two
buttons are all inside it, so it still works saved to disk and opened later
with no server running. Every value that came from the export goes through
`esc()` on its way into the page, so a cell containing "<script>" is shown as
text rather than run.
"""

from __future__ import annotations

import base64
import csv
import html
import io
import re
from pathlib import Path

from . import __version__
from .engine import Result
from .rules import Finding

STYLE = (Path(__file__).parent / "web" / "style.css").read_text(encoding="utf-8")

# Long lists are cut short on the page (the CSV always has everything).
SHOWN_PER_CHECK = 500
SHOWN_STUDENTS = 300

CSV_HEADERS = ["Row", "Student", "Severity", "Check", "Column", "Value",
               "Problem", "How to fix", "Check ID"]

SEVERITY_NAMES = {"error": "Error", "warning": "Warning"}

# The two buttons. Both build the file in the browser from what is already in
# the page, so nothing is fetched from anywhere when they are pressed.
SCRIPT = """
(function () {
  function save(name, type, data) {
    var url = URL.createObjectURL(new Blob([data], { type: type }));
    var link = document.createElement("a");
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
  }
  var csvButton = document.getElementById("download-csv");
  csvButton.addEventListener("click", function () {
    var text = atob(document.getElementById("findings-csv").textContent.trim());
    var bytes = new Uint8Array(text.length);
    for (var i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i);
    save(csvButton.dataset.filename, "text/csv;charset=utf-8", bytes);
  });
  var saveButton = document.getElementById("save-report");
  saveButton.addEventListener("click", function () {
    var copy = document.documentElement.cloneNode(true);
    copy.querySelectorAll(".no-save").forEach(function (el) { el.remove(); });
    save(saveButton.dataset.filename, "text/html;charset=utf-8",
         "<!DOCTYPE html>\\n" + copy.outerHTML);
  });
})();
"""


def esc(value) -> str:
    """Make any value safe to place in HTML text or an attribute."""
    return html.escape(str(value), quote=True)


def file_stem(source_name: str) -> str:
    """A safe base for download names: "Students 2026.txt" -> "Students_2026"."""
    stem = Path(source_name or "export").stem
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "export"


def page(title: str, body: str, script: str = "") -> str:
    """Wrap a page body in the shared document shell and stylesheet."""
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<meta name=\"robots\" content=\"noindex\">\n"
        f"<title>{esc(title)}</title>\n<style>{STYLE}</style>\n</head>\n<body>\n"
        f"{body}\n{script}\n</body>\n</html>\n"
    )


# ------------------------------------------------------------------ HTML
def render_html(result: Result, back_url: str | None = None) -> str:
    """The full report page. `back_url`, when given, adds "Check another file"."""
    parts = [_topbar(result, back_url), "<main>", _tiles(result), _banners(result)]
    if result.findings:
        parts += [
            '<input type="radio" name="view" id="view-check" class="tab-radio" checked>',
            '<input type="radio" name="view" id="view-student" class="tab-radio">',
            '<div class="tabs"><label for="view-check">By check</label>'
            '<label for="view-student">By student</label></div>',
            '<div class="view view-check">', _summary_table(result), *_check_sections(result), "</div>",
            '<div class="view view-student">', *_student_sections(result), "</div>",
        ]
    parts += [_skipped(result), _passed(result), _file_details(result), _footer(), "</main>"]
    data = base64.b64encode(findings_csv_bytes(result)).decode("ascii")
    script = (f'<script id="findings-csv" type="application/octet-stream">{data}</script>\n'
              f"<script>{SCRIPT}</script>")
    return page(f"Roster check: {result.source_name or 'export'}", "\n".join(parts), script)


def _topbar(result: Result, back_url: str | None) -> str:
    stem = file_stem(result.source_name)
    when = result.checked_at.strftime("%Y-%m-%d %H:%M")
    back = (f'<a class="btn no-save" href="{esc(back_url)}">Check another file</a>'
            if back_url else "")
    return f"""<header class="topbar">
  <div class="brand">TechX Roster Check</div>
  <div class="meta">{esc(result.source_name or "export")} · {esc(result.pack.name)} · {esc(when)}</div>
  <div class="spacer"></div>
  {back}
  <button class="btn" type="button" id="download-csv" data-filename="{esc(stem)}-findings.csv">Download CSV</button>
  <button class="btn" type="button" id="save-report" data-filename="{esc(stem)}-report.html">Save report</button>
</header>"""


def _tiles(result: Result) -> str:
    tiles = [
        ("", len(result.table.rows), "rows checked"),
        ("", result.rows_with_findings, "rows with problems"),
        ("error", result.errors, "errors: must be fixed"),
        ("warning", result.warnings, "warnings: worth a look"),
    ]
    cells = "".join(
        f'<div class="tile {kind}"><div class="num">{count:,}</div><div class="cap">{caption}</div></div>'
        for kind, count, caption in tiles
    )
    return f'<section class="tiles">{cells}</section>'


def _banners(result: Result) -> str:
    out = []
    if result.notes:
        notes = "".join(f"<p>{esc(note)}</p>" for note in result.notes)
        out.append(f'<div class="banner note">{notes}</div>')
    if not result.findings and result.table.rows:
        ran = len(result.passed)
        out.append(f'<div class="banner ok">No problems found. All {ran} '
                   f'check{"" if ran == 1 else "s"} that could run passed.</div>')
    return "\n".join(out)


def _sev(severity: str) -> str:
    return f'<span class="sev {esc(severity)}">{SEVERITY_NAMES.get(severity, severity)}</span>'


def _value(text: str) -> str:
    return f'<span class="value">{esc(text)}</span>' if text else '<span class="blank">(blank)</span>'


def _anchor(rule_id: str) -> str:
    return "check-" + re.sub(r"[^A-Za-z0-9_-]+", "-", rule_id)


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def _summary_table(result: Result) -> str:
    rows = "".join(
        f'<tr><td><a href="#{_anchor(rule.id)}">{esc(rule.label)}</a></td>'
        f"<td>{_sev(rule.severity)}</td><td class=\"num\">{len(found):,}</td></tr>"
        for rule, found in result.by_rule()
    )
    return (
        '<section class="panel"><div class="panel-head"><h2>Problems by check</h2></div>'
        '<div class="scroll"><table><thead><tr><th>Check</th><th>Severity</th>'
        f'<th class="num">Problems</th></tr></thead><tbody>{rows}</tbody></table></div></section>'
    )


def _check_sections(result: Result) -> list[str]:
    sections = []
    for rule, found in result.by_rule():
        sub = []
        if rule.scope():
            sub.append(esc(rule.scope()) + ".")
        if rule.fix:
            sub.append(f"<strong>How to fix:</strong> {esc(rule.fix)}")
        sub_html = f'<div class="panel-sub">{" ".join(sub)}</div>' if sub else ""
        rows = "".join(
            f'<tr><td class="rowno">{f.row.line}</td><td>{esc(result.row_label(f.row))}</td>'
            f"<td>{_value(f.value)}</td><td>{esc(f.sentence)}</td></tr>"
            for f in found[:SHOWN_PER_CHECK]
        )
        more = ""
        if len(found) > SHOWN_PER_CHECK:
            more = (f'<div class="more">Showing the first {SHOWN_PER_CHECK:,} of {len(found):,}. '
                    "Download the CSV for the full list.</div>")
        sections.append(
            f'<section class="panel" id="{_anchor(rule.id)}">'
            f'<div class="panel-head">{_sev(rule.severity)}<h2>{esc(rule.label)}</h2>'
            f'<span class="count">{_plural(len(found), "problem")}</span></div>{sub_html}'
            '<div class="scroll"><table class="fixed"><colgroup><col class="c-row"><col class="c-student">'
            '<col class="c-value"><col></colgroup><thead><tr><th>Row</th><th>Student</th><th>Value</th>'
            f"<th>Problem</th></tr></thead><tbody>{rows}</tbody></table></div>{more}</section>"
        )
    return sections


def _student_sections(result: Result) -> list[str]:
    groups = result.by_row()
    sections = []
    for row, found in groups[:SHOWN_STUDENTS]:
        rows = "".join(_student_line(f) for f in found)
        label = result.row_label(row)
        sections.append(
            f'<section class="panel"><div class="panel-head"><h3>Row {row.line}</h3>'
            f"<span>{esc(label)}</span>"
            f'<span class="count">{_plural(len(found), "problem")}</span></div>'
            '<div class="scroll"><table class="fixed"><colgroup><col class="c-sev"><col class="c-check">'
            '<col class="c-value"><col></colgroup><thead><tr><th>Severity</th><th>Check</th>'
            f"<th>Value</th><th>Problem</th></tr></thead><tbody>{rows}</tbody></table></div></section>"
        )
    if len(groups) > SHOWN_STUDENTS:
        sections.append(f'<div class="banner note"><p>Showing the first {SHOWN_STUDENTS:,} of '
                        f"{len(groups):,} rows with problems. Download the CSV for the full list.</p></div>")
    return sections


def _student_line(f: Finding) -> str:
    fix = f'<div class="muted">{esc(f.rule.fix)}</div>' if f.rule.fix else ""
    return (f"<tr><td>{_sev(f.rule.severity)}</td><td>{esc(f.rule.label)}{fix}</td>"
            f"<td>{_value(f.value)}</td><td>{esc(f.sentence)}</td></tr>")


def _skipped(result: Result) -> str:
    if not result.skipped:
        return ""
    rows = "".join(
        f"<tr><td>{esc(s.rule.label)}</td><td class=\"mono\">{esc(', '.join(s.missing))}</td></tr>"
        for s in result.skipped
    )
    return (
        f'<details class="panel"><summary>Checks not run ({len(result.skipped)})</summary>'
        '<div class="panel-sub">These checks need columns this file does not have. That is '
        "expected when a rule pack covers more than one kind of export. Add the column to the "
        "export to include the check.</div>"
        '<div class="scroll"><table><thead><tr><th>Check</th><th>Missing column</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div></details>"
    )


def _passed(result: Result) -> str:
    if not result.passed:
        return ""
    items = "".join(f"<li>{esc(rule.label)}</li>" for rule in result.passed)
    return (f'<details class="panel"><summary>Checks passed ({len(result.passed)})</summary>'
            f'<ul class="list">{items}</ul></details>')


def _file_details(result: Result) -> str:
    table = result.table
    used = set(result.pack.columns_used())
    facts = [
        ("File", result.source_name or "export"),
        ("Encoding", table.encoding),
        ("Delimiter", table.delimiter_name),
        ("Data rows", f"{len(table.rows):,}"),
        ("Rule pack", f"{result.pack.name} ({result.pack.path.name})"),
        ("Checks in pack", f"{len(result.pack.rules)} on"
                           + (f", {result.pack.disabled} switched off" if result.pack.disabled else "")),
    ]
    fact_rows = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in facts)
    column_rows = "".join(
        f'<tr><td class="mono">{esc(header)}</td><td class="mono">{esc(column)}</td>'
        f'<td>{"yes" if column in used else "<span class=muted>no</span>"}</td></tr>'
        for header, column in zip(table.headers, table.columns)
    )
    return (
        '<details class="panel"><summary>About this file</summary>'
        f'<div class="scroll"><table><tbody>{fact_rows}</tbody></table></div>'
        '<div class="scroll"><table><thead><tr><th>Header in the file</th><th>Read as</th>'
        f"<th>Checked by this pack</th></tr></thead><tbody>{column_rows}</tbody></table></div></details>"
    )


def _footer() -> str:
    return (
        f"<footer>TechX Roster Check {esc(__version__)}. This report contains student information; "
        "keep it where student records are allowed. Roster Check itself kept no copy: the export "
        "was read in memory and discarded once this report was made.</footer>"
    )


# ------------------------------------------------------------------- CSV
def findings_csv(result: Result) -> str:
    """Every finding, one per line, in a spreadsheet-friendly CSV."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_HEADERS)
    for f in result.findings:
        writer.writerow([_cell(v) for v in (
            f.row.line, result.row_label(f.row), f.rule.severity, f.rule.label,
            f.column, f.value, f.sentence, f.rule.fix, f.rule.id,
        )])
    return out.getvalue()


def findings_csv_bytes(result: Result) -> bytes:
    # The byte-order mark tells Excel the file is UTF-8. Without it, names
    # with accents (José, Ñuñez) open garbled.
    return ("﻿" + findings_csv(result)).encode("utf-8")


_PLAIN_NUMBER = re.compile(r"[-+]?\d+(\.\d+)?")


def _cell(value) -> str:
    """Stop Excel treating a value like "=HYPERLINK(...)" as a formula.

    A leading apostrophe makes Excel show the text as typed. Plain numbers
    such as a grade of -1 are left alone.
    """
    text = str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r") and not _PLAIN_NUMBER.fullmatch(text):
        return "'" + text
    return text


# -------------------------------------------------------------- terminal
def summary_text(result: Result) -> str:
    """A few lines for the terminal after a command-line check."""
    lines = [
        f"{result.source_name}: {len(result.table.rows):,} rows checked with \"{result.pack.name}\"",
        f"  {_plural(result.errors, 'error')}, {_plural(result.warnings, 'warning')} "
        f"on {_plural(result.rows_with_findings, 'row')}",
    ]
    for rule, found in result.by_rule():
        lines.append(f"  {len(found):>6,}  {rule.severity:<7}  {rule.label}")
    if result.skipped:
        lines.append(f"  {len(result.skipped)} check(s) not run: the file lacks their columns")
    lines += [f"  note: {note}" for note in result.notes]
    return "\n".join(lines)
