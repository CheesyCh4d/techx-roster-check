"""The built-in health pack against the fake sample export.

Every row of examples/students-sample.txt was built to trip exactly one
check (or none), so this pins down what the pack reports end to end.
"""

from __future__ import annotations

from techx_roster_check.engine import check_file

from conftest import SAMPLE, TODAY

EXPECTED = {
    (2, "state-id-unique"),
    (3, "senior-counselor"),
    (4, "state-id-format"),
    (4, "student-number-unique"),
    (5, "state-id-unique"),
    (6, "grade-known"),
    (7, "student-number-present"),
    (8, "student-number-unique"),
    (9, "dob-valid"),
    (10, "dob-valid"),
    (11, "grade-age"),
    (12, "gender-known"),
    (13, "school-known"),
    (14, "exit-after-entry"),
    (15, "phone-format"),
    (16, "guardian-email-format"),
    (17, "zip-format"),
    (18, "active-not-graduated"),
    (19, "enroll-status-known"),
    (20, "state-id-present"),
    (21, "dob-age"),
}


def test_sample_findings(health):
    result = check_file(SAMPLE.read_bytes(), SAMPLE.name, health, today=TODAY)
    assert {(f.row.line, f.rule.id) for f in result.findings} == EXPECTED
    assert len(result.findings) == len(EXPECTED)
    assert (result.errors, result.warnings, result.rows_with_findings) == (15, 6, 20)
    assert {s.rule.id for s in result.skipped} == {"custodial-contact-email", "contact-email-format"}


def test_sample_reads_cleanly(health):
    result = check_file(SAMPLE.read_bytes(), SAMPLE.name, health, today=TODAY)
    assert result.notes == []
    assert result.table.delimiter_name == "tab"
    assert "zip" in result.table.columns          # "Mailing_Zip" via an alias
    names = {result.row_label(f.row) for f in result.findings}
    assert "100002 · Sample, Ben" in names


def test_contact_export(health):
    data = ("Student_Number\tIs_Custodial\tEmail_Address\n"
            "1\t1\t\n"
            "2\t0\t\n"
            "3\tY\tnot-an-email\n").encode()
    result = check_file(data, "contacts.txt", health, today=TODAY)
    assert {(f.row.line, f.rule.id) for f in result.findings} == {
        (2, "custodial-contact-email"), (4, "contact-email-format"),
    }
