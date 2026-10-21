"""The kinds of check a rule pack can ask for.

Each class below is one rule *type*. A pack's [[rules]] entry names a type
("present", "format", ...) and gives its settings. `build_rule` turns that
entry into an object with one job: look at rows and yield a Finding for every
row that breaks the rule.

Every type also understands these shared settings:

    id        short unique name, used in the CSV and in error messages
    label     plain-English description of the check, shown in the report
    severity  "error" (must fix) or "warning" (worth a look); default "error"
    fix       optional hint telling the office how to fix it
    when      optional condition; the rule only looks at rows that match it
    enabled   set to false to switch a rule off without deleting it

Values that are blank are left to `present` rules. Every other type skips
blanks, so a missing birthdate is reported once ("dob is blank") rather than
once per rule that looks at birthdates.
"""

from __future__ import annotations

import operator
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .reader import ReadError, Row, normalize_header, read_table, rows_phrase

SEVERITIES = ("error", "warning")

# The school year is taken to begin on July 1. Grade/age checks measure age
# on the cutoff date (Sept 1 by default) of the school year `today` falls in.
SCHOOL_YEAR_START = (7, 1)


class PackError(ValueError):
    """A rule pack has a mistake that a person needs to fix."""


@dataclass
class PackContext:
    """What a rule needs from its pack while it is being built."""

    folder: Path                     # where the pack file lives
    date_formats: list[str]          # how dates are written in the exports
    aliases: dict[str, str]          # header spelling -> column name

    def column(self, text: str) -> str:
        """The column name a rule means, resolving any alias the pack lists."""
        key = normalize_header(text)
        return self.aliases.get(key, key)


@dataclass
class Finding:
    """One problem on one row."""

    rule: "Rule"
    row: Row
    column: str
    value: str        # the offending value, exactly as it appears in the file
    problem: str      # what is wrong, written to follow the column name

    @property
    def sentence(self) -> str:
        """The problem as a full sentence, e.g. "dob is blank"."""
        return f"{self.column} {self.problem}"


class Settings:
    """A rule's settings from the pack, read one at a time.

    Each read is remembered, so once a rule has taken everything it knows
    about, whatever is left over must be a typo ("colum" for "column") and
    can be reported instead of being silently ignored.
    """

    def __init__(self, raw: dict, where: str):
        self.raw = raw
        self.where = where
        self.used: set[str] = set()

    def error(self, message: str) -> PackError:
        return PackError(f"{self.where} {message}")

    def get(self, key: str, default=None):
        self.used.add(key)
        return self.raw.get(key, default)

    def text(self, key: str, default: str | None = None, required: bool = False) -> str | None:
        value = self.get(key)
        if value is None or value == "":
            if required:
                raise self.error(f'needs a "{key}" setting.')
            return default
        if isinstance(value, (list, dict)):
            raise self.error(f'has a "{key}" setting that should be a single value.')
        return str(value)

    def text_list(self, key: str, required: bool = False) -> list[str]:
        """A setting that may be one value or a list; always returns a list."""
        value = self.get(key)
        if value is None or value == [] or value == "":
            if required:
                raise self.error(f'needs a "{key}" setting.')
            return []
        if not isinstance(value, list):
            value = [value]
        return [str(item) for item in value]

    def number(self, key: str, default: float | None = None) -> float | None:
        value = self.get(key)
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise self.error(f'has a "{key}" setting that should be a number (no quotes).')
        return value

    def flag(self, key: str, default: bool) -> bool:
        value = self.get(key, default)
        if not isinstance(value, bool):
            raise self.error(f'has a "{key}" setting that should be true or false.')
        return value

    def leftovers(self) -> list[str]:
        return sorted(set(self.raw) - self.used)


