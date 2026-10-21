"""Each rule type, the shared `when` setting, and skipping missing columns."""

from __future__ import annotations

from datetime import date

from techx_roster_check.engine import check_file
from techx_roster_check.rules import GradeAge

from conftest import TODAY


def run(pack, data: str):
    return check_file(data.encode("utf-8"), "test.txt", pack, today=TODAY)


def problems(result):
    """(row, column, problem) for every finding: easy to compare in a test."""
    return [(f.row.line, f.column, f.problem) for f in result.findings]


def one_rule(write_pack, rule: str, extra: str = ""):
    return write_pack(f"[pack]\nname = \"t\"\n{extra}\n[[rules]]\nid = \"r\"\n{rule}\n")


class TestPresent:
    def test_blank_values(self, write_pack):
        pack = one_rule(write_pack, 'type = "present"\ncolumns = ["a", "b"]')
        result = run(pack, "A\tB\nx\t\n\ty\n")
        assert problems(result) == [(2, "b", "is blank"), (3, "a", "is blank")]


class TestFormat:
    def test_pattern_must_match_the_whole_value(self, write_pack):
        pack = one_rule(write_pack, 'type = "format"\ncolumn = "id"\npattern = \'\\d{3}\'\nexpected = "3 digits"')
        result = run(pack, "ID\n123\n1234\n12a\n\n")
        assert problems(result) == [(3, "id", "is not 3 digits"), (4, "id", "is not 3 digits")]

    def test_split_checks_each_piece(self, write_pack):
        pack = one_rule(write_pack, 'type = "format"\ncolumn = "e"\nsplit = ",;"\npattern = \'\\w+@\\w+\\.org\'\nexpected = "an email"')
        result = run(pack, 'E\na@b.org, c@d.org\na@b.org; nope\n')
        assert problems(result) == [(3, "e", "is not an email: nope")]


class TestAllowedAndForbidden:
    def test_allowed_ignores_case_by_default(self, write_pack):
        pack = one_rule(write_pack, 'type = "allowed"\ncolumn = "g"\nvalues = ["M", "F"]')
        result = run(pack, "G\nm\nF\nQ\n")
        assert [f.row.line for f in result.findings] == [4]
        assert "(M, F)" in result.findings[0].problem

    def test_numbers_in_values_match_text(self, write_pack):
        pack = one_rule(write_pack, 'type = "allowed"\ncolumn = "grade"\nvalues = [-1, 0, 12]')
        assert [f.value for f in run(pack, "Grade\n-1\n0\n12\n13\n").findings] == ["13"]

    def test_forbidden(self, write_pack):
        pack = one_rule(write_pack, 'type = "forbidden"\ncolumn = "phone"\nvalues = ["000-000-0000", "TBD"]')
        assert [f.value for f in run(pack, "Phone\ntbd\n907-555-0100\n").findings] == ["tbd"]


class TestReference:
    def test_values_come_from_a_file(self, write_pack, tmp_path):
        (tmp_path / "schools.csv").write_text("# comment\nschool_number,name\n100,A\n200,B\n")
        pack = one_rule(write_pack, 'type = "reference"\ncolumn = "schoolid"\nfile = "schools.csv"\nfile_column = "school_number"')
        assert [f.value for f in run(pack, "SchoolID\n100\n300\n").findings] == ["300"]


class TestUnique:
    def test_each_duplicate_names_the_others(self, write_pack):
        pack = one_rule(write_pack, 'type = "unique"\ncolumn = "id"')
        result = run(pack, "ID\n1\n2\n1\n\n\n1\n")
        assert problems(result) == [
            (2, "id", "is also used on 2 rows (4, 7)"),
            (4, "id", "is also used on 2 rows (2, 7)"),
            (7, "id", "is also used on 2 rows (2, 4)"),
        ]

    def test_blanks_are_not_duplicates(self, write_pack):
        pack = one_rule(write_pack, 'type = "unique"\ncolumn = "id"')
        assert run(pack, "ID\tX\n\t1\n\t2\n").findings == []

    def test_combined_key(self, write_pack):
        pack = one_rule(write_pack, 'type = "unique"\nkey = ["student", "course"]')
        result = run(pack, "Student\tCourse\n1\tMATH\n1\tENG\n1\tmath\n")
        assert [(f.row.line, f.column, f.value) for f in result.findings] == [
            (2, "student + course", "1 / MATH"), (4, "student + course", "1 / math"),
        ]


