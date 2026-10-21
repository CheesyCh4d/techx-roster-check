"""The command line: report files, exit codes, pack copies, loopback-only serve."""

from __future__ import annotations

import shutil
import stat

from techx_roster_check.cli import main

from conftest import SAMPLE


def test_check_writes_private_report_and_csv(tmp_path, capsys):
    export = tmp_path / "students.txt"
    shutil.copyfile(SAMPLE, export)
    code = main(["check", str(export), "--today", "2026-10-21"])
    assert code == 1                                         # errors were found
    for name in ("students-report.html", "students-findings.csv"):
        path = tmp_path / name
        assert path.is_file()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    out = capsys.readouterr().out
    assert "15 errors, 6 warnings on 20 rows" in out


def test_clean_file_exits_zero(tmp_path):
    export = tmp_path / "ok.txt"
    export.write_text("Student_Number\n1\n2\n")
    assert main(["check", str(export), "--out", str(tmp_path / "r.html")]) == 0
    assert (tmp_path / "ok-findings.csv").is_file()


def test_unusable_input_exits_two(tmp_path, capsys):
    assert main(["check", str(tmp_path / "missing.txt")]) == 2
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    assert main(["check", str(empty)]) == 2
    assert main(["check", str(SAMPLE), "--pack", "nope"]) == 2
    assert "error:" in capsys.readouterr().err


def test_copy_pack(user_packs, capsys):
    assert main(["copy-pack", "health"]) == 0
    assert (user_packs / "health.toml").is_file()
    assert (user_packs / "health-schools.csv").is_file()
    assert main(["copy-pack", "health"]) == 2                # already there
    assert main(["copy-pack", "health", "--force"]) == 0
    assert main(["packs"]) == 0
    assert "your copy" in capsys.readouterr().out


def test_serve_refuses_other_interfaces(capsys):
    assert main(["serve", "--host", "0.0.0.0", "--no-browser"]) == 2
    assert "refusing" in capsys.readouterr().err