class Condition:
    """One test from a rule's `when` setting, such as grade_level in ["12"]."""

    def __init__(self, raw, where: str, ctx: PackContext):
        if not isinstance(raw, dict):
            raise PackError(f'{where} has a "when" that should look like '
                            '{ column = "grade_level", in = ["12"] }.')
        s = Settings(raw, f'{where} "when"')
        self.column = ctx.column(s.text("column", required=True))
        self.within = _folded(s.text_list("in")) if "in" in raw else None
        self.not_within = _folded(s.text_list("not_in")) if "not_in" in raw else None
        self.filled = s.flag("filled", True) if "filled" in raw else None
        if self.within is None and self.not_within is None and self.filled is None:
            raise s.error('needs one of "in", "not_in" or "filled".')
        if s.leftovers():
            raise s.error(f"has settings it does not use: {', '.join(s.leftovers())}.")

    def matches(self, row: Row) -> bool:
        value = row.get(self.column)
        if self.filled is not None and bool(value) != self.filled:
            return False
        if self.within is not None and value.casefold() not in self.within:
            return False
        if self.not_within is not None and value.casefold() in self.not_within:
            return False
        return True

    def describe(self) -> str:
        parts = []
        if self.filled is True:
            parts.append(f"{self.column} is filled in")
        elif self.filled is False:
            parts.append(f"{self.column} is blank")
        if self.within is not None:
            parts.append(f"{self.column} is {_or_list(self.within)}")
        if self.not_within is not None:
            parts.append(f"{self.column} is not {_or_list(self.not_within)}")
        return " and ".join(parts)


class Rule:
    """What every rule type has in common. Subclasses add `check`."""

    type_name = ""    # what a pack writes in type = "..."

    def __init__(self, s: Settings, ctx: PackContext):
        s.get("type")                                  # already checked by build_rule
        self.id = s.text("id", required=True)
        self.label = s.text("label", default="")
        self.severity = s.text("severity", default="error").lower()
        if self.severity not in SEVERITIES:
            raise s.error('has a "severity" that should be "error" or "warning".')
        self.fix = s.text("fix", default="")
        self.enabled = s.flag("enabled", True)
        when = s.get("when")
        if when is None:
            self.conditions: list[Condition] = []
        else:
            # One condition, or a list of them that must all hold.
            items = when if isinstance(when, list) else [when]
            self.conditions = [Condition(item, s.where, ctx) for item in items]

    # -- subclasses override these ---------------------------------------
    def own_columns(self) -> list[str]:
        """The columns this rule reads (not counting `when`)."""
        return []

    def default_label(self) -> str:
        return self.id

    def check(self, rows: list[Row], today: date) -> Iterator[Finding]:
        raise NotImplementedError

    # -- shared helpers -----------------------------------------------------
    def columns_needed(self) -> list[str]:
        """Every column that must be in the file for this rule to run."""
        needed = list(self.own_columns())
        for condition in self.conditions:
            if condition.column not in needed:
                needed.append(condition.column)
        return needed

    def applies_to(self, row: Row) -> bool:
        return all(condition.matches(row) for condition in self.conditions)

    def scope(self) -> str:
        """"" for every row, or e.g. "Only rows where grade_level is 12"."""
        if not self.conditions:
            return ""
        return "Only rows where " + " and ".join(c.describe() for c in self.conditions)

    def finding(self, row: Row, column: str, value: str, problem: str) -> Finding:
        return Finding(self, row, column, value, problem)


def read_columns(s: Settings, ctx: PackContext) -> list[str]:
    """The `column` (one) or `columns` (several) a rule applies to."""
    names = s.text_list("column") + s.text_list("columns")
    if not names:
        raise s.error('needs a "column" (or "columns") setting.')
    return [ctx.column(name) for name in names]


# ---------------------------------------------------------------- types
class Present(Rule):
    """The value must not be blank."""

    type_name = "present"

    def __init__(self, s, ctx):
        super().__init__(s, ctx)
        self.columns = read_columns(s, ctx)

    def own_columns(self):
        return self.columns

    def default_label(self):
        verb = "is" if len(self.columns) == 1 else "are"
        return f"{_and_list(self.columns)} {verb} filled in"

    def check(self, rows, today):
        for row in rows:
            for column in self.columns:
                if not row.get(column):
                    yield self.finding(row, column, "", "is blank")


