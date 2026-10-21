"""Command line entry points.

    techx-roster-check check EXPORT.txt     check a file, write an HTML + CSV report
    techx-roster-check serve                open the local web page
    techx-roster-check packs                list rule packs and where they live
    techx-roster-check copy-pack health     make your own editable copy of a pack

`check` exits 0 when no errors were found, 1 when there were errors (warnings
alone do not count), and 2 when the file or pack could not be used, so it can
gate a script: `techx-roster-check check students.txt || echo "fix first"`.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import shutil
import socket
import sys
import threading
import webbrowser
from datetime import date
from pathlib import Path

from . import __version__
from .engine import check_file
from .packs import BUILTIN_DIR, available_packs, find_pack, load_pack, user_pack_dir
from .reader import ReadError
from .report import file_stem, findings_csv_bytes, render_html, summary_text
from .rules import PackError

DEFAULT_PORT = 8766

LOOPBACK_ONLY = (
    "refusing to listen on {host}: Roster Check has no login and shows student data, "
    "so it only listens on this computer (127.0.0.1)."
)


def write_private(path: Path, data: bytes) -> None:
    """Write a file only this user can read (0600): reports hold student data."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        os.fchmod(handle.fileno(), 0o600)    # in case the file already existed
        handle.write(data)


# ------------------------------------------------------------------ check
def cmd_check(args: argparse.Namespace) -> int:
    source = Path(args.file).expanduser()
    try:
        today = date.fromisoformat(args.today) if args.today else None
    except ValueError:
        print(f"error: --today should be a date like 2026-10-21, not {args.today}", file=sys.stderr)
        return 2
    try:
        pack = load_pack(find_pack(args.pack))
        data = source.read_bytes()
        result = check_file(data, source.name, pack, today=today)
    except (PackError, ReadError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: cannot read {source}: {exc.strerror}", file=sys.stderr)
        return 2

    stem = file_stem(source.name)
    html_path = Path(args.out).expanduser() if args.out else source.with_name(f"{stem}-report.html")
    csv_path = Path(args.csv).expanduser() if args.csv else html_path.with_name(f"{stem}-findings.csv")
    try:
        write_private(html_path, render_html(result).encode("utf-8"))
        write_private(csv_path, findings_csv_bytes(result))
    except OSError as exc:
        print(f"error: cannot write the report: {exc}", file=sys.stderr)
        return 2

    print(summary_text(result))
    print(f"\nreport: {html_path}\ncsv:    {csv_path}")
    if args.open:
        webbrowser.open(html_path.resolve().as_uri())
    return 1 if result.errors else 0


# ------------------------------------------------------------------ serve
def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def cmd_serve(args: argparse.Namespace) -> int:
    if not _is_loopback(args.host):
        print(LOOPBACK_ONLY.format(host=args.host), file=sys.stderr)
        return 2
    if not _port_free(args.host, args.port):
        print(f"error: port {args.port} is already in use. If Roster Check is already running, "
              f"open http://127.0.0.1:{args.port}; otherwise pick another with --port.",
              file=sys.stderr)
        return 2

    import uvicorn

    from .app import create_app

    url = f"http://127.0.0.1:{args.port}"
    print(f"TechX Roster Check is running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    # access_log=False: request lines would carry export file names.
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning", access_log=False)
    return 0


# ------------------------------------------------------------------ packs
def cmd_packs(args: argparse.Namespace) -> int:
    user_dir = user_pack_dir()
    for name, path in available_packs().items():
        where = "your copy" if path.parent == user_dir else "built in"
        try:
            pack = load_pack(path)
            print(f"{name:<14} {pack.name}  ({len(pack.rules)} checks, {where})")
        except PackError as exc:
            print(f"{name:<14} HAS A MISTAKE ({where}): {exc}")
        print(f"{'':<14} {path}")
    print(f"\nYour own packs go in {user_dir}")
    return 0


def cmd_copy_pack(args: argparse.Namespace) -> int:
    source = BUILTIN_DIR / f"{args.name}.toml"
    if not source.is_file():
        names = ", ".join(p.stem for p in sorted(BUILTIN_DIR.glob("*.toml")))
        print(f'error: there is no built-in pack called "{args.name}". Built-in packs: {names}',
              file=sys.stderr)
        return 2
    target_dir = user_pack_dir()
    files = [source, *load_pack(source).support_files()]
    targets = [target_dir / f.relative_to(BUILTIN_DIR) for f in files]
    existing = [t for t in targets if t.exists()]
    if existing and not args.force:
        print("error: these already exist (add --force to replace them):\n  "
              + "\n  ".join(str(t) for t in existing), file=sys.stderr)
        return 2
    for src, dst in zip(files, targets):
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        print(f"copied {dst}")
    print(f'\nEdit {targets[0]}. Your copy now replaces the built-in "{args.name}" pack.')
    return 0


# ------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="techx-roster-check",
        description="Check PowerSchool exports against rule packs and report every row that fails.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="check an export and write a report")
    check.add_argument("file", help="the export (tab-delimited or CSV)")
    check.add_argument("--pack", default="health", help="rule pack name or .toml path (default: health)")
    check.add_argument("--out", help="where to write the HTML report (default: next to the export)")
    check.add_argument("--csv", help="where to write the findings CSV (default: next to the report)")
    check.add_argument("--open", action="store_true", help="open the report in the browser")
    check.add_argument("--today", help=argparse.SUPPRESS)   # YYYY-MM-DD; for tests and reruns
    check.set_defaults(func=cmd_check)

    serve = sub.add_parser("serve", help="run the local web page")
    serve.add_argument("--host", default="127.0.0.1", help="loopback addresses only")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"default {DEFAULT_PORT}")
    serve.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    serve.set_defaults(func=cmd_serve)

    packs = sub.add_parser("packs", help="list rule packs")
    packs.set_defaults(func=cmd_packs)

    copy = sub.add_parser("copy-pack", help="copy a built-in pack to your config folder to edit")
    copy.add_argument("name", help="e.g. health")
    copy.add_argument("--force", action="store_true", help="replace an existing copy")
    copy.set_defaults(func=cmd_copy_pack)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
