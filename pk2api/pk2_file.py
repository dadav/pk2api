"""Pk2File class representing a file in a PK2 archive."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, BinaryIO

if TYPE_CHECKING:
    from .pk2_folder import Pk2Folder


class Pk2File:
    """Represents a file within a PK2 archive."""

    def __init__(
        self,
        name: str,
        parent: Pk2Folder,
        file_stream: BinaryIO,
        offset: int,
        size: int,
        original_name: str | None = None,
    ):
        """
        Initialize a Pk2File.

        Args:
            name: File name (lowercase)
            parent: Parent folder
            file_stream: The underlying file stream for reading content
            offset: Byte offset of the file data in the stream
            size: File size in bytes
            original_name: Original case-preserved name from archive
        """
        self.name = name
        self.parent = parent
        self._file_stream = file_stream
        self.offset = offset
        self.size = size
        self.original_name = original_name or name

    def get_full_path(self) -> str:
        """Get the full path to this file from root (lowercase)."""
        if self.parent is not None:
            parent_path = self.parent.get_full_path()
            if parent_path:
                return os.path.join(parent_path, self.name.lower())
            return self.name.lower()
        return self.name.lower()

    def get_original_path(self) -> str:
        """Get the full path with original case preserved."""
        if self.parent is not None:
            parent_path = self.parent.get_original_path()
            if parent_path:
                return os.path.join(parent_path, self.original_name)
            return self.original_name
        return self.original_name

    def get_content(self) -> bytes:
        """Read and return the file content."""
        self._file_stream.seek(self.offset)
        return self._file_stream.read(self.size)

    def __repr__(self) -> str:
        return f"Pk2File(name={self.name!r}, offset={self.offset}, size={self.size})"