class Format(Rule):
    """The value must match a pattern (a regular expression).

    `expected` finishes the sentence "is not ...", e.g. "a 10-digit number".
    `split` lists characters that separate several values in one cell, such
    as the commas between addresses in PowerSchool's GuardianEmail field;
    each piece is then checked on its own.
    """

    type_name = "format"

    def __init__(self, s, ctx):
        super().__init__(s, ctx)
        self.columns = read_columns(s, ctx)
        flags = re.IGNORECASE if s.flag("ignore_case", False) else 0
        pattern = s.text("pattern", required=True)
        try:
            self.pattern = re.compile(pattern, flags)
        except re.error as exc:
            raise s.error(f'has a "pattern" that is not a valid regular expression: {exc}.')
        self.expected = s.text("expected", default="in the expected format")
        self.split = s.text("split", default="")

    def own_columns(self):
        return self.columns

    def default_label(self):
        return f"{_and_list(self.columns)} in the expected format"

    def pieces(self, value: str) -> list[str]:
        if not self.split:
            return [value]
        parts = re.split("[" + re.escape(self.split) + "]", value)
        return [part.strip() for part in parts if part.strip()]

    def check(self, rows, today):
        for row in rows:
            for column in self.columns:
                value = row.get(column)
                if not value:
                    continue
                pieces = self.pieces(value)
                bad = [p for p in pieces if not self.pattern.fullmatch(p)]
                if not bad:
                    continue
                problem = f"is not {self.expected}"
                if len(pieces) > 1:
                    problem += ": " + ", ".join(bad)
                yield self.finding(row, column, value, problem)


class Allowed(Rule):
    """The value must be one of a fixed list of codes."""

    type_name = "allowed"

    def __init__(self, s, ctx):
        super().__init__(s, ctx)
        self.columns = read_columns(s, ctx)
        self.values = s.text_list("values", required=True)
        self.ignore_case = s.flag("ignore_case", True)
        self.allowed = {self._key(v) for v in self.values}

    def _key(self, value: str) -> str:
        return value.casefold() if self.ignore_case else value

    def own_columns(self):
        return self.columns

    def default_label(self):
        return f"{_and_list(self.columns)} is a known code"

    def check(self, rows, today):
        if len(self.values) <= 20:
            problem = f"is not one of the allowed values ({', '.join(self.values)})"
        else:
            problem = "is not one of the allowed values"
        for row in rows:
            for column in self.columns:
                value = row.get(column)
                if value and self._key(value) not in self.allowed:
                    yield self.finding(row, column, value, problem)


class Forbidden(Rule):
    """The value must NOT be one of these codes.

    The opposite of `allowed`: for placeholders that should never stay in
    PowerSchool ("TBD", "000-000-0000"), or codes that are wrong only in
    combination with a `when` (grade 99 on an active student).
    """

    type_name = "forbidden"

    def __init__(self, s, ctx):
        super().__init__(s, ctx)
        self.columns = read_columns(s, ctx)
        self.values = s.text_list("values", required=True)
        self.ignore_case = s.flag("ignore_case", True)
        self.forbidden = {self._key(v) for v in self.values}

    def _key(self, value: str) -> str:
        return value.casefold() if self.ignore_case else value

    def own_columns(self):
        return self.columns

    def default_label(self):
        return f"{_and_list(self.columns)} is not a placeholder"

    def check(self, rows, today):
        for row in rows:
            for column in self.columns:
                value = row.get(column)
                if value and self._key(value) in self.forbidden:
                    yield self.finding(row, column, value, "is a value that should not be used here")


class Reference(Rule):
    """The value must appear in a list kept in its own file.

    For lists that change more often than the rules do, like the district's
    school numbers. `file` is a CSV beside the pack; `file_column` names the
    column to use (default: the first). Lines starting with # are comments.
    """

    type_name = "reference"

    def __init__(self, s, ctx):
        super().__init__(s, ctx)
        self.columns = read_columns(s, ctx)
        self.file = s.text("file", required=True)
        self.path = ctx.folder / self.file
        self.ignore_case = s.flag("ignore_case", True)
        wanted = s.text("file_column")
        self.known = {self._key(v) for v in _load_reference(self.path, wanted, s)}

    def _key(self, value: str) -> str:
        return value.casefold() if self.ignore_case else value

    def own_columns(self):
        return self.columns

    def default_label(self):
        return f"{_and_list(self.columns)} is in {self.file}"

    def check(self, rows, today):
        problem = f"is not in the list of known values ({self.file})"
        for row in rows:
            for column in self.columns:
                value = row.get(column)
                if value and self._key(value) not in self.known:
                    yield self.finding(row, column, value, problem)


