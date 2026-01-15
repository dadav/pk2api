# PK2API

Based on: https://github.com/JellyBitz/SRO.PK2API

Python library to read and write data into the PK2 file format from Silkroad Online.

## Features

- Fast search O(1) lookups
- Create new PK2 files
- Create new paths recursively if does not exist
- Full read/write support
- Bulk extract and import operations
- Archive comparison (diff) functionality
- Copy files/folders between archives
- Glob pattern matching
- Archive validation and integrity checks
- Command-line interface (CLI)
- Zero external dependencies (pure Python)

## Installation

```bash
pip install pk2api
```

Or install from source:

```bash
pip install -e .
```

## CLI Usage

After installation, the `pk2` command is available:

```bash
# List all files in an archive
pk2 list Media.pk2

# List files matching a glob pattern
pk2 list Media.pk2 -p "**/*.txt"

# Extract entire archive
pk2 extract Media.pk2 -o ./output

# Extract specific folder
pk2 extract Media.pk2 -f data -o ./output

# Import directory into archive
pk2 add Media.pk2 ./files -t target

# Show archive statistics
pk2 info Media.pk2

# Validate archive integrity
pk2 validate Media.pk2

# Compare two archives
pk2 compare old.pk2 new.pk2           # Text diff output
pk2 compare old.pk2 new.pk2 -f json   # JSON output for tools
pk2 cmp old.pk2 new.pk2 --quick       # Size-only comparison (faster)

# Copy between archives
pk2 copy src.pk2 dst.pk2 path/file.txt        # Copy single file
pk2 copy src.pk2 dst.pk2 "**/*.xml"           # Copy files matching glob
pk2 copy src.pk2 dst.pk2 data/folder -r       # Copy folder recursively
pk2 cp src.pk2 dst.pk2 folder -r -d backup    # Copy to different destination
```

## Python API Usage

```python
from pk2api import Pk2Stream

key = "169841"  # Default SRO key

# Read-only mode
with Pk2Stream("/path/to/Media.pk2", key, read_only=True) as pk2:
    # Get file content
    file = pk2.get_file("Type.txt")
    if file:
        content = file.get_content()
        print(content.decode("utf-8"))

    # List files from root folder
    root = pk2.get_folder("")
    print("Files:")
    for path in root.files.keys():
        print(f" - {path}")

    # List folders from root folder
    print("Folders:")
    for path in root.folders.keys():
        print(f" - {path}")

    # Find files by glob pattern
    for file in pk2.glob("**/*.txt"):
        print(file.get_full_path())

    # Iterate all files
    for file in pk2.iter_files():
        print(file.get_full_path())

    # Get archive statistics
    stats = pk2.get_stats()
    print(f"Files: {stats['file_count']}, Folders: {stats['folder_count']}")

# Read-write mode
with Pk2Stream("/path/to/Media.pk2", key) as pk2:
    # Add & remove folder
    pk2.add_folder("test/new_folder")
    pk2.remove_folder("test/new_folder")

    # Add & remove file
    pk2.add_file("test/new_file.txt", b"Hello World")
    pk2.remove_file("test/new_file.txt")

    # Bulk extract
    pk2.extract_all("./output")
    pk2.extract_folder("data", "./output")

    # Bulk import from disk
    pk2.import_from_disk("./files", "target/path")

    # Validate archive integrity
    result = pk2.validate()
    print(f"Valid: {result['valid']}")

# Copy between archives
with Pk2Stream("source.pk2", key, read_only=True) as src:
    with Pk2Stream("dest.pk2", key) as dst:
        dst.copy_file_from(src, "path/to/file.txt")
        dst.copy_folder_from(src, "data/folder")
        dst.copy_files_from(src, ["file1.txt", "file2.txt"])

# Compare archives
from pk2api import compare_archives

with Pk2Stream("old.pk2", key, read_only=True) as old:
    with Pk2Stream("new.pk2", key, read_only=True) as new:
        result = compare_archives(old, new)
        print(f"Added: {len(result.added_files)}")
        print(f"Removed: {len(result.removed_files)}")
        print(f"Modified: {len(result.modified_files)}")
```

