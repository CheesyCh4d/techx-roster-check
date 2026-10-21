# TechX Roster Check

Checks a PowerSchool export against a list of rules and reports every row that
fails one, in a report the front office can act on.

It is **stateless**: read the file, check it, show the report, forget it. No
student data is stored, cached, or logged. It runs on one workstation and
listens only on `127.0.0.1`.

```
students.txt (from Data Export Manager)
        │
        ├─ read: tab or CSV, UTF-8 or Windows-1252, any header spelling
        ├─ check: every rule in the pack (health.toml)
        └─ report: counts up top, by check or by student, CSV download
```

## What you need

| | |
|---|---|
| Python | 3.12 or newer |
| `pipx` | Kubuntu: `sudo apt install pipx` · Arch: `sudo pacman -S python-pipx` |

## Install

```bash
git clone https://github.com/CheesyCh4d/techx-roster-check
pipx install ./techx-roster-check
```

## Use it

**Web page** (for anyone):

```bash
techx-roster-check serve
```

A browser tab opens at `http://127.0.0.1:8766`. Drop in an export, pick a rule
pack, press **Check file**. The report has **Download CSV** and **Save report**
buttons; a saved report works offline with nothing running.

**Command line** (for scripts):

```bash
techx-roster-check check students.txt
```

Writes `students-report.html` and `students-findings.csv` next to the export,
readable only by you (`0600`). Exit code: `0` no errors, `1` errors found,
`2` the file or pack could not be used. Warnings alone exit `0`.

Try both on the fake sample: `examples/students-sample.txt`.

## Rule packs

A rule pack is a TOML file. Adding or changing a check means editing that
file; no Python changes. The built-in pack is `health` (district data health).

Make your own editable copy first, so upgrades never overwrite it:

```bash
techx-roster-check copy-pack health
techx-roster-check packs        # shows which copy is in use, and where
```

Your copy lives in `~/.config/techx-roster-check/packs/` and replaces the
built-in one. **Put your real school numbers in `health-schools.csv` there**;
the shipped list is examples. Packs are re-read on every check, so edits take
effect without restarting.

### Shape of a pack

```toml
[pack]
name = "District data health"
description = "Shown under the pack picker."
row_label = ["{student_number} · {last_name}, {first_name}", "{student_number}"]
date_formats = ["%m/%d/%Y", "%Y-%m-%d"]

[columns]
dob = ["birthdate", "birth_date"]      # other header spellings for "dob"

[[rules]]
id = "dob-present"                      # unique; appears in the CSV
type = "present"
column = "dob"
label = "Every student has a date of birth"   # what the report calls the check
severity = "error"                      # or "warning"; default "error"
fix = "Check the enrollment paperwork."  # optional hint for the office
```

Header matching ignores case, spaces, dashes and table prefixes, so
`Students.DOB`, `[1]DOB` and `dob` all match without being listed. Columns not
named in `[columns]` can still be used by a rule under their own name.

A rule runs only if the file has every column it names. Otherwise it is listed
under **Checks not run**, so one pack can serve student and contact exports.

### Rule types

| type | checks | settings |
|---|---|---|
| `present` | not blank | `column` or `columns` |
| `format` | matches a regular expression (whole value) | `pattern`, `expected` (finishes "is not ..."), `split` (e.g. `",;"` to check each address in one cell), `ignore_case` |
| `allowed` | one of a fixed list | `values`, `ignore_case` (default true) |
| `forbidden` | not one of a list | `values`, `ignore_case` |
| `reference` | in a list kept in a CSV beside the pack | `file`, `file_column` |
| `unique` | no duplicates | `column`/`columns` (each on its own) or `key` (combined) |
| `range` | a number within bounds | `min`, `max` |
| `date` | a real date, optionally in a window | `not_before`, `not_after` (a date or `"today"`) |
| `age` | a birthdate gives an age in years | `min`, `max` |
| `compare` | two columns on one row | `left`, `op` (`<` `<=` `>` `>=` `==` `!=`), `right`, `kind` (`date`/`number`/`text`) |
| `grade_age` | grade roughly matches age | `grade`, `dob`, `tolerance` (1), `age_at_k` (5), `cutoff` (`"09-01"`) |

Blank values are only reported by `present`; every other type skips them.
Unreadable dates are only reported by `date`; `age`, `compare` and
`grade_age` skip them. Each problem is reported once.

Any rule can take `when`, to look only at matching rows:

```toml
when = { column = "grade_level", in = ["12"] }          # also: not_in, filled = true/false
when = [{ column = "is_custodial", in = ["1", "y"] },   # a list: all must hold
        { column = "enroll_status", in = ["0"] }]
```

Set `enabled = false` to switch a rule off without deleting it.

Mistakes in a pack are reported by rule id ("Rule "dob-age" has settings an
"age" rule does not use: mn. Check the spelling.") rather than ignored.

## Privacy

- The web page sends the file as the body of one request. It is checked in
  memory and dropped; it is never written to disk, not even to a temp file.
- Responses are `Cache-Control: no-store`. Request logging is off.
- The server refuses non-loopback addresses, requests whose `Host` is not
  this machine (DNS rebinding), and cross-site `POST`s.
- Reports contain student data. The command line writes them `0600`.

## Project layout

```
techx_roster_check/
  reader.py     bytes -> Table: encoding, delimiter, header matching
  rules.py      one class per rule type, plus `when` conditions
  packs.py      find and load TOML packs, with friendly errors
  engine.py     run a pack over a Table -> Result
  report.py     Result -> HTML report and CSV
  app.py        the local web page (FastAPI)
  cli.py        check / serve / packs / copy-pack
  packs/        built-in packs (health.toml, health-schools.csv)
  web/style.css the stylesheet inlined into every page
examples/       a fake export with one problem per row
tests/          pytest suite
```

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

## Out of scope

Historical comparison over time (a separate, stateful project) and sending
anything. This tool only validates and reports.
