"""
Pk2Stream - Main class for reading and writing PK2 archives.
"""
from __future__ import annotations

import math
import os
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from .pk2_file import Pk2File
from .pk2_folder import Pk2Folder
from .security import Blowfish
from .structures import (
    PackFileBlock,
    PackFileEntry,
    PackFileEntryType,
    PackFileHeader,
)


class Pk2AuthenticationError(Exception):
    """Raised when the Blowfish key is incorrect."""
    pass


class Pk2Stream:
    """
    PK2 file stream handler.

    Provides read/write access to Silkroad Online PK2 archives.
    Supports creating new archives, adding/removing files and folders.
    """

    def __init__(self, path: str | Path, key: str, read_only: bool = False):
        """
        Initialize a PK2 stream.

        Args:
            path: Path to the PK2 file
            key: Blowfish encryption key
            read_only: If True, open in read-only mode. Otherwise, create if doesn't exist.

        Raises:
            Pk2AuthenticationError: If the key is incorrect
            IOError: If there's an error reading the PK2 file
        """
        self._blowfish = Blowfish()
        self._blowfish.initialize(key)

        self._file_stream: BinaryIO
        self._header: PackFileHeader
        self._folders: dict[str, Pk2Folder] = {}
        self._files: dict[str, Pk2File] = {}
        self._disk_allocations: dict[int, int] = {}  # offset -> size

        path = Path(path)
        file_exists = path.exists()

        if read_only:
            self._file_stream = open(path, "rb")
        else:
            self._file_stream = open(path, "r+b" if file_exists else "w+b")
            if not file_exists:
                self._create_base_stream()

        # Read header
        self._file_stream.seek(0)
        header_bytes = self._file_stream.read(PackFileHeader.SIZE)
        self._header = PackFileHeader.from_bytes(header_bytes)

        # Track header allocation
        self._disk_allocations[0] = PackFileHeader.SIZE

        # Validate key by checking checksum
        checksum = self._blowfish.encode(b"Joymax Pack File")
        if checksum is None:
            raise Pk2AuthenticationError("Failed to generate checksum")

        comparer = bytes(3) + bytes(13)  # Only compare first 3 bytes
        expected = self._header.checksum[:3] + bytes(13)
        if checksum[:3] != self._header.checksum[:3]:
            raise Pk2AuthenticationError("Invalid encryption key")

        # Set up root folder
        root = Pk2Folder("", None, self._file_stream.tell())
        self._folders[""] = root

        # Load all blocks
        try:
            self._disk_allocations[root.offset] = PackFileBlock.SIZE
            self._initialize_stream_block(root.offset, root)
        except Exception as ex:
            raise IOError(f"Error reading PK2 file: {ex}") from ex

    def __enter__(self) -> Pk2Stream:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """Close the stream and free resources."""
        if hasattr(self, "_file_stream") and self._file_stream:
            self._file_stream.close()

    def get_folder(self, path: str) -> Pk2Folder | None:
        """
        Get a folder by path.

        Args:
            path: Folder path (forward or back slashes accepted)

        Returns:
            Pk2Folder if found, None otherwise
        """
        if path is None:
            raise ValueError("Path cannot be None")
        # Normalize path
        path = path.lower().replace("/", os.sep).replace("\\", os.sep)
        return self._folders.get(path)

    def get_file(self, path: str) -> Pk2File | None:
        """
        Get a file by path.

        Args:
            path: File path (forward or back slashes accepted)

        Returns:
            Pk2File if found, None otherwise
        """
        if not path:
            raise ValueError("Path cannot be empty")
        # Normalize path
        path = path.lower().replace("/", os.sep).replace("\\", os.sep)
        return self._files.get(path)

    def add_folder(self, path: str) -> bool:
        """
        Add a folder (creates parent folders if necessary).

        Args:
            path: Folder path to create

        Returns:
            True if created, False if already exists or couldn't be created
        """
        # Check if folder already exists
        if self.get_folder(path) is not None:
            return False
        # Make sure path doesn't exist as a file
        if self.get_file(path) is not None:
            return False

        # Normalize path
        path = path.lower().replace("/", os.sep).replace("\\", os.sep)

        # Find closest existing parent
        parts = path.split(os.sep)
        parent = None

        for i in range(len(parts), 0, -1):
            near_path = os.sep.join(parts[:i])
            if near_path in self._folders:
                parent = self._folders[near_path]
                parts = parts[i:]
                break

        # Use root if no parent found
        if parent is None:
            parent = self._folders[""]

        # Create missing folders
        if parts:
            self._create_folder_block(parent.offset, parent, list(parts))

        return True

    def add_file(self, path: str, data: bytes) -> bool:
        """
        Add or update a file.

        Args:
            path: File path
            data: File content

        Returns:
            True if successful, False otherwise
        """
        # Can't add a file where a folder exists
        if self.get_folder(path) is not None:
            return False

        # Normalize path
        path = path.lower().replace("/", os.sep).replace("\\", os.sep)

        existing_file = self.get_file(path)
        if existing_file is not None:
            # Update existing file
            if len(data) <= existing_file.size:
                file_offset = existing_file.offset
            else:
                # Need more space - remove old allocation and allocate new
                if existing_file.offset in self._disk_allocations:
                    del self._disk_allocations[existing_file.offset]
                file_offset = self._allocate_space(len(data))

            # Find and update block entry
            block_offset = existing_file.parent.offset
            while block_offset != 0:
                block = self._load_pack_file_block(block_offset)
                for i, entry in enumerate(block.entries):
                    if entry.entry_type == PackFileEntryType.FILE and entry.offset == existing_file.offset:
                        new_file = Pk2File(
                            existing_file.name,
                            existing_file.parent,
                            self._file_stream,
                            file_offset,
                            len(data),
                        )
                        # Write file data
                        self._file_stream.seek(new_file.offset)
                        self._file_stream.write(data)
                        self._file_stream.flush()
                        self._disk_allocations[new_file.offset] = new_file.size

                        # Update entry
                        block.entries[i].modification_time = datetime.now()
                        block.entries[i].size = new_file.size
                        block.entries[i].offset = new_file.offset
                        self._update_pack_file_block(block_offset, block)

                        # Update references
                        existing_file.parent.files[new_file.name] = new_file
                        self._files[path] = new_file
                        return True

                block_offset = block.entries[-1].next_block
            return False
        else:
            # Create new file
            folder_path = os.path.dirname(path)
            if folder_path:
                self.add_folder(folder_path)
            folder = self.get_folder(folder_path)
            if folder is None:
                folder = self._folders[""]

            # Find empty entry in folder's block chain
            block_offset = folder.offset
            while block_offset != 0:
                block = self._load_pack_file_block(block_offset)
                for i, entry in enumerate(block.entries):
                    if entry.entry_type == PackFileEntryType.EMPTY:
                        new_file = Pk2File(
                            os.path.basename(path),
                            folder,
                            self._file_stream,
                            self._allocate_space(len(data)),
                            len(data),
                        )
                        # Write file data
                        self._file_stream.seek(new_file.offset)
                        self._file_stream.write(data)
                        self._file_stream.flush()
                        self._disk_allocations[new_file.offset] = new_file.size

                        # Update entry
                        now = datetime.now()
                        block.entries[i].entry_type = PackFileEntryType.FILE
                        block.entries[i].name = new_file.name
                        block.entries[i].creation_time = now
                        block.entries[i].modification_time = now
                        block.entries[i].size = new_file.size
                        block.entries[i].offset = new_file.offset
                        self._update_pack_file_block(block_offset, block)

                        folder.files[new_file.name] = new_file
                        self._files[path] = new_file
                        return True

                # Move to next block or expand
                next_block = block.entries[-1].next_block
                if next_block == 0:
                    block_offset = self._expand_pack_file_block(block_offset, block)
                else:
                    block_offset = next_block
            return False

    def remove_folder(self, path: str) -> bool:
        """
        Remove a folder and all its contents.

        Args:
            path: Folder path to remove

        Returns:
            True if removed, False if not found or is root

        Raises:
            ValueError: If trying to remove root folder
        """
        if path == "":
            raise ValueError("Root folder cannot be removed")

        folder = self.get_folder(path)
        if folder is None:
            return False

        # Remove all links
        self._remove_folder_links(folder)

        # Remove entry from parent block
        block_offset = folder.parent.offset
        while block_offset != 0:
            block = self._load_pack_file_block(block_offset)
            for i, entry in enumerate(block.entries):
                if entry.entry_type == PackFileEntryType.FOLDER and entry.offset == folder.offset:
                    block.entries[i] = PackFileEntry.get_default()
                    self._update_pack_file_block(block_offset, block)
                    del folder.parent.folders[folder.name]
                    return True
            block_offset = block.entries[-1].next_block

        return False

    def remove_file(self, path: str) -> bool:
        """
        Remove a file.

        Args:
            path: File path to remove

        Returns:
            True if removed, False if not found
        """
        file = self.get_file(path)
        if file is None:
            return False

        # Normalize path
        path = path.lower().replace("/", os.sep).replace("\\", os.sep)

        # Find and remove entry
        block_offset = file.parent.offset
        while block_offset != 0:
            block = self._load_pack_file_block(block_offset)
            for i, entry in enumerate(block.entries):
                if entry.entry_type == PackFileEntryType.FILE and entry.offset == file.offset:
                    block.entries[i] = PackFileEntry.get_default()
                    self._update_pack_file_block(block_offset, block)
                    del file.parent.files[file.name]
                    del self._files[path]
                    return True
            block_offset = block.entries[-1].next_block

        return False

    # Private methods

    def _create_base_stream(self) -> None:
        """Create a new PK2 file structure."""
        # Write header
        header = PackFileHeader.get_default()
        checksum = self._blowfish.encode(b"Joymax Pack File")
        if checksum:
            header.checksum = checksum[:3] + bytes(13)
        self._file_stream.write(header.to_bytes())
        self._file_stream.flush()

        # Create root block
        offset = PackFileHeader.SIZE
        block = PackFileBlock.get_default()

        # Initialize root pointer
        now = datetime.now()
        block.entries[0].entry_type = PackFileEntryType.FOLDER
        block.entries[0].name = "."
        block.entries[0].creation_time = now
        block.entries[0].modification_time = now
        block.entries[0].offset = offset

        self._update_pack_file_block(offset, block)

        # Pad to 4096 bytes
        current_length = self._file_stream.seek(0, 2)
        if current_length < 4096:
            self._file_stream.write(bytes(4096 - current_length))
            self._file_stream.flush()

    def _initialize_stream_block(self, offset: int, parent: Pk2Folder) -> None:
        """Recursively load all blocks and build folder/file structure."""
        block = self._load_pack_file_block(offset)

        for entry in block.entries:
            if entry.entry_type == PackFileEntryType.EMPTY:
                continue
            elif entry.entry_type == PackFileEntryType.FOLDER:
                # Skip self and parent pointers
                if entry.name in (".", ".."):
                    continue
                # Create folder
                new_folder = Pk2Folder(entry.name, parent, entry.offset)
                parent.folders[entry.name.lower()] = new_folder
                self._folders[new_folder.get_full_path()] = new_folder
                self._disk_allocations[entry.offset] = PackFileBlock.SIZE
                self._initialize_stream_block(entry.offset, new_folder)
            elif entry.entry_type == PackFileEntryType.FILE:
                new_file = Pk2File(entry.name, parent, self._file_stream, entry.offset, entry.size)
                parent.files[entry.name.lower()] = new_file
                self._files[new_file.get_full_path()] = new_file
                self._disk_allocations[entry.offset] = entry.size

        # Check for next block in chain
        next_block = block.entries[-1].next_block
        if next_block != 0:
            self._initialize_stream_block(next_block, parent)
            self._disk_allocations[next_block] = PackFileBlock.SIZE

    def _load_pack_file_block(self, offset: int) -> PackFileBlock:
        """Load and decrypt a PackFileBlock from the given offset."""
        self._file_stream.seek(offset)
        data = self._file_stream.read(PackFileBlock.SIZE)
        decrypted = self._blowfish.decode(data)
        if decrypted is None:
            raise IOError("Failed to decrypt block")
        return PackFileBlock.from_bytes(decrypted)

    def _update_pack_file_block(self, offset: int, block: PackFileBlock) -> None:
        """Encrypt and write a PackFileBlock to the given offset."""
        data = block.to_bytes()
        encrypted = self._blowfish.encode(data)
        if encrypted is None:
            raise IOError("Failed to encrypt block")
        self._file_stream.seek(offset)
        self._file_stream.write(encrypted)
        self._file_stream.flush()

    def _expand_pack_file_block(self, offset: int, block: PackFileBlock) -> int:
        """Create a new block in the chain and link it."""
        new_block_offset = self._allocate_space(PackFileBlock.SIZE)

        # Create new block
        new_block = PackFileBlock.get_default()
        self._update_pack_file_block(new_block_offset, new_block)
        self._disk_allocations[new_block_offset] = PackFileBlock.SIZE

        # Link to chain
        block.entries[-1].next_block = new_block_offset
        self._update_pack_file_block(offset, block)

        return new_block_offset

    def _allocate_space(self, size: int) -> int:
        """
        Find or create space for data.
        First tries to find a gap between allocations, then appends to EOF.
        """
        offsets = sorted(self._disk_allocations.keys())

        for i, offset in enumerate(offsets):
            allocation_size = self._disk_allocations[offset]
            next_allocation = offset + allocation_size

            if next_allocation not in self._disk_allocations:
                # Check space between this and next allocation
                if i + 1 < len(offsets):
                    available = offsets[i + 1] - next_allocation
                else:
                    # Check space to end of file
                    self._file_stream.seek(0, 2)
                    available = self._file_stream.tell() - next_allocation

                if available >= size:
                    return next_allocation

        # Append to end of file, aligned to 4096
        aligned_size = math.ceil(size / 4096) * 4096
        self._file_stream.seek(0, 2)
        pos = self._file_stream.tell()
        self._file_stream.write(bytes(aligned_size))
        self._file_stream.flush()
        return pos

    def _create_folder_block(
        self,
        offset: int,
        parent_folder: Pk2Folder,
        paths: list[str],
    ) -> None:
        """Create folder blocks for the given path hierarchy."""
        block = self._load_pack_file_block(offset)

        for i, entry in enumerate(block.entries):
            if entry.entry_type == PackFileEntryType.EMPTY:
                new_folder = Pk2Folder(
                    paths[0],
                    parent_folder,
                    self._allocate_space(PackFileBlock.SIZE),
                )

                # Create new block for folder
                now = datetime.now()
                new_block = PackFileBlock.get_default()

                # Initialize . and .. entries
                new_block.entries[0].entry_type = PackFileEntryType.FOLDER
                new_block.entries[0].name = "."
                new_block.entries[0].creation_time = now
                new_block.entries[0].modification_time = now
                new_block.entries[0].offset = new_folder.offset

                new_block.entries[1].entry_type = PackFileEntryType.FOLDER
                new_block.entries[1].name = ".."
                new_block.entries[1].creation_time = now
                new_block.entries[1].modification_time = now
                new_block.entries[1].offset = parent_folder.offset

                self._update_pack_file_block(new_folder.offset, new_block)
                self._disk_allocations[new_folder.offset] = PackFileBlock.SIZE

                # Update parent entry
                block.entries[i].entry_type = PackFileEntryType.FOLDER
                block.entries[i].name = new_folder.name
                block.entries[i].creation_time = now
                block.entries[i].modification_time = now
                block.entries[i].offset = new_folder.offset
                self._update_pack_file_block(offset, block)

                parent_folder.folders[new_folder.name] = new_folder
                self._folders[new_folder.get_full_path()] = new_folder

                # Continue with remaining paths
                paths.pop(0)
                if paths:
                    self._create_folder_block(new_folder.offset, new_folder, paths)
                return

        # Need to expand block chain
        next_block = block.entries[-1].next_block
        if next_block == 0:
            offset = self._expand_pack_file_block(offset, block)
        else:
            offset = next_block
        self._create_folder_block(offset, parent_folder, paths)

    def _remove_folder_links(self, folder: Pk2Folder) -> None:
        """Recursively remove all links to a folder's contents."""
        # Remove subfolders
        for subfolder in list(folder.folders.values()):
            self._remove_folder_links(subfolder)

        # Remove files
        for file in list(folder.files.values()):
            full_path = file.get_full_path()
            if full_path in self._files:
                del self._files[full_path]
            if file.offset in self._disk_allocations:
                del self._disk_allocations[file.offset]

        # Remove folder itself
        full_path = folder.get_full_path()
        if full_path in self._folders:
            del self._folders[full_path]
        if folder.offset in self._disk_allocations:
            del self._disk_allocations[folder.offset]