## API Reference

### Pk2Stream

Main class for reading and writing PK2 archives.

```python
Pk2Stream(path: str, key: str, read_only: bool = False)
```

**Core Methods:**

- `get_folder(path: str) -> Pk2Folder | None` - Get folder by path (O(1))
- `get_file(path: str) -> Pk2File | None` - Get file by path (O(1))
- `add_folder(path: str) -> bool` - Create folder (recursive)
- `add_file(path: str, data: bytes) -> bool` - Add or update file
- `remove_folder(path: str) -> bool` - Remove folder and contents
- `remove_file(path: str) -> bool` - Remove file
- `close()` - Close the stream

**Iteration Methods:**

- `iter_files() -> Iterator[Pk2File]` - Iterate all files in archive
- `iter_folders() -> Iterator[Pk2Folder]` - Iterate all folders in archive
- `glob(pattern: str) -> Iterator[Pk2File]` - Find files matching glob pattern

**Utility Methods:**

- `get_stats() -> dict` - Return file/folder counts and sizes
- `validate() -> dict` - Check archive integrity
- `extract_all(output_dir: str, progress=None)` - Extract entire archive
- `extract_folder(folder_path: str, output_dir: str, progress=None)` - Extract subfolder
- `import_from_disk(source_dir: str, target_path: str = "", progress=None)` - Bulk import

**Inter-Archive Copy Methods:**

- `copy_file_from(source: Pk2Stream, source_path: str, target_path: str = None)` - Copy single file
- `copy_folder_from(source: Pk2Stream, source_path: str, target_path: str = None, progress=None)` - Copy folder recursively
- `copy_files_from(source: Pk2Stream, paths: list, target_base: str = "", progress=None)` - Copy multiple files

### Pk2Folder

Represents a folder within a PK2 archive.

**Properties:**

- `name: str` - Folder name (lowercase)
- `original_name: str` - Case-preserved name from archive
- `parent: Pk2Folder | None` - Parent folder
- `offset: int` - Byte offset in stream
- `files: dict[str, Pk2File]` - Files in this folder
- `folders: dict[str, Pk2Folder]` - Subfolders

**Methods:**

- `get_full_path() -> str` - Get full path from root (lowercase)
- `get_original_path() -> str` - Get full path with original case

### Pk2File

Represents a file within a PK2 archive.

**Properties:**

- `name: str` - File name (lowercase)
- `original_name: str` - Case-preserved name from archive
- `parent: Pk2Folder` - Parent folder
- `offset: int` - Byte offset in stream
- `size: int` - File size in bytes

**Methods:**

- `get_full_path() -> str` - Get full path from root (lowercase)
- `get_original_path() -> str` - Get full path with original case
- `get_content() -> bytes` - Read file content

### Comparison Functions

```python
from pk2api import compare_archives, ComparisonResult, FileChange, FolderChange, ChangeType
```

- `compare_archives(source: Pk2Stream, target: Pk2Stream, compute_hashes: bool = True) -> ComparisonResult`

**ComparisonResult Properties:**

- `added_files: list[FileChange]` - Files added in target
- `removed_files: list[FileChange]` - Files removed from source
- `modified_files: list[FileChange]` - Files modified between archives

**ChangeType Enum:**

- `ADDED`, `REMOVED`, `MODIFIED`, `UNCHANGED`

## Requirements

- Python 3.10+

---

> ### Special Thanks!
>
> - [**DummkopfOfHachtenduden**](https://www.elitepvpers.com/forum/members/1084164-daxtersoul.html)
> - [**pushedx**](https://www.elitepvpers.com/forum/members/900141-pushedx.html)
> - [**JellyBitz**](https://github.com/JellyBitz)