def _load_reference(path: Path, wanted: str | None, s: Settings) -> list[str]:
    if not path.is_file():
        raise s.error(f'has a "file" that was not found: {path}')
    lines = path.read_bytes().splitlines(keepends=True)
    kept = b"".join(line for line in lines if not line.lstrip().startswith(b"#"))
    try:
        table = read_table(kept)
    except ReadError as exc:
        raise s.error(f"has a reference file that could not be read ({path.name}): {exc}")
    if not table.columns:
        raise s.error(f"has a reference file with no header row: {path.name}")
    column = normalize_header(wanted) if wanted else table.columns[0]
    if not table.has(column):
        raise s.error(f'names a "file_column" that {path.name} does not have: {wanted}')
    return [row.get(column) for row in table.rows if row.get(column)]


class Unique(Rule):
    """No two rows may share a value.

    `column` or `columns`: each column must be unique on its own.
    `key`: the listed columns *together* must be unique (e.g. student
    number + course + section for an enrollment export).
    """

    type_name = "unique"

    def __init__(self, s, ctx):
        super().__init__(s, ctx)
        key = [ctx.column(c) for c in s.text_list("key")]
        if key:
            if s.raw.get("column") or s.raw.get("columns"):
                raise s.error('should have either "key" or "column", not both.')
            self.keys = [key]
        else:
            self.keys = [[c] for c in read_columns(s, ctx)]

    def own_columns(self):
        return [c for key in self.keys for c in key]

    def default_label(self):
        return f"{_and_list([' + '.join(k) for k in self.keys])} is unique"

    def check(self, rows, today):
        for key in self.keys:
            groups: dict[tuple, list[Row]] = defaultdict(list)
            for row in rows:
                parts = tuple(row.get(c) for c in key)
                if all(parts):            # a blank part is a `present` problem
                    groups[tuple(p.casefold() for p in parts)].append(row)
            column = " + ".join(key)
            for group in groups.values():
                if len(group) < 2:
                    continue
                for row in group:
                    others = [other.line for other in group if other is not row]
                    value = " / ".join(row.get(c) for c in key)
                    yield self.finding(row, column, value, f"is also used on {rows_phrase(others)}")


class Range(Rule):
    """A number must fall between `min` and `max` (either may be left out)."""

    type_name = "range"

    def __init__(self, s, ctx):
        super().__init__(s, ctx)
        self.columns = read_columns(s, ctx)
        self.min = s.number("min")
        self.max = s.number("max")
        if self.min is None and self.max is None:
            raise s.error('needs a "min" or a "max" setting.')

    def own_columns(self):
        return self.columns

    def default_label(self):
        return f"{_and_list(self.columns)} is in range"

    def check(self, rows, today):
        for row in rows:
            for column in self.columns:
                value = row.get(column)
                if not value:
                    continue
                number = _to_number(value)
                if number is None:
                    yield self.finding(row, column, value, "is not a number")
                elif self.min is not None and number < self.min:
                    yield self.finding(row, column, value, f"is below the lowest allowed value, {_num(self.min)}")
                elif self.max is not None and number > self.max:
                    yield self.finding(row, column, value, f"is above the highest allowed value, {_num(self.max)}")


class DateCheck(Rule):
    """The value must be a real date, optionally inside a window.

    `not_before` / `not_after` take a date (1990-01-01) or "today". This is
    the only type that reports unreadable dates; `age`, `compare` and
    `grade_age` skip them so each bad date is reported once.
    """

    type_name = "date"

    def __init__(self, s, ctx):
        super().__init__(s, ctx)
        self.columns = read_columns(s, ctx)
        self.formats = ctx.date_formats
        self.not_before = _date_bound(s, "not_before")
        self.not_after = _date_bound(s, "not_after")

    def own_columns(self):
        return self.columns

    def default_label(self):
        return f"{_and_list(self.columns)} is a real date"

    def check(self, rows, today):
        earliest = _resolve(self.not_before, today)
        latest = _resolve(self.not_after, today)
        for row in rows:
            for column in self.columns:
                value = row.get(column)
                if not value:
                    continue
                when = parse_date(value, self.formats)
                if when is None:
                    yield self.finding(
                        row, column, value,
                        f"is not a date in a recognised format ({describe_formats(self.formats)})",
                    )
                elif earliest and when < earliest:
                    yield self.finding(row, column, value, f"is before {_long_date(earliest)}")
                elif latest and when > latest:
                    problem = "is in the future" if self.not_after == "today" else f"is after {_long_date(latest)}"
                    yield self.finding(row, column, value, problem)