class TestRange:
    def test_numbers(self, write_pack):
        pack = one_rule(write_pack, 'type = "range"\ncolumn = "gpa"\nmin = 0\nmax = 4.5')
        result = run(pack, "GPA\n3.2\n-1\n5\nabc\n")
        assert [f.problem for f in result.findings] == [
            "is below the lowest allowed value, 0",
            "is above the highest allowed value, 4.5",
            "is not a number",
        ]


class TestDates:
    def test_unreadable_and_out_of_window(self, write_pack):
        pack = one_rule(write_pack, 'type = "date"\ncolumn = "dob"\nnot_before = 2000-01-01\nnot_after = "today"')
        result = run(pack, "DOB\n05/06/2015\n2015-05-06\n13/45/2015\n12/31/1999\n10/22/2026\n")
        assert [(f.row.line, f.problem) for f in result.findings] == [
            (4, "is not a date in a recognised format (MM/DD/YYYY or YYYY-MM-DD)"),
            (5, "is before Jan 1, 2000"),
            (6, "is in the future"),
        ]

    def test_pack_date_formats(self, write_pack):
        pack = one_rule(write_pack, 'type = "date"\ncolumn = "d"', extra='date_formats = ["%d.%m.%Y"]')
        assert [f.value for f in run(pack, "D\n21.10.2026\n10/21/2026\n").findings] == ["10/21/2026"]

    def test_age(self, write_pack):
        pack = one_rule(write_pack, 'type = "age"\ncolumn = "dob"\nmin = 3\nmax = 21')
        # On 2026-10-21: 2 years old, 3 the day of, 21, 22, and an unreadable date (skipped).
        result = run(pack, "DOB\n10/22/2023\n10/21/2023\n10/21/2005\n10/20/2004\nnope\n")
        assert [(f.row.line, f.problem) for f in result.findings] == [
            (2, "makes the student 2; the youngest expected is 3"),
            (5, "makes the student 22; the oldest expected is 21"),
        ]


class TestCompare:
    def test_dates(self, write_pack):
        pack = one_rule(write_pack, 'type = "compare"\nleft = "exitdate"\nop = ">="\nright = "entrydate"\nkind = "date"')
        result = run(pack, "EntryDate\tExitDate\n08/20/2026\t05/21/2027\n08/20/2026\t08/01/2026\n08/20/2026\t\n")
        assert problems(result) == [(3, "exitdate", "should be on or after entrydate (08/20/2026)")]

    def test_numbers(self, write_pack):
        pack = one_rule(write_pack, 'type = "compare"\nleft = "earned"\nop = "<="\nright = "attempted"\nkind = "number"')
        assert [f.row.line for f in run(pack, "Earned\tAttempted\n3\t4\n5\t4\n").findings] == [3]


class TestGradeAge:
    def test_age_on_the_cutoff_date(self, write_pack):
        pack = one_rule(write_pack, 'type = "grade_age"\ngrade = "grade_level"\ndob = "dob"')
        # Cutoff for 2026-10-21 is Sep 1, 2026. Grade 3 expects about 8.
        data = ("Grade_Level\tDOB\n"
                "3\t03/14/2018\n"     # 8: fine
                "3\t03/14/2017\n"     # 9: within 1
                "3\t03/14/2016\n"     # 10: reported
                "0\t09/01/2021\n"     # K, 5 exactly
                "99\t01/01/2000\n"    # graduated: skipped
                "K\t01/01/2010\n")    # not a number: skipped
        result = run(pack, data)
        assert [f.row.line for f in result.findings] == [4]
        assert "was 10 on Sep 1, 2026" in result.findings[0].problem

    def test_school_year_starts_in_july(self, write_pack):
        rule = one_rule(write_pack, 'type = "grade_age"\ngrade = "g"\ndob = "d"').rules[0]
        assert isinstance(rule, GradeAge)
        assert rule.cutoff_date(date(2027, 3, 1)) == date(2026, 9, 1)
        assert rule.cutoff_date(date(2027, 7, 1)) == date(2027, 9, 1)


