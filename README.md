# PK2API

Based on: https://github.com/JellyBitz/JMX-File-Editor

Python library to read and write data into the PK2 file format from Silkroad Online.

## Features

- Fast search O(1)
- Create new PK2 files
- Create new paths recursively if does not exist
- Full read/write support

## Installation

```bash
pip install pk2api
```

Or install from source:

```bash
pip install -e .
```

## Usage

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

# Read-write mode
with Pk2Stream("/path/to/Media.pk2", key) as pk2:
    # Add & remove folder
    pk2.add_folder("test/new_folder")
    pk2.remove_folder("test/new_folder")

    # Add & remove file
    pk2.add_file("test/new_file.txt", b"Hello World")
    pk2.remove_file("test/new_file.txt")
```

## API Reference

### Pk2Stream

Main class for reading and writing PK2 archives.

```python
Pk2Stream(path: str, key: str, read_only: bool = False)
```

**Methods:**

- `get_folder(path: str) -> Pk2Folder | None` - Get folder by path
- `get_file(path: str) -> Pk2File | None` - Get file by path
- `add_folder(path: str) -> bool` - Create folder (recursive)
- `add_file(path: str, data: bytes) -> bool` - Add or update file
- `remove_folder(path: str) -> bool` - Remove folder and contents
- `remove_file(path: str) -> bool` - Remove file
- `close()` - Close the stream

### Pk2Folder

Represents a folder within a PK2 archive.

**Properties:**

- `name: str` - Folder name
- `parent: Pk2Folder | None` - Parent folder
- `offset: int` - Byte offset in stream
- `files: dict[str, Pk2File]` - Files in this folder
- `folders: dict[str, Pk2Folder]` - Subfolders

**Methods:**

- `get_full_path() -> str` - Get full path from root

### Pk2File

Represents a file within a PK2 archive.

**Properties:**

- `name: str` - File name
- `parent: Pk2Folder` - Parent folder
- `offset: int` - Byte offset in stream
- `size: int` - File size in bytes

**Methods:**

- `get_full_path() -> str` - Get full path from root
- `get_content() -> bytes` - Read file content

## Known Issues

- New folder/file names are saved in lowercase

## Requirements

- Python 3.10+

---

> ### Special Thanks!
>
> - [**DummkopfOfHachtenduden**](https://www.elitepvpers.com/forum/members/1084164-daxtersoul.html)
> - [**pushedx**](https://www.elitepvpers.com/forum/members/900141-pushedx.html)