class Age(Rule):
    """A birthdate must make the person between `min` and `max` years old today."""

    type_name = "age"

    def __init__(self, s, ctx):
        super().__init__(s, ctx)
        self.columns = read_columns(s, ctx)
        self.formats = ctx.date_formats
        self.min = s.number("min")
        self.max = s.number("max")
        if self.min is None and self.max is None:
            raise s.error('needs a "min" or a "max" setting (ages in years).')

    def own_columns(self):
        return self.columns

    def default_label(self):
        return f"{_and_list(self.columns)} gives a plausible age"

    def check(self, rows, today):
        for row in rows:
            for column in self.columns:
                value = row.get(column)
                born = parse_date(value, self.formats) if value else None
                if born is None or born > today:      # a `date` rule reports these
                    continue
                age = years_between(born, today)
                if self.min is not None and age < self.min:
                    yield self.finding(row, column, value, f"makes the student {age}; the youngest expected is {_num(self.min)}")
                elif self.max is not None and age > self.max:
                    yield self.finding(row, column, value, f"makes the student {age}; the oldest expected is {_num(self.max)}")


# How each operator reads in a sentence, per kind of value.
OPERATORS: dict[str, Callable] = {
    "<": operator.lt, "<=": operator.le, ">": operator.gt,
    ">=": operator.ge, "==": operator.eq, "!=": operator.ne,
}
OPERATOR_WORDS = {
    "date": {"<": "before", "<=": "on or before", ">": "after",
             ">=": "on or after", "==": "the same as", "!=": "different from"},
    "number": {"<": "less than", "<=": "at most", ">": "greater than",
               ">=": "at least", "==": "equal to", "!=": "different from"},
}
OPERATOR_WORDS["text"] = OPERATOR_WORDS["number"]


class Compare(Rule):
    """Two columns on the same row must compare a certain way.

    Example: exitdate >= entrydate. `kind` says how to read the values:
    "date", "number" or "text". Rows where either value is blank or
    unreadable are skipped.
    """

    type_name = "compare"

    def __init__(self, s, ctx):
        super().__init__(s, ctx)
        self.left = ctx.column(s.text("left", required=True))
        self.right = ctx.column(s.text("right", required=True))
        self.op = s.text("op", required=True)
        if self.op not in OPERATORS:
            raise s.error(f'has an "op" that should be one of: {" ".join(OPERATORS)}.')
        self.kind = s.text("kind", required=True)
        if self.kind not in OPERATOR_WORDS:
            raise s.error('has a "kind" that should be "date", "number" or "text".')
        self.formats = ctx.date_formats

    def own_columns(self):
        return [self.left, self.right]

    def default_label(self):
        return f"{self.left} is {OPERATOR_WORDS[self.kind][self.op]} {self.right}"

    def _read(self, value: str):
        if self.kind == "date":
            return parse_date(value, self.formats)
        if self.kind == "number":
            return _to_number(value)
        return value.casefold()

    def check(self, rows, today):
        test = OPERATORS[self.op]
        words = OPERATOR_WORDS[self.kind][self.op]
        for row in rows:
            left, right = row.get(self.left), row.get(self.right)
            if not left or not right:
                continue
            a, b = self._read(left), self._read(right)
            if a is None or b is None:
                continue
            if not test(a, b):
                yield self.finding(row, self.left, left, f"should be {words} {self.right} ({right})")


