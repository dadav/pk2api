"""
PK2 Command Line Interface.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Generator

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
)
from rich.text import Text

from .comparison import ChangeType, compare_archives
from .pk2_stream import Pk2AuthenticationError, Pk2Stream

# Global console instances (initialized in main())
console: Console
err_console: Console


def _init_consoles(no_color: bool) -> None:
    """Initialize global console instances."""
    global console, err_console
    console = Console(no_color=no_color)
    err_console = Console(stderr=True, no_color=no_color)


def _print_error(message: str) -> None:
    """Print error message to stderr in red."""
    err_console.print(f"[red]Error: {message}[/red]")


def _print_success(message: str) -> None:
    """Print success message in green."""
    console.print(f"[green]{message}[/green]")


def _format_size(size: int) -> str:
    """Format byte size to human readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _create_progress() -> Progress:
    """Create a progress bar with ETA."""
    return Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=err_console,
        transient=True,
    )


@contextmanager
def _progress_context(
    description: str, quiet: bool
) -> Generator[Callable[[int, int], None] | None, None, None]:
    """
    Context manager yielding a progress callback function.
    Returns None if quiet mode is enabled.
    """
    if quiet:
        yield None
        return

    with _create_progress() as progress:
        task_id = progress.add_task(description, total=None)

        def callback(current: int, total: int) -> None:
            progress.update(task_id, completed=current, total=total)

        yield callback


def _highlight_match(line: str, pattern: str, ignore_case: bool) -> Text:
    """Highlight pattern matches within a line."""
    text = Text(line)
    text.highlight_words([pattern], style="bold red", case_sensitive=not ignore_case)
    return text


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
                text = Text()
                text.append(f"{size_str:>10}", style="dim")
                text.append("  ")
                text.append(file.get_original_path())
                console.print(text)

            _print_success(f"\n{len(files)} file(s)")
    except Pk2AuthenticationError:
        _print_error("Invalid encryption key")
        return 1
    except FileNotFoundError:
        _print_error(f"File not found: {args.archive}")
        return 1
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    """Extract files from archive."""
    try:
        with Pk2Stream(args.archive, args.key, read_only=True) as pk2:
            with _progress_context("Extracting", args.quiet) as progress:
                if args.folder:
                    count = pk2.extract_folder(args.folder, args.output, progress=progress)
                else:
                    count = pk2.extract_all(args.output, progress=progress)

            if not args.quiet:
                _print_success(f"Extracted {count} file(s) to {args.output}")
    except Pk2AuthenticationError:
        _print_error("Invalid encryption key")
        return 1
    except FileNotFoundError:
        _print_error(f"File not found: {args.archive}")
        return 1
    except ValueError as e:
        _print_error(str(e))
        return 1
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    """Add files to archive."""
    source = Path(args.source)
    if not source.exists():
        _print_error(f"Source not found: {args.source}")
        return 1

    try:
        with Pk2Stream(args.archive, args.key) as pk2:
            with _progress_context("Adding", args.quiet) as progress:
                if source.is_dir():
                    count = pk2.import_from_disk(source, args.target, progress=progress)
                else:
                    pk2.add_file(
                        args.target + "/" + source.name if args.target else source.name,
                        source.read_bytes(),
                    )
                    count = 1

            if not args.quiet:
                _print_success(f"Added {count} file(s)")
    except Pk2AuthenticationError:
        _print_error("Invalid encryption key")
        return 1
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    """Show archive information."""
    try:
        with Pk2Stream(args.archive, args.key, read_only=True) as pk2:
            stats = pk2.get_stats()
            console.print(f"[dim]Archive:[/dim] {args.archive}")
            console.print(f"[dim]Files:[/dim]   {stats['files']}")
            console.print(f"[dim]Folders:[/dim] {stats['folders']}")
            console.print(f"[dim]Size:[/dim]    [cyan]{_format_size(stats['total_size'])}[/cyan]")
            console.print(f"[dim]On disk:[/dim] [cyan]{_format_size(stats['disk_used'])}[/cyan]")
    except Pk2AuthenticationError:
        _print_error("Invalid encryption key")
        return 1
    except FileNotFoundError:
        _print_error(f"File not found: {args.archive}")
        return 1
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate archive integrity."""
    try:
        with Pk2Stream(args.archive, args.key, read_only=True) as pk2:
            errors = pk2.validate()
            if errors:
                console.print(f"[red]Found {len(errors)} error(s):[/red]")
                for error in errors:
                    console.print(f"  [red]- {error}[/red]")
                return 1
            else:
                _print_success("Archive is valid")
    except Pk2AuthenticationError:
        _print_error("Invalid encryption key")
        return 1
    except FileNotFoundError:
        _print_error(f"File not found: {args.archive}")
        return 1
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare two archives."""
    try:
        with Pk2Stream(args.source, args.key, read_only=True) as source:
            with Pk2Stream(args.target, args.key, read_only=True) as target:
                with _progress_context("Comparing", args.quiet) as progress:

                    def progress_cb(current_file: str, current: int, total: int) -> None:
                        if progress and total > 0:
                            progress(current, total)

                    result = compare_archives(
                        source,
                        target,
                        compute_hashes=not args.quick,
                        hash_algorithm=args.hash,
                        include_unchanged=args.all,
                        progress=progress_cb if not args.quiet else None,
                    )

                if args.format == "json":
                    console.print(json.dumps(result.to_dict(), indent=2))
                else:
                    console.print(f"Comparing: {args.source} -> {args.target}\n")

                    if result.removed_files:
                        console.print(f"[bold]Removed ({len(result.removed_files)}):[/bold]")
                        for f in result.removed_files:
                            console.print(
                                f"  [red]- {f.original_path}[/red] [dim]({_format_size(f.source_size)})[/dim]"
                            )

                    if result.added_files:
                        console.print(f"\n[bold]Added ({len(result.added_files)}):[/bold]")
                        for f in result.added_files:
                            console.print(
                                f"  [green]+ {f.original_path}[/green] [dim]({_format_size(f.target_size)})[/dim]"
                            )

                    if result.modified_files:
                        console.print(f"\n[bold]Modified ({len(result.modified_files)}):[/bold]")
                        for f in result.modified_files:
                            size_change = (f.target_size or 0) - (f.source_size or 0)
                            sign = "+" if size_change >= 0 else ""
                            console.print(
                                f"  [yellow]* {f.original_path}[/yellow] [dim]({sign}{_format_size(abs(size_change))})[/dim]"
                            )

                    if result.unchanged_files:
                        console.print(f"\n[bold]Unchanged ({len(result.unchanged_files)}):[/bold]")
                        for f in result.unchanged_files:
                            console.print(
                                f"  [dim]= {f.original_path} ({_format_size(f.source_size)})[/dim]"
                            )

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
                            console.print(f"\n[bold]Folders removed ({len(removed_folders)}):[/bold]")
                            for f in removed_folders:
                                console.print(f"  [red]- {f.original_path}/[/red]")

                        if added_folders:
                            console.print(f"\n[bold]Folders added ({len(added_folders)}):[/bold]")
                            for f in added_folders:
                                console.print(f"  [green]+ {f.original_path}/[/green]")

                    if not result.has_differences:
                        _print_success("Archives are identical")
                    else:
                        console.print(
                            f"\n[bold]Summary:[/bold] [green]{len(result.added_files)} added[/green], "
                            f"[red]{len(result.removed_files)} removed[/red], "
                            f"[yellow]{len(result.modified_files)} modified[/yellow], "
                            f"[dim]{len(result.unchanged_files)} unchanged[/dim]"
                        )

                return 0 if not result.has_differences else 2

    except Pk2AuthenticationError:
        _print_error("Invalid encryption key")
        return 1
    except FileNotFoundError as e:
        _print_error(f"File not found: {e.filename}")
        return 1


