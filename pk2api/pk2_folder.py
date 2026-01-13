"""Pk2Folder class representing a folder in a PK2 archive."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pk2_file import Pk2File


class Pk2Folder:
    """Represents a folder within a PK2 archive."""

    def __init__(
        self,
        name: str,
        parent: Pk2Folder | None,
        offset: int,
        original_name: str | None = None,
    ):
        """
        Initialize a Pk2Folder.

        Args:
            name: Folder name (lowercase)
            parent: Parent folder (None for root)
            offset: Byte offset of the folder's PackFileBlock in the stream
            original_name: Original case-preserved name from archive
        """
        self.name = name
        self.parent = parent
        self.offset = offset
        self.original_name = original_name or name
        self.files: dict[str, Pk2File] = {}
        self.folders: dict[str, Pk2Folder] = {}

    def get_full_path(self) -> str:
        """Get the full path to this folder from root (lowercase)."""
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

    def __repr__(self) -> str:
        return f"Pk2Folder(name={self.name!r}, offset={self.offset}, files={len(self.files)}, folders={len(self.folders)})"
