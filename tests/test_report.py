"""The HTML report and the CSV: counts, escaping, spreadsheet safety."""

from __future__ import annotations

import base64
import csv
import io
import re

from techx_roster_check.engine import check_file
from techx_roster_check.report import file_stem, findings_csv, findings_csv_bytes, render_html

from conftest import SAMPLE, TODAY


def sample(health):
    return check_file(SAMPLE.read_bytes(), SAMPLE.name, health, today=TODAY)


def test_counts_up_top(health):
    page = render_html(sample(health))
    tiles = re.findall(r'<div class="num">([\d,]+)</div><div class="cap">([^<]+)</div>', page)
    assert tiles == [("22", "rows checked"), ("20", "rows with problems"),
                     ("15", "errors: must be fixed"), ("6", "warnings: worth a look")]


def test_both_views_and_sections(health):
    page = render_html(sample(health))
    assert 'id="view-check"' in page and 'id="view-student"' in page
    assert "Checks not run (2)" in page
    assert "About this file" in page


def test_values_from_the_file_are_escaped(write_pack):
    pack = write_pack('[[rules]]\nid = "r"\ntype = "allowed"\ncolumn = "name"\nvalues = ["x"]\n')
    data = b'Name\n<script>alert(1)</script>\n<img src=x onerror=alert(2)>\n'
    page = render_html(check_file(data, '<b>evil".txt', pack, today=TODAY))
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<img src=x" not in page
    assert "<b>evil" not in page


def test_no_problems_banner(write_pack):
    pack = write_pack('[[rules]]\nid = "r"\ntype = "present"\ncolumn = "a"\n')
    page = render_html(check_file(b"A\n1\n", "ok.txt", pack, today=TODAY))
    assert "No problems found. All 1 check that could run passed." in page
    assert 'id="view-check"' not in page


def test_embedded_csv_matches_download(health):
    result = sample(health)
    page = render_html(result)
    data = re.search(r'id="findings-csv" type="application/octet-stream">([^<]+)<', page).group(1)
    assert base64.b64decode(data) == findings_csv_bytes(result)


def test_csv_rows(health):
    result = sample(health)
    rows = list(csv.reader(io.StringIO(findings_csv(result))))
    assert rows[0] == ["Row", "Student", "Severity", "Check", "Column", "Value",
                       "Problem", "How to fix", "Check ID"]
    assert len(rows) == 1 + len(result.findings)
    aksid = next(row for row in rows if row[8] == "state-id-format")
    assert aksid == ["4", "100003 · Example, Cara", "error", "State ID (AKSID) is 10 digits",
                     "state_studentnumber", "12345", "state_studentnumber is not a 10-digit number",
                     "Re-enter the AKSID exactly as DEED issued it: 10 digits, no spaces or dashes.",
                     "state-id-format"]
    assert findings_csv_bytes(result).startswith(b"\xef\xbb\xbf")


def test_csv_neutralises_formulas(write_pack):
    pack = write_pack('[[rules]]\nid = "r"\ntype = "allowed"\ncolumn = "v"\nvalues = ["ok"]\n')
    data = b'V\tX\n=HYPERLINK("http://x","click")\t1\n-1\t2\n@SUM(A1)\t3\n'
    result = check_file(data, "f.txt", pack, today=TODAY)
    values = [row[5] for row in list(csv.reader(io.StringIO(findings_csv(result))))[1:]]
    assert values == ["'=HYPERLINK(\"http://x\",\"click\")", "-1", "'@SUM(A1)"]


def test_long_lists_are_cut_short_on_the_page(write_pack, monkeypatch):
    from techx_roster_check import report
    monkeypatch.setattr(report, "SHOWN_PER_CHECK", 3)
    pack = write_pack('[[rules]]\nid = "r"\ntype = "present"\ncolumn = "a"\n')
    data = b"A\tB\n" + b"\tx\n" * 5
    page = render_html(check_file(data, "f.txt", pack, today=TODAY))
    assert "Showing the first 3 of 5" in page


def test_file_stem():
    assert file_stem("Students 2026 (final).txt") == "Students_2026_final"
    assert file_stem("") == "export"
