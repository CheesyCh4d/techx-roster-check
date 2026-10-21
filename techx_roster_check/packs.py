"""Find and load rule packs.

A rule pack is a TOML file with three parts:

    [pack]        its name, description, and how to label a row in the report
    [columns]     each column the rules use, plus other header spellings for it
    [[rules]]     one block per check (the types are described in rules.py)

Packs that ship with the tool live in the `packs` folder inside the package.
Your own editable copies live in your config folder (see `user_pack_dir`)
and win over a built-in pack with the same file name, so upgrading the tool
never overwrites your changes.
"""

from __future__ import annotations

import os
import string
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .reader import Row, Table, normalize_header
from .rules import PackContext, PackError, Reference, Rule, build_rule

BUILTIN_DIR = Path(__file__).parent / "packs"
DEFAULT_DATE_FORMATS = ["%m/%d/%Y", "%Y-%m-%d"]


def user_pack_dir() -> Path:
    """Linux: ~/.config/techx-roster-check/packs
    Windows: %APPDATA%\\techx-roster-check\\packs"""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "techx-roster-check" / "packs"


@dataclass
class RowLabel:
    """One `row_label` template, e.g. "{student_number} · {last_name}, {first_name}"."""

    template: str
    fields: dict[str, str]          # name used in the template -> column name

    def fits(self, table: Table) -> bool:
        return all(table.has(column) for column in self.fields.values())

    def render(self, row: Row) -> str:
        text = self.template.format_map({name: row.get(col) for name, col in self.fields.items()})
        return text.strip(" ·,-")     # tidy up when a leading/trailing value is blank


@dataclass
class Pack:
    name: str
    description: str
    path: Path
    aliases: dict[str, str]          # normalized header spelling -> column name
    rules: list[Rule]                # enabled rules only, in file order
    row_labels: list[RowLabel] = field(default_factory=list)
    date_formats: list[str] = field(default_factory=lambda: list(DEFAULT_DATE_FORMATS))
    disabled: int = 0                # how many rules have enabled = false

    def label_for(self, table: Table):
        """Pick the first row_label template whose columns are all in this file."""
        return next((label for label in self.row_labels if label.fits(table)), None)

    def columns_used(self) -> list[str]:
        seen: list[str] = []
        for rule in self.rules:
            for column in rule.columns_needed():
                if column not in seen:
                    seen.append(column)
        return seen

    def support_files(self) -> list[Path]:
        """Files the pack reads besides itself (reference lists)."""
        return sorted({rule.path for rule in self.rules if isinstance(rule, Reference)})


def available_packs() -> dict[str, Path]:
    """Every pack by short name ("health"), your copies replacing built-ins."""
    found = {path.stem: path for path in sorted(BUILTIN_DIR.glob("*.toml"))}
    user_dir = user_pack_dir()
    if user_dir.is_dir():
        found.update({path.stem: path for path in sorted(user_dir.glob("*.toml"))})
    return found


def find_pack(name_or_path: str, allow_paths: bool = True) -> Path:
    """Resolve "health" (a pack name) or "./my-rules.toml" (a file) to a path."""
    packs = available_packs()
    if name_or_path in packs:
        return packs[name_or_path]
    if allow_paths:
        path = Path(name_or_path).expanduser()
        if path.suffix == ".toml" and path.is_file():
            return path
    names = ", ".join(packs) or "none"
    raise PackError(f'There is no rule pack called "{name_or_path}". Available packs: {names}.')


def load_pack(path: Path) -> Pack:
    """Read a pack file and check every rule in it. Raises PackError on mistakes."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PackError(f"The rule pack {path} does not exist.")
    except tomllib.TOMLDecodeError as exc:
        raise PackError(f"{path.name} is not valid TOML: {exc}")

    unknown = sorted(set(raw) - {"pack", "columns", "rules"})
    if unknown:
        raise PackError(f"{path.name} has sections this tool does not use: {', '.join(unknown)}.")

    meta = raw.get("pack", {})
    unknown = sorted(set(meta) - {"name", "description", "row_label", "date_formats"})
    if unknown:
        raise PackError(f"{path.name} [pack] has settings this tool does not use: {', '.join(unknown)}.")

    aliases = _read_aliases(raw.get("columns", {}), path.name)
    date_formats = meta.get("date_formats") or list(DEFAULT_DATE_FORMATS)
    ctx = PackContext(folder=path.parent, date_formats=list(date_formats), aliases=aliases)

    templates = meta.get("row_label", [])
    if isinstance(templates, str):
        templates = [templates]
    row_labels = [_read_row_label(t, ctx, path.name) for t in templates]

    rules: list[Rule] = []
    seen_ids: set[str] = set()
    disabled = 0
    for position, entry in enumerate(raw.get("rules", []), start=1):
        rule = build_rule(entry, position, ctx)
        if rule.id in seen_ids:
            raise PackError(f'{path.name} has two rules with the id "{rule.id}". Each id must be unique.')
        seen_ids.add(rule.id)
        if rule.enabled:
            rules.append(rule)
        else:
            disabled += 1

    return Pack(
        name=str(meta.get("name") or path.stem),
        description=str(meta.get("description") or ""),
        path=path,
        aliases=aliases,
        rules=rules,
        row_labels=row_labels,
        date_formats=list(date_formats),
        disabled=disabled,
    )


def _read_aliases(columns: dict, file_name: str) -> dict[str, str]:
    """Build the spelling -> column map from the [columns] section."""
    aliases: dict[str, str] = {}
    for column, spellings in columns.items():
        name = normalize_header(column)
        if isinstance(spellings, str):
            spellings = [spellings]
        if not isinstance(spellings, list):
            raise PackError(f'{file_name} [columns] "{column}" should be a list of other header spellings.')
        for spelling in [column, *spellings]:
            key = normalize_header(str(spelling))
            if aliases.get(key, name) != name:
                raise PackError(
                    f'{file_name} [columns] lists "{spelling}" under both '
                    f'"{aliases[key]}" and "{name}". A spelling can only mean one column.'
                )
            aliases[key] = name
    return aliases


def _read_row_label(template, ctx: PackContext, file_name: str) -> RowLabel:
    if not isinstance(template, str):
        raise PackError(f"{file_name} [pack] row_label should be text like \"{{student_number}}\".")
    fields = {}
    for _literal, name, spec, conversion in string.Formatter().parse(template):
        if name is None:
            continue
        if not name or spec or conversion or not name.isidentifier():
            raise PackError(f'{file_name} [pack] row_label "{template}" should use plain column names in {{braces}}.')
        fields[name] = ctx.column(name)
    return RowLabel(template, fields)
