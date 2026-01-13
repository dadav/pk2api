"""
PK2 Command Line Interface.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pk2_stream import Pk2AuthenticationError, Pk2Stream


def _format_size(size: int) -> str:
    """Format byte size to human readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _progress_bar(current: int, total: int) -> None:
    """Print progress bar to stderr."""
    if total == 0:
        return
    width = 40
    filled = int(width * current / total)
    bar = "=" * filled + "-" * (width - filled)
    percent = current * 100 // total
    sys.stderr.write(f"\r[{bar}] {percent}% ({current}/{total})")
    sys.stderr.flush()
    if current == total:
        sys.stderr.write("\n")


def cmd_list(args: argparse.Namespace) -> int:
    """List archive contents."""
    try:
        with Pk2Stream(args.archive, args.key, read_only=True) as pk2:
            if args.pattern:
                files = pk2.glob(args.pattern)
            else:
                files = list(pk2.iter_files())

            for file in sorted(files, key=lambda f: f.get_full_path()):
                size_str = _format_size(file.size)
                print(f"{size_str:>10}  {file.get_original_path()}")

            print(f"\n{len(files)} file(s)")
    except Pk2AuthenticationError:
        print("Error: Invalid encryption key", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"Error: File not found: {args.archive}", file=sys.stderr)
        return 1
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    """Extract files from archive."""
    try:
        with Pk2Stream(args.archive, args.key, read_only=True) as pk2:
            progress = _progress_bar if not args.quiet else None

            if args.folder:
                count = pk2.extract_folder(args.folder, args.output, progress=progress)
            else:
                count = pk2.extract_all(args.output, progress=progress)

            if not args.quiet:
                print(f"Extracted {count} file(s) to {args.output}")
    except Pk2AuthenticationError:
        print("Error: Invalid encryption key", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"Error: File not found: {args.archive}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    """Add files to archive."""
    source = Path(args.source)
    if not source.exists():
        print(f"Error: Source not found: {args.source}", file=sys.stderr)
        return 1

    try:
        with Pk2Stream(args.archive, args.key) as pk2:
            progress = _progress_bar if not args.quiet else None

            if source.is_dir():
                count = pk2.import_from_disk(source, args.target, progress=progress)
            else:
                pk2.add_file(
                    args.target + "/" + source.name if args.target else source.name,
                    source.read_bytes(),
                )
                count = 1

            if not args.quiet:
                print(f"Added {count} file(s)")
    except Pk2AuthenticationError:
        print("Error: Invalid encryption key", file=sys.stderr)
        return 1
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    """Show archive information."""
    try:
        with Pk2Stream(args.archive, args.key, read_only=True) as pk2:
            stats = pk2.get_stats()
            print(f"Archive: {args.archive}")
            print(f"Files:   {stats['files']}")
            print(f"Folders: {stats['folders']}")
            print(f"Size:    {_format_size(stats['total_size'])}")
            print(f"On disk: {_format_size(stats['disk_used'])}")
    except Pk2AuthenticationError:
        print("Error: Invalid encryption key", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"Error: File not found: {args.archive}", file=sys.stderr)
        return 1
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate archive integrity."""
    try:
        with Pk2Stream(args.archive, args.key, read_only=True) as pk2:
            errors = pk2.validate()
            if errors:
                print(f"Found {len(errors)} error(s):")
                for error in errors:
                    print(f"  - {error}")
                return 1
            else:
                print("Archive is valid")
    except Pk2AuthenticationError:
        print("Error: Invalid encryption key", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"Error: File not found: {args.archive}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="pk2",
        description="PK2 archive tool for Silkroad Online",
    )
    parser.add_argument(
        "--key", "-k", default="169841", help="Encryption key (default: 169841)"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # list command
    list_p = subparsers.add_parser("list", aliases=["ls"], help="List archive contents")
    list_p.add_argument("archive", help="PK2 archive path")
    list_p.add_argument("--pattern", "-p", help="Glob pattern filter")
    list_p.set_defaults(func=cmd_list)

    # extract command
    ext_p = subparsers.add_parser("extract", aliases=["x"], help="Extract files")
    ext_p.add_argument("archive", help="PK2 archive path")
    ext_p.add_argument("--output", "-o", default=".", help="Output directory")
    ext_p.add_argument("--folder", "-f", help="Extract specific folder only")
    ext_p.add_argument("--quiet", "-q", action="store_true", help="Suppress progress")
    ext_p.set_defaults(func=cmd_extract)

    # add command
    add_p = subparsers.add_parser("add", help="Add files to archive")
    add_p.add_argument("archive", help="PK2 archive path")
    add_p.add_argument("source", help="Source file or directory to add")
    add_p.add_argument("--target", "-t", default="", help="Target path in archive")
    add_p.add_argument("--quiet", "-q", action="store_true", help="Suppress progress")
    add_p.set_defaults(func=cmd_add)

    # info command
    info_p = subparsers.add_parser("info", help="Show archive info")
    info_p.add_argument("archive", help="PK2 archive path")
    info_p.set_defaults(func=cmd_info)

    # validate command
    val_p = subparsers.add_parser("validate", help="Validate archive integrity")
    val_p.add_argument("archive", help="PK2 archive path")
    val_p.set_defaults(func=cmd_validate)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
