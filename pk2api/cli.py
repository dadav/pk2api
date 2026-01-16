"""
PK2 Command Line Interface.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .comparison import ChangeType, compare_archives
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


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare two archives."""
    try:
        with Pk2Stream(args.source, args.key, read_only=True) as source:
            with Pk2Stream(args.target, args.key, read_only=True) as target:

                def progress_cb(current_file: str, current: int, total: int) -> None:
                    if not args.quiet and total > 0:
                        _progress_bar(current, total)

                result = compare_archives(
                    source,
                    target,
                    compute_hashes=not args.quick,
                    hash_algorithm=args.hash,
                    include_unchanged=args.all,
                    progress=progress_cb if not args.quiet else None,
                )

                if args.format == "json":
                    print(json.dumps(result.to_dict(), indent=2))
                else:
                    # Text output
                    print(f"Comparing: {args.source} -> {args.target}\n")

                    if result.removed_files:
                        print(f"Removed ({len(result.removed_files)}):")
                        for f in result.removed_files:
                            print(f"  - {f.original_path} ({_format_size(f.source_size)})")

                    if result.added_files:
                        print(f"\nAdded ({len(result.added_files)}):")
                        for f in result.added_files:
                            print(f"  + {f.original_path} ({_format_size(f.target_size)})")

                    if result.modified_files:
                        print(f"\nModified ({len(result.modified_files)}):")
                        for f in result.modified_files:
                            size_change = (f.target_size or 0) - (f.source_size or 0)
                            sign = "+" if size_change >= 0 else ""
                            print(
                                f"  * {f.original_path} ({sign}{_format_size(abs(size_change))})"
                            )

                    if result.unchanged_files:
                        print(f"\nUnchanged ({len(result.unchanged_files)}):")
                        for f in result.unchanged_files:
                            print(f"  = {f.original_path} ({_format_size(f.source_size)})")

                    if result.folder_changes:
                        removed_folders = [
                            f
                            for f in result.folder_changes
                            if f.change_type == ChangeType.REMOVED
                        ]
                        added_folders = [
                            f
                            for f in result.folder_changes
                            if f.change_type == ChangeType.ADDED
                        ]

                        if removed_folders:
                            print(f"\nFolders removed ({len(removed_folders)}):")
                            for f in removed_folders:
                                print(f"  - {f.original_path}/")

                        if added_folders:
                            print(f"\nFolders added ({len(added_folders)}):")
                            for f in added_folders:
                                print(f"  + {f.original_path}/")

                    if not result.has_differences:
                        print("Archives are identical")
                    else:
                        print(
                            f"\nSummary: {len(result.added_files)} added, "
                            f"{len(result.removed_files)} removed, "
                            f"{len(result.modified_files)} modified, "
                            f"{len(result.unchanged_files)} unchanged"
                        )

                # Return 0 if identical, 2 if different (like diff)
                return 0 if not result.has_differences else 2

    except Pk2AuthenticationError:
        print("Error: Invalid encryption key", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"Error: File not found: {e.filename}", file=sys.stderr)
        return 1


def cmd_copy(args: argparse.Namespace) -> int:
    """Copy files between archives."""
    try:
        with Pk2Stream(args.source, args.key, read_only=True) as source:
            with Pk2Stream(args.target, args.key) as target:
                progress = _progress_bar if not args.quiet else None

                if args.folder:
                    # Copy entire folder
                    count = target.copy_folder_from(
                        source,
                        args.path,
                        args.dest if args.dest else None,
                        progress=progress,
                    )
                else:
                    # Copy single file or glob pattern
                    if "*" in args.path or "?" in args.path:
                        # Glob pattern
                        files = source.glob(args.path)
                        if not files:
                            print(f"No files match pattern: {args.path}", file=sys.stderr)
                            return 1
                        paths = [f.get_full_path() for f in files]
                        count = target.copy_files_from(
                            source, paths, args.dest, progress=progress
                        )
                    else:
                        # Single file
                        if target.copy_file_from(
                            source, args.path, args.dest if args.dest else None
                        ):
                            count = 1
                        else:
                            print(f"Error: File not found: {args.path}", file=sys.stderr)
                            return 1

                if not args.quiet:
                    print(f"Copied {count} file(s)")
                return 0

    except Pk2AuthenticationError:
        print("Error: Invalid encryption key", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"Error: File not found: {e.filename}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_grep(args: argparse.Namespace) -> int:
    """Search for text in archive files."""
    try:
        with Pk2Stream(args.archive, args.key, read_only=True) as pk2:
            if args.file_pattern:
                files = pk2.glob(args.file_pattern)
            else:
                files = list(pk2.iter_files())

            pattern = args.pattern
            if args.ignore_case:
                pattern = pattern.lower()

            match_count = 0
            matched_files = set()

            for file in sorted(files, key=lambda f: f.get_full_path()):
                try:
                    content = file.get_content().decode("utf-8")
                except UnicodeDecodeError:
                    continue  # Skip binary files

                lines = content.splitlines()
                for line_num, line in enumerate(lines, 1):
                    search_line = line.lower() if args.ignore_case else line
                    if pattern in search_line:
                        match_count += 1
                        filepath = file.get_original_path()

                        if args.files_only:
                            if filepath not in matched_files:
                                matched_files.add(filepath)
                                print(filepath)
                        else:
                            print(f"{filepath}:{line_num}:{line}")

            if match_count == 0:
                return 2  # No matches (like grep)
            return 0

    except Pk2AuthenticationError:
        print("Error: Invalid encryption key", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"Error: File not found: {args.archive}", file=sys.stderr)
        return 1


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

    # compare command
    cmp_p = subparsers.add_parser(
        "compare", aliases=["cmp"], help="Compare two archives"
    )
    cmp_p.add_argument("source", help="Source archive (reference)")
    cmp_p.add_argument("target", help="Target archive to compare")
    cmp_p.add_argument(
        "--format",
        "-f",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    cmp_p.add_argument(
        "--quick",
        "-Q",
        action="store_true",
        help="Skip hash comparison (size-only for modifications)",
    )
    cmp_p.add_argument(
        "--hash",
        default="md5",
        choices=["md5", "sha256"],
        help="Hash algorithm (default: md5)",
    )
    cmp_p.add_argument(
        "--all", "-a", action="store_true", help="Include unchanged files in output"
    )
    cmp_p.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress progress output"
    )
    cmp_p.set_defaults(func=cmd_compare)

    # copy command
    cp_p = subparsers.add_parser("copy", aliases=["cp"], help="Copy files between archives")
    cp_p.add_argument("source", help="Source archive")
    cp_p.add_argument("target", help="Target archive")
    cp_p.add_argument("path", help="File path, glob pattern, or folder to copy")
    cp_p.add_argument(
        "--dest", "-d", default="", help="Destination path in target archive"
    )
    cp_p.add_argument(
        "--folder", "-r", action="store_true", help="Copy entire folder recursively"
    )
    cp_p.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress progress output"
    )
    cp_p.set_defaults(func=cmd_copy)

    # grep command
    grep_p = subparsers.add_parser("grep", help="Search for text in files")
    grep_p.add_argument("archive", help="PK2 archive path")
    grep_p.add_argument("pattern", help="Text to search for")
    grep_p.add_argument("-p", "--file-pattern", help="Glob pattern to filter files")
    grep_p.add_argument("-i", "--ignore-case", action="store_true", help="Case-insensitive")
    grep_p.add_argument("-l", "--files-only", action="store_true", help="Only print filenames")
    grep_p.set_defaults(func=cmd_grep)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