class GradeAge(Rule):
    """A student's age should roughly match their grade.

    Age is measured on the cutoff date (default Sept 1) of the current school
    year. A kindergartner is expected to be `age_at_k` (default 5) then, a
    first grader 6, and so on; anything more than `tolerance` years away
    (default 1) is reported. Grades outside -2 (PK3) to 12, such as 99 for
    graduated students, are skipped.
    """

    type_name = "grade_age"

    def __init__(self, s, ctx):
        super().__init__(s, ctx)
        self.grade = ctx.column(s.text("grade", required=True))
        self.dob = ctx.column(s.text("dob", required=True))
        self.tolerance = s.number("tolerance", 1)
        self.age_at_k = s.number("age_at_k", 5)
        cutoff = s.text("cutoff", default="09-01")
        match = re.fullmatch(r"(\d{1,2})-(\d{1,2})", cutoff)
        if not match:
            raise s.error('has a "cutoff" that should be a month and day, like "09-01".')
        self.cutoff = (int(match.group(1)), int(match.group(2)))
        self.formats = ctx.date_formats

    def own_columns(self):
        return [self.grade, self.dob]

    def default_label(self):
        return f"{self.grade} matches age"

    def cutoff_date(self, today: date) -> date:
        start_year = today.year if (today.month, today.day) >= SCHOOL_YEAR_START else today.year - 1
        return date(start_year, *self.cutoff)

    def check(self, rows, today):
        on = self.cutoff_date(today)
        for row in rows:
            grade_text = row.get(self.grade)
            if not re.fullmatch(r"-?\d+", grade_text):
                continue
            grade = int(grade_text)
            born = parse_date(row.get(self.dob), self.formats) if row.get(self.dob) else None
            if born is None or not -2 <= grade <= 12 or born > on:
                continue
            age = years_between(born, on)
            expected = grade + int(self.age_at_k)
            if abs(age - expected) > self.tolerance:
                yield self.finding(
                    row, self.grade, grade_text,
                    f"is {_grade_name(grade)} but the student was {age} on {_long_date(on)} "
                    f"(born {row.get(self.dob)}); about {expected} is typical",
                )


RULE_TYPES: dict[str, type[Rule]] = {
    cls.type_name: cls
    for cls in (Present, Format, Allowed, Forbidden, Reference, Unique, Range, DateCheck, Age,
                Compare, GradeAge)
}


def build_rule(raw, position: int, ctx: PackContext) -> Rule:
    """Turn one [[rules]] entry from a pack into a Rule object."""
    if not isinstance(raw, dict):
        raise PackError(f"Rule #{position} is not a table of settings.")
    where = f'Rule "{raw["id"]}"' if raw.get("id") else f"Rule #{position}"
    type_name = raw.get("type")
    if type_name not in RULE_TYPES:
        raise PackError(
            f'{where} has type "{type_name}", which is not one of: {", ".join(RULE_TYPES)}.'
        )
    settings = Settings(raw, where)
    rule = RULE_TYPES[type_name](settings, ctx)
    if settings.leftovers():
        raise settings.error(
            f'has settings a "{type_name}" rule does not use: {", ".join(settings.leftovers())}. '
            "Check the spelling."
        )
    if not rule.label:
        rule.label = rule.default_label()
    return rule


# ---------------------------------------------------------------- helpers
def parse_date(text: str, formats: Iterable[str]) -> date | None:
    """Read a date written in any of `formats`, or None if none fit."""
    for fmt in formats:
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def describe_formats(formats: Iterable[str]) -> str:
    """"%m/%d/%Y" -> "MM/DD/YYYY", for messages a person reads."""
    names = {"%m": "MM", "%d": "DD", "%Y": "YYYY", "%y": "YY"}
    shown = []
    for fmt in formats:
        for code, name in names.items():
            fmt = fmt.replace(code, name)
        shown.append(fmt)
    return " or ".join(shown)


def years_between(born: date, on: date) -> int:
    """Whole years from `born` to `on`: a person's age that day."""
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def _date_bound(s: Settings, key: str):
    """A not_before/not_after setting: a date, "today", or None."""
    value = s.get(key)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip().lower() == "today":
        return "today"
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise s.error(f'has a "{key}" that should be a date like 1990-01-01, or "today".')


def _resolve(bound, today: date) -> date | None:
    return today if bound == "today" else bound


def _to_number(text: str) -> float | None:
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _long_date(day: date) -> str:
    return f"{day:%b} {day.day}, {day.year}"


def _grade_name(grade: int) -> str:
    if grade == 0:
        return "grade K"
    if grade < 0:
        return f"grade {grade} (pre-K)"
    return f"grade {grade}"


def _folded(values: list[str]) -> set[str]:
    return {v.casefold() for v in values}


def _or_list(values: Iterable[str]) -> str:
    return " or ".join(sorted(values))


def _and_list(values: list[str]) -> str:
    return values[0] if len(values) == 1 else ", ".join(values[:-1]) + " and " + values[-1]