def cmd_copy(args: argparse.Namespace) -> int:
    """Copy files between archives."""
    try:
        with Pk2Stream(args.source, args.key, read_only=True) as source:
            with Pk2Stream(args.target, args.key) as target:
                with _progress_context("Copying", args.quiet) as progress:
                    if args.folder:
                        count = target.copy_folder_from(
                            source,
                            args.path,
                            args.dest if args.dest else None,
                            progress=progress,
                        )
                    else:
                        if "*" in args.path or "?" in args.path:
                            files = source.glob(args.path)
                            if not files:
                                _print_error(f"No files match pattern: {args.path}")
                                return 1
                            paths = [f.get_full_path() for f in files]
                            count = target.copy_files_from(
                                source, paths, args.dest, progress=progress
                            )
                        else:
                            if target.copy_file_from(
                                source, args.path, args.dest if args.dest else None
                            ):
                                count = 1
                            else:
                                _print_error(f"File not found: {args.path}")
                                return 1

                if not args.quiet:
                    _print_success(f"Copied {count} file(s)")
                return 0

    except Pk2AuthenticationError:
        _print_error("Invalid encryption key")
        return 1
    except FileNotFoundError as e:
        _print_error(f"File not found: {e.filename}")
        return 1
    except ValueError as e:
        _print_error(str(e))
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
            search_pattern = pattern.lower() if args.ignore_case else pattern

            match_count = 0
            matched_files = set()

            for file in sorted(files, key=lambda f: f.get_full_path()):
                try:
                    content = file.get_content().decode("utf-8")
                except UnicodeDecodeError:
                    continue

                lines = content.splitlines()
                for line_num, line in enumerate(lines, 1):
                    search_line = line.lower() if args.ignore_case else line
                    if search_pattern in search_line:
                        match_count += 1
                        filepath = file.get_original_path()

                        if args.files_only:
                            if filepath not in matched_files:
                                matched_files.add(filepath)
                                console.print(f"[cyan]{filepath}[/cyan]")
                        else:
                            text = Text()
                            text.append(filepath, style="cyan")
                            text.append(":", style="dim")
                            text.append(str(line_num), style="dim")
                            text.append(":", style="dim")
                            text.append(_highlight_match(line, pattern, args.ignore_case))
                            console.print(text)

            if match_count == 0:
                return 2
            return 0

    except Pk2AuthenticationError:
        _print_error("Invalid encryption key")
        return 1
    except FileNotFoundError:
        _print_error(f"File not found: {args.archive}")
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
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output (also respects NO_COLOR env var)",
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

    # Initialize consoles with color preference
    no_color = args.no_color or os.environ.get("NO_COLOR") is not None
    _init_consoles(no_color)

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
