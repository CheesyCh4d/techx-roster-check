"""Run a rule pack over an export and collect what was found.

This is the only place that knows about both halves: it reads the file with
the pack's column aliases, runs each rule over the rows it applies to, and
notes the rules that could not run because the file lacks a column they need.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .packs import Pack
from .reader import Row, Table, read_table
from .rules import Finding, Rule


@dataclass
class Skipped:
    """A rule that did not run, and the columns the file was missing."""

    rule: Rule
    missing: list[str]


@dataclass
class Result:
    source_name: str            # the export's file name, for the report title
    pack: Pack
    table: Table
    findings: list[Finding]
    skipped: list[Skipped]
    passed: list[Rule]          # rules that ran and found nothing
    checked_at: datetime
    notes: list[str] = field(default_factory=list)
    labels: dict[int, str] = field(default_factory=dict)   # row line -> "100234 · Smith, Jane"

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.rule.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.rule.severity == "warning")

    @property
    def rows_with_findings(self) -> int:
        return len({f.row.line for f in self.findings})

    def row_label(self, row: Row) -> str:
        return self.labels.get(row.line, "")

    def by_rule(self) -> list[tuple[Rule, list[Finding]]]:
        """Findings grouped by rule, in the order the pack lists its rules."""
        groups: dict[str, list[Finding]] = {}
        for finding in self.findings:
            groups.setdefault(finding.rule.id, []).append(finding)
        return [(rule, groups[rule.id]) for rule in self.pack.rules if rule.id in groups]

    def by_row(self) -> list[tuple[Row, list[Finding]]]:
        """Findings grouped by row (student), in file order."""
        groups: dict[int, list[Finding]] = {}
        rows: dict[int, Row] = {}
        for finding in self.findings:
            groups.setdefault(finding.row.line, []).append(finding)
            rows[finding.row.line] = finding.row
        return [(rows[line], groups[line]) for line in sorted(groups)]


def check_file(data: bytes, source_name: str, pack: Pack, today: date | None = None) -> Result:
    """Read an export's bytes and run `pack` over it."""
    table = read_table(data, aliases=pack.aliases)
    return run_checks(table, pack, source_name, today)


def run_checks(table: Table, pack: Pack, source_name: str = "", today: date | None = None) -> Result:
    today = today or date.today()
    findings: list[Finding] = []
    skipped: list[Skipped] = []
    passed: list[Rule] = []
    order = {rule.id: index for index, rule in enumerate(pack.rules)}

    for rule in pack.rules:
        missing = [column for column in rule.columns_needed() if not table.has(column)]
        if missing:
            skipped.append(Skipped(rule, missing))
            continue
        rows = [row for row in table.rows if rule.applies_to(row)]
        found = list(rule.check(rows, today))
        if found:
            findings.extend(found)
        else:
            passed.append(rule)

    # Row by row, and within a row in the pack's order, so a student's
    # problems read in the same sequence everywhere.
    findings.sort(key=lambda f: (f.row.line, order[f.rule.id]))

    notes = list(table.notes)
    used = pack.columns_used()
    if not table.rows:
        notes.insert(0, "The file has a header row but no data rows.")
    elif used and not any(table.has(column) for column in used):
        notes.insert(0, "None of the columns this rule pack checks are in this file. "
                        "Check that the right file and rule pack were chosen.")

    label = pack.label_for(table)
    labels = {f.row.line: label.render(f.row) for f in findings} if label else {}

    return Result(
        source_name=source_name,
        pack=pack,
        table=table,
        findings=findings,
        skipped=skipped,
        passed=passed,
        checked_at=datetime.now().replace(microsecond=0),
        notes=notes,
        labels=labels,
    )
