"""
PK2API - Python library for reading and writing Silkroad Online PK2 files.

This library provides an API to work with the encrypted PK2 archive format
used by Silkroad Online (JMXPACK format).

Example usage:
    from pk2api import Pk2Stream

    # Read-only mode
    with Pk2Stream("Media.pk2", "169841", read_only=True) as pk2:
        file = pk2.get_file("Type.txt")
        if file:
            content = file.get_content()
            print(content.decode("utf-8"))

    # Read-write mode
    with Pk2Stream("Media.pk2", "169841") as pk2:
        pk2.add_folder("test/new_folder")
        pk2.add_file("test/hello.txt", b"Hello World!")
"""
from .pk2_file import Pk2File
from .pk2_folder import Pk2Folder
from .pk2_stream import Pk2AuthenticationError, Pk2Stream
from .structures import (
    PackFileBlock,
    PackFileEntry,
    PackFileEntryType,
    PackFileHeader,
)

__version__ = "1.0.0"
__author__ = "Engels Quintero"

__all__ = [
    "Pk2Stream",
    "Pk2File",
    "Pk2Folder",
    "Pk2AuthenticationError",
    "PackFileHeader",
    "PackFileBlock",
    "PackFileEntry",
    "PackFileEntryType",
]