class TestWhen:
    def test_rule_only_sees_matching_rows(self, write_pack):
        pack = one_rule(write_pack, 'type = "present"\ncolumn = "counselor"\nwhen = { column = "grade", in = ["12"] }')
        result = run(pack, "Grade\tCounselor\n11\t\n12\t\n12\tMs. R\n")
        assert [f.row.line for f in result.findings] == [3]
        assert result.pack.rules[0].scope() == "Only rows where grade is 12"

    def test_list_of_conditions_must_all_hold(self, write_pack):
        pack = one_rule(write_pack, 'type = "present"\ncolumn = "email"\nwhen = [{ column = "custodial", in = ["1"] }, { column = "status", not_in = ["inactive"] }]')
        result = run(pack, "Custodial\tStatus\tEmail\n1\tactive\t\n1\tInactive\t\n0\tactive\t\n")
        assert [f.row.line for f in result.findings] == [2]

    def test_filled(self, write_pack):
        pack = one_rule(write_pack, 'type = "present"\ncolumn = "exitcode"\nwhen = { column = "exitdate", filled = true }')
        assert [f.row.line for f in run(pack, "ExitDate\tExitCode\n\t\n06/01/2026\t\n").findings] == [3]

    def test_unique_within_matching_rows(self, write_pack):
        pack = one_rule(write_pack, 'type = "unique"\ncolumn = "locker"\nwhen = { column = "status", in = ["0"] }')
        result = run(pack, "Status\tLocker\n0\t5\n2\t5\n0\t6\n")
        assert result.findings == []


class TestSkipping:
    def test_missing_column_skips_the_rule(self, write_pack):
        pack = one_rule(write_pack, 'type = "present"\ncolumn = "contact_email"\nwhen = { column = "is_custodial", in = ["1"] }')
        result = run(pack, "Student_Number\n1\n")
        assert result.findings == []
        assert result.skipped[0].missing == ["contact_email", "is_custodial"]

    def test_wrong_file_is_noted(self, write_pack):
        pack = one_rule(write_pack, 'type = "present"\ncolumn = "dob"')
        result = run(pack, "Course\tSection\nMATH\t1\n")
        assert "None of the columns" in result.notes[0]

    def test_alias_in_a_rule_column(self, write_pack):
        pack = write_pack('[columns]\ndob = ["birthdate"]\n[[rules]]\nid = "r"\ntype = "present"\ncolumn = "Birthdate"\n')
        result = run(pack, "DOB\n\n01/01/2015\n\n")
        assert pack.rules[0].columns == ["dob"]
        assert [f.row.line for f in result.findings] == []
        assert run(pack, "Birthdate\tX\n\t1\n").findings[0].column == "dob"


class TestRowLabels:
    def test_first_template_that_fits(self, write_pack):
        pack = write_pack(
            '[pack]\nrow_label = ["{student_number} · {lastfirst}", "{student_number} · {last_name}, {first_name}"]\n'
            '[[rules]]\nid = "r"\ntype = "present"\ncolumn = "dob"\n'
        )
        result = run(pack, "Student_Number\tLast_Name\tFirst_Name\tDOB\n7\tSmith\tJo\t\n\tLee\tAl\t\n")
        assert [result.row_label(f.row) for f in result.findings] == ["7 · Smith, Jo", "Lee, Al"]
