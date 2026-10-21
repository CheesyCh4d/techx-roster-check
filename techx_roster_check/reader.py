"""Read a PowerSchool export into rows the rule engine can check.

PowerSchool's Data Export Manager (DEM) writes tab-delimited or comma files,
in UTF-8 or Windows-1252, with or without table names in the headers. This
module accepts all of those so nobody has to re-save a file before checking it.

Nothing here touches the disk: the file arrives as bytes and leaves as a Table.
"""

from __future__ import annotations

import codecs
import csv
import io
import re
from dataclasses import dataclass, field

# Delimiters we recognise, in the order we prefer them when the header line
# contains the same number of each. Tab is first because it is DEM's default.
DELIMITERS = ("\t", ",", "|", ";")
DELIMITER_NAMES = {"\t": "tab", ",": "comma", "|": "pipe", ";": "semicolon"}


class ReadError(ValueError):
    """The file could not be read as a table at all."""


@dataclass
class Row:
    """One line of the export: a student, a contact, an enrollment..."""

    line: int               # row number as Excel shows it; the header is row 1
    values: dict[str, str]  # column name -> cell text, outer spaces removed

    def get(self, column: str) -> str:
        """The cell for `column`, or "" when the file has no such column."""
        return self.values.get(column, "")


@dataclass
class Table:
    """Everything read from one export."""

    headers: list[str]      # header text exactly as written in the file
    columns: list[str]      # the column name each header was matched to
    rows: list[Row]
    encoding: str           # "UTF-8", "Windows-1252"... shown in the report
    delimiter: str
    notes: list[str] = field(default_factory=list)  # oddities worth telling the reader

    def has(self, column: str) -> bool:
        return column in self.columns

    @property
    def delimiter_name(self) -> str:
        return DELIMITER_NAMES.get(self.delimiter, repr(self.delimiter))


def normalize_header(text: str) -> str:
    """Reduce a header to a plain key so different spellings can match.

    "Students.Student_Number", "[1]Student_Number", " Student Number " and
    "STUDENT-NUMBER" all become "student_number".
    """
    text = text.strip().lstrip("﻿")
    text = re.sub(r"^\[[^\]]*\]", "", text)          # "[Students]" or "[1]" prefix
    parts = [part for part in text.split(".") if part.strip()]
    if parts:
        text = parts[-1]                             # "Students." table prefix
    text = re.sub(r"[^0-9a-z]+", "_", text.lower())  # spaces, dashes... -> "_"
    return text.strip("_")


def decode(data: bytes) -> tuple[str, str]:
    """Turn raw bytes into text. Returns (text, name of the encoding used).

    A byte-order mark settles it when present. Otherwise UTF-8 is tried first
    because it fails loudly on anything that is not UTF-8; Windows-1252 (what
    Excel calls "ANSI") is next; Latin-1 accepts every byte, so it never fails.
    """
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8):].decode("utf-8", errors="replace"), "UTF-8"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace"), "UTF-16"
    for encoding, label in (("utf-8", "UTF-8"), ("cp1252", "Windows-1252")):
        try:
            return data.decode(encoding), label
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "Latin-1"


def sniff_delimiter(header_line: str) -> str:
    """Guess the delimiter from the header line: whichever appears most."""
    counts = {d: header_line.count(d) for d in DELIMITERS}
    best = max(DELIMITERS, key=lambda d: counts[d])  # ties go to the earlier one
    return best if counts[best] else ","


def read_table(data: bytes, aliases: dict[str, str] | None = None) -> Table:
    """Parse an export into a Table.

    `aliases` maps a normalized header spelling to the column name a rule
    pack uses, so "Birthdate" in the file satisfies a rule written for "dob".
    Headers with no alias keep their own normalized name, so any column in
    the file can still be used by a rule.
    """
    aliases = aliases or {}
    text, encoding = decode(data)
    first_line = next((line for line in text.splitlines() if line.strip()), None)
    if first_line is None:
        raise ReadError("The file is empty.")

    delimiter = sniff_delimiter(first_line)
    # Comma files put quotes around values that contain commas, so quotes must
    # be honoured. DEM's tab files are normally unquoted, and there a stray "
    # at the start of a value (a nickname, say) would swallow the rest of the
    # line, so quotes are left alone unless the header itself is quoted.
    if delimiter == "," or first_line.lstrip().startswith('"'):
        quoting = csv.QUOTE_MINIMAL
    else:
        quoting = csv.QUOTE_NONE
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, quoting=quoting)

    header_cells: list[str] = []
    for cells in reader:
        if any(cell.strip() for cell in cells):
            header_cells = cells
            break

    headers: list[str] = []
    columns: list[str] = []
    notes: list[str] = []
    for position, raw in enumerate(header_cells, start=1):
        key = normalize_header(raw) or f"column_{position}"
        name = aliases.get(key, key)
        if name in columns:
            first = headers[columns.index(name)]
            notes.append(
                f'The headers "{first}" and "{raw.strip()}" both mean "{name}". '
                f'Only "{first}" is checked.'
            )
            name = f"{key}_{position}"    # keep the values under a name no rule uses
        headers.append(raw.strip())
        columns.append(name)

    rows: list[Row] = []
    long_rows: list[int] = []
    short_rows: list[int] = []
    while True:
        start = reader.line_num + 1       # the line this record begins on
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            raise ReadError(f"Row {reader.line_num} could not be read: {exc}") from exc
        if not any(cell.strip() for cell in cells):
            continue                      # blank line, often at the end of the file
        if len(cells) > len(columns):
            long_rows.append(start)
        elif len(cells) < len(columns):
            short_rows.append(start)
        values = {
            name: (cells[i].strip() if i < len(cells) else "")
            for i, name in enumerate(columns)
        }
        rows.append(Row(line=start, values=values))

    if long_rows:
        notes.append(
            f"{rows_phrase(long_rows).capitalize()} {_has(long_rows)} more values than "
            "there are headers. The extra values were ignored."
        )
    if short_rows:
        notes.append(
            f"{rows_phrase(short_rows).capitalize()} {_has(short_rows)} fewer values than "
            "there are headers, so the missing values count as blank. A line break "
            "inside a value (a comment field, for example) splits one row into two "
            "and causes this."
        )
    return Table(headers, columns, rows, encoding, delimiter, notes)


def rows_phrase(lines: list[int], limit: int = 5) -> str:
    """Describe row numbers briefly: "row 7" or "3 rows (7, 9, 12)"."""
    if len(lines) == 1:
        return f"row {lines[0]}"
    shown = ", ".join(str(n) for n in lines[:limit])
    if len(lines) > limit:
        shown += f" and {len(lines) - limit} more"
    return f"{len(lines)} rows ({shown})"


def _has(lines: list[int]) -> str:
    return "has" if len(lines) == 1 else "have"
