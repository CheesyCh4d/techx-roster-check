"""Reading exports: encodings, delimiters, header spellings, row numbers."""

from __future__ import annotations

import pytest

from techx_roster_check.reader import ReadError, normalize_header, read_table, rows_phrase


class TestHeaders:
    @pytest.mark.parametrize("header", [
        "Student_Number", "Students.Student_Number", "[1]Student_Number",
        "[Students]Student_Number", " Student Number ", "STUDENT-NUMBER", "﻿Student_Number",
    ])
    def test_spellings_reduce_to_one_key(self, header):
        assert normalize_header(header) == "student_number"

    def test_aliases_rename_columns(self):
        table = read_table(b"Birthdate\tGrade\n01/02/2015\t5\n", aliases={"birthdate": "dob"})
        assert table.columns == ["dob", "grade"]
        assert table.headers == ["Birthdate", "Grade"]
        assert table.rows[0].get("dob") == "01/02/2015"

    def test_unknown_columns_are_kept_and_usable(self):
        table = read_table(b"Student_Number\tU_Bus_Route\n1\t7A\n")
        assert table.rows[0].get("u_bus_route") == "7A"

    def test_two_headers_meaning_the_same_column(self):
        table = read_table(b"DOB\tBirthdate\n01/01/2015\t02/02/2015\n",
                           aliases={"dob": "dob", "birthdate": "dob"})
        assert table.rows[0].get("dob") == "01/01/2015"
        assert any("both mean" in note for note in table.notes)


class TestEncodings:
    def test_utf8(self):
        table = read_table("Name\nJosé\n".encode("utf-8"))
        assert (table.encoding, table.rows[0].get("name")) == ("UTF-8", "José")

    def test_utf8_with_bom(self):
        table = read_table(b"\xef\xbb\xbfName\nAva\n")
        assert table.columns == ["name"]

    def test_windows_1252(self):
        table = read_table("Name\nNúñez\n".encode("cp1252"))
        assert (table.encoding, table.rows[0].get("name")) == ("Windows-1252", "Núñez")

    def test_utf16(self):
        table = read_table("Name\tGrade\nJosé\t3\n".encode("utf-16"))
        assert table.encoding == "UTF-16"
        assert table.rows[0].get("name") == "José"


class TestDelimiters:
    @pytest.mark.parametrize("delimiter,name", [("\t", "tab"), (",", "comma"), ("|", "pipe"), (";", "semicolon")])
    def test_detected(self, delimiter, name):
        data = delimiter.join(["A", "B", "C"]) + "\n" + delimiter.join(["1", "2", "3"]) + "\n"
        table = read_table(data.encode())
        assert table.delimiter_name == name
        assert table.rows[0].values == {"a": "1", "b": "2", "c": "3"}

    def test_quoted_comma_values(self):
        table = read_table(b'Name,Email\n"Smith, Jo","a@b.org, c@d.org"\n')
        assert table.rows[0].get("name") == "Smith, Jo"
        assert table.rows[0].get("email") == "a@b.org, c@d.org"

    def test_a_stray_quote_in_a_tab_file_stays_in_its_cell(self):
        table = read_table(b'Nickname\tGrade\n"Bud\t5\nAl\t6\n')
        assert [r.get("grade") for r in table.rows] == ["5", "6"]
        assert table.rows[0].get("nickname") == '"Bud'


class TestRows:
    def test_row_numbers_match_excel(self):
        table = read_table(b"A\n1\n\n3\n")
        assert [row.line for row in table.rows] == [2, 4]   # blank line 3 skipped

    def test_values_are_trimmed(self):
        table = read_table(b"A\tB\n  x  \t y\n")
        assert table.rows[0].values == {"a": "x", "b": "y"}

    def test_short_and_long_rows_are_noted(self):
        table = read_table(b"A\tB\tC\n1\t2\n1\t2\t3\t4\n")
        assert table.rows[0].get("c") == ""
        assert any("Row 2 has fewer values" in n for n in table.notes)
        assert any("Row 3 has more values" in n for n in table.notes)

    def test_crlf_line_endings(self):
        table = read_table(b"A\tB\r\n1\t2\r\n")
        assert table.rows[0].values == {"a": "1", "b": "2"}

    @pytest.mark.parametrize("data", [b"", b"\n\n  \n"])
    def test_empty_file(self, data):
        with pytest.raises(ReadError):
            read_table(data)

    def test_header_only(self):
        table = read_table(b"A\tB\n")
        assert table.columns == ["a", "b"] and table.rows == []


def test_rows_phrase():
    assert rows_phrase([7]) == "row 7"
    assert rows_phrase([7, 9]) == "2 rows (7, 9)"
    assert rows_phrase(list(range(1, 9))) == "8 rows (1, 2, 3, 4, 5 and 3 more)"
