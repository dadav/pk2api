"""
Pk2Stream - Main class for reading and writing PK2 archives.
"""
from __future__ import annotations

import fnmatch
import math
import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Callable

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


ProgressCallback = Callable[[int, int], None]  # (current, total)
OpenProgressCallback = Callable[[int, int], None]  # (blocks_loaded, estimated_total)


class Pk2Stream:
    """
    PK2 file stream handler.

    Provides read/write access to Silkroad Online PK2 archives.
    Supports creating new archives, adding/removing files and folders.
    """

    def __init__(
        self,
        path: str | Path,
        key: str,
        read_only: bool = False,
        progress: OpenProgressCallback | None = None,
    ):
        """
        Initialize a PK2 stream.

        Args:
            path: Path to the PK2 file
            key: Blowfish encryption key
            read_only: If True, open in read-only mode. Otherwise, create if doesn't exist.
            progress: Optional callback(blocks_loaded, estimated_total) for progress updates

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

            # Set up progress tracking
            progress_state = None
            if progress:
                # Estimate total blocks from file size
                self._file_stream.seek(0, 2)
                file_size = self._file_stream.tell()
                estimated_blocks = max(1, file_size // PackFileBlock.SIZE)
                progress_state = {
                    "loaded": 0,
                    "total": estimated_blocks,
                    "callback": progress,
                }

            self._initialize_stream_block(root.offset, root, progress_state)

            # Final progress callback
            if progress_state:
                progress_state["callback"](
                    progress_state["loaded"], progress_state["loaded"]
                )
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
                            original_name=existing_file.original_name,
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

    def iter_files(self) -> Iterator[Pk2File]:
        """Iterate over all files in the archive."""
        yield from self._files.values()

    def iter_folders(self) -> Iterator[Pk2Folder]:
        """Iterate over all folders in the archive."""
        yield from self._folders.values()

    def glob(self, pattern: str) -> list[Pk2File]:
        """
        Find files matching a glob pattern.

        Args:
            pattern: Glob pattern (e.g., '**/*.txt', 'data/*.xml')

        Returns:
            List of matching Pk2File objects
        """
        pattern = pattern.lower().replace("/", os.sep).replace("\\", os.sep)
        return [
            f for f in self._files.values() if fnmatch.fnmatch(f.get_full_path(), pattern)
        ]

    def get_stats(self) -> dict:
        """
        Get archive statistics.

        Returns:
            Dictionary with keys: files, folders, total_size, disk_used
        """
        return {
            "files": len(self._files),
            "folders": len(self._folders),
            "total_size": sum(f.size for f in self._files.values()),
            "disk_used": sum(self._disk_allocations.values()),
        }

    def extract_all(
        self, output_dir: str | Path, progress: ProgressCallback | None = None
    ) -> int:
        """
        Extract all files to output directory.

        Args:
            output_dir: Destination directory
            progress: Optional callback(current, total) for progress updates

        Returns:
            Number of files extracted
        """
        output_path = Path(output_dir)
        files = list(self._files.values())
        total = len(files)

        for i, file in enumerate(files):
            if progress:
                progress(i, total)

            rel_path = file.get_original_path()
            dest = output_path / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(file.get_content())

        if progress:
            progress(total, total)
        return total

    def extract_folder(
        self,
        folder_path: str,
        output_dir: str | Path,
        progress: ProgressCallback | None = None,
    ) -> int:
        """
        Extract a specific folder to output directory.

        Args:
            folder_path: Path of folder to extract
            output_dir: Destination directory
            progress: Optional callback(current, total) for progress updates

        Returns:
            Number of files extracted

        Raises:
            ValueError: If folder not found
        """
        folder = self.get_folder(folder_path)
        if folder is None:
            raise ValueError(f"Folder not found: {folder_path}")

        output_path = Path(output_dir)
        prefix = folder.get_full_path()
        prefix_len = len(prefix) + 1 if prefix else 0  # +1 for separator

        files = [
            f
            for f in self._files.values()
            if f.get_full_path() == prefix or f.get_full_path().startswith(prefix + os.sep)
        ]
        total = len(files)

        for i, file in enumerate(files):
            if progress:
                progress(i, total)

            # Get path relative to extracted folder
            original_path = file.get_original_path()
            rel_path = original_path[prefix_len:] if prefix_len else original_path
            if not rel_path:
                rel_path = file.original_name

            dest = output_path / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(file.get_content())

        if progress:
            progress(total, total)
        return total

    def import_from_disk(
        self,
        source_dir: str | Path,
        target_path: str = "",
        progress: ProgressCallback | None = None,
    ) -> int:
        """
        Import a directory tree into the archive.

        Args:
            source_dir: Source directory to import
            target_path: Target path in archive (empty for root)
            progress: Optional callback(current, total) for progress updates

        Returns:
            Number of files imported

        Raises:
            ValueError: If source is not a directory
        """
        source = Path(source_dir)
        if not source.is_dir():
            raise ValueError(f"Not a directory: {source_dir}")

        files = [f for f in source.rglob("*") if f.is_file()]
        total = len(files)

        for i, file in enumerate(files):
            if progress:
                progress(i, total)

            rel_path = file.relative_to(source)
            archive_path = (
                os.path.join(target_path, str(rel_path)) if target_path else str(rel_path)
            )
            self.add_file(archive_path, file.read_bytes())

        if progress:
            progress(total, total)
        return total

    def validate(self) -> list[str]:
        """
        Validate archive integrity.

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        # Check all files are readable
        for path, file in self._files.items():
            try:
                content = file.get_content()
                if len(content) != file.size:
                    errors.append(
                        f"Size mismatch: {path} (expected {file.size}, got {len(content)})"
                    )
            except Exception as e:
                errors.append(f"Read error: {path} - {e}")

        # Check folder structure consistency
        for path, folder in self._folders.items():
            if folder.parent and folder not in folder.parent.folders.values():
                errors.append(f"Orphan folder: {path}")

        return errors

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

    def _initialize_stream_block(
        self, offset: int, parent: Pk2Folder, progress_state: dict | None = None
    ) -> None:
        """Recursively load all blocks and build folder/file structure."""
        block = self._load_pack_file_block(offset)

        # Report progress after loading block
        if progress_state:
            progress_state["loaded"] += 1
            progress_state["callback"](
                progress_state["loaded"], progress_state["total"]
            )

        for entry in block.entries:
            if entry.entry_type == PackFileEntryType.EMPTY:
                continue
            elif entry.entry_type == PackFileEntryType.FOLDER:
                # Skip self and parent pointers
                if entry.name in (".", ".."):
                    continue
                # Create folder
                new_folder = Pk2Folder(
                    entry.name.lower(), parent, entry.offset, original_name=entry.name
                )
                parent.folders[entry.name.lower()] = new_folder
                self._folders[new_folder.get_full_path()] = new_folder
                self._disk_allocations[entry.offset] = PackFileBlock.SIZE
                self._initialize_stream_block(entry.offset, new_folder, progress_state)
            elif entry.entry_type == PackFileEntryType.FILE:
                new_file = Pk2File(
                    entry.name.lower(),
                    parent,
                    self._file_stream,
                    entry.offset,
                    entry.size,
                    original_name=entry.name,
                )
                parent.files[entry.name.lower()] = new_file
                self._files[new_file.get_full_path()] = new_file
                self._disk_allocations[entry.offset] = entry.size

        # Check for next block in chain
        next_block = block.entries[-1].next_block
        if next_block != 0:
            self._disk_allocations[next_block] = PackFileBlock.SIZE
            self._initialize_stream_block(next_block, parent, progress_state)

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

    # Copy methods for inter-archive operations

    def copy_file_from(
        self,
        source: Pk2Stream,
        source_path: str,
        target_path: str | None = None,
    ) -> bool:
        """
        Copy a single file from another archive.

        Args:
            source: Source archive to copy from
            source_path: Path of file in source archive
            target_path: Destination path (defaults to same as source)

        Returns:
            True if successful, False if source file not found
        """
        source_file = source.get_file(source_path)
        if source_file is None:
            return False

        dest_path = target_path if target_path else source_file.get_original_path()
        content = source_file.get_content()
        return self.add_file(dest_path, content)

    def copy_folder_from(
        self,
        source: Pk2Stream,
        source_path: str,
        target_path: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> int:
        """
        Copy a folder and all its contents from another archive.

        Args:
            source: Source archive to copy from
            source_path: Path of folder in source archive
            target_path: Destination path (defaults to same as source)
            progress: Optional callback(current, total) for progress

        Returns:
            Number of files copied

        Raises:
            ValueError: If source folder not found
        """
        source_folder = source.get_folder(source_path)
        if source_folder is None:
            raise ValueError(f"Folder not found: {source_path}")

        # Get all files under the source folder
        prefix = source_folder.get_full_path()
        prefix_len = len(prefix) + 1 if prefix else 0

        files_to_copy = [
            f
            for f in source.iter_files()
            if f.get_full_path() == prefix
            or f.get_full_path().startswith(prefix + os.sep)
        ]

        total = len(files_to_copy)
        dest_base = target_path if target_path is not None else prefix

        for i, file in enumerate(files_to_copy):
            if progress:
                progress(i, total)

            # Compute relative path within source folder
            rel_path = (
                file.get_original_path()[prefix_len:] if prefix_len else file.get_original_path()
            )
            if not rel_path:
                rel_path = file.original_name

            # Compute destination path
            dest_path = os.path.join(dest_base, rel_path) if dest_base else rel_path
            self.add_file(dest_path, file.get_content())

        if progress:
            progress(total, total)

        return total

    def copy_files_from(
        self,
        source: Pk2Stream,
        paths: list[str],
        target_base: str = "",
        progress: ProgressCallback | None = None,
    ) -> int:
        """
        Copy multiple files from another archive.

        Args:
            source: Source archive to copy from
            paths: List of file paths to copy
            target_base: Base path for destination (prepended to each file's name)
            progress: Optional callback(current, total) for progress

        Returns:
            Number of files successfully copied
        """
        total = len(paths)
        copied = 0

        for i, path in enumerate(paths):
            if progress:
                progress(i, total)

            source_file = source.get_file(path)
            if source_file:
                dest_path = (
                    os.path.join(target_base, source_file.original_name)
                    if target_base
                    else source_file.get_original_path()
                )
                if self.add_file(dest_path, source_file.get_content()):
                    copied += 1

        if progress:
            progress(total, total)

        return copied
