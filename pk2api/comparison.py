"""
Archive comparison functionality for pk2api.

Provides data structures and functions for comparing two PK2 archives,
detecting added, removed, and modified files.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .pk2_stream import Pk2Stream


class ChangeType(Enum):
    """Type of change detected between archives."""

    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    UNCHANGED = "unchanged"


@dataclass
class FileChange:
    """Represents a file difference between two archives."""

    path: str
    original_path: str
    change_type: ChangeType
    source_size: int | None
    target_size: int | None
    source_hash: str | None = None
    target_hash: str | None = None


@dataclass
class FolderChange:
    """Represents a folder difference between two archives."""

    path: str
    original_path: str
    change_type: ChangeType


@dataclass
class ComparisonResult:
    """Complete comparison result between two archives."""

    source_path: str
    target_path: str
    file_changes: list[FileChange] = field(default_factory=list)
    folder_changes: list[FolderChange] = field(default_factory=list)

    @property
    def added_files(self) -> list[FileChange]:
        """Files present in target but not in source."""
        return [f for f in self.file_changes if f.change_type == ChangeType.ADDED]

    @property
    def removed_files(self) -> list[FileChange]:
        """Files present in source but not in target."""
        return [f for f in self.file_changes if f.change_type == ChangeType.REMOVED]

    @property
    def modified_files(self) -> list[FileChange]:
        """Files with different content between archives."""
        return [f for f in self.file_changes if f.change_type == ChangeType.MODIFIED]

    @property
    def unchanged_files(self) -> list[FileChange]:
        """Files identical in both archives."""
        return [f for f in self.file_changes if f.change_type == ChangeType.UNCHANGED]

    @property
    def added_folders(self) -> list[FolderChange]:
        """Folders present in target but not in source."""
        return [f for f in self.folder_changes if f.change_type == ChangeType.ADDED]

    @property
    def removed_folders(self) -> list[FolderChange]:
        """Folders present in source but not in target."""
        return [f for f in self.folder_changes if f.change_type == ChangeType.REMOVED]

    @property
    def has_differences(self) -> bool:
        """True if any differences were detected."""
        return (
            any(f.change_type != ChangeType.UNCHANGED for f in self.file_changes)
            or bool(self.folder_changes)
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "source": self.source_path,
            "target": self.target_path,
            "summary": {
                "added": len(self.added_files),
                "removed": len(self.removed_files),
                "modified": len(self.modified_files),
                "unchanged": len(self.unchanged_files),
                "folders_added": len(self.added_folders),
                "folders_removed": len(self.removed_folders),
            },
            "files": [
                {
                    "path": f.original_path,
                    "change": f.change_type.value,
                    "source_size": f.source_size,
                    "target_size": f.target_size,
                    "source_hash": f.source_hash,
                    "target_hash": f.target_hash,
                }
                for f in self.file_changes
                if f.change_type != ChangeType.UNCHANGED
            ],
            "folders": [
                {"path": f.original_path, "change": f.change_type.value}
                for f in self.folder_changes
            ],
        }


# Callback signature: (current_file, current_index, total)
ComparisonCallback = Callable[[str, int, int], None]


def compute_file_hash(content: bytes, algorithm: str = "md5") -> str:
    """
    Compute hash of file content.

    Args:
        content: File content bytes
        algorithm: Hash algorithm ("md5" or "sha256")

    Returns:
        Hex digest of the hash

    Raises:
        ValueError: If algorithm is not supported
    """
    if algorithm == "md5":
        return hashlib.md5(content).hexdigest()
    elif algorithm == "sha256":
        return hashlib.sha256(content).hexdigest()
    else:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}")


def compare_archives(
    source: Pk2Stream,
    target: Pk2Stream,
    compute_hashes: bool = True,
    hash_algorithm: str = "md5",
    include_unchanged: bool = False,
    progress: ComparisonCallback | None = None,
) -> ComparisonResult:
    """
    Compare two PK2 archives.

    Args:
        source: Source archive (the "old" or reference archive)
        target: Target archive (the "new" archive to compare against)
        compute_hashes: If True, compute content hashes for modification detection
        hash_algorithm: Hash algorithm to use ("md5" or "sha256")
        include_unchanged: If True, include unchanged files in results
        progress: Optional callback(current_file, current, total) for progress

    Returns:
        ComparisonResult with all detected differences
    """
    result = ComparisonResult(
        source_path=str(source._file_stream.name),
        target_path=str(target._file_stream.name),
    )

    # Build dictionaries for O(1) lookup
    source_files = {f.get_full_path(): f for f in source.iter_files()}
    target_files = {f.get_full_path(): f for f in target.iter_files()}

    source_folders = {f.get_full_path(): f for f in source.iter_folders()}
    target_folders = {f.get_full_path(): f for f in target.iter_folders()}

    # Compare files
    all_file_paths = set(source_files.keys()) | set(target_files.keys())
    total = len(all_file_paths)

    for i, path in enumerate(sorted(all_file_paths)):
        if progress:
            progress(path, i, total)

        source_file = source_files.get(path)
        target_file = target_files.get(path)

        if source_file and not target_file:
            # File removed in target
            result.file_changes.append(
                FileChange(
                    path=path,
                    original_path=source_file.get_original_path(),
                    change_type=ChangeType.REMOVED,
                    source_size=source_file.size,
                    target_size=None,
                )
            )
        elif target_file and not source_file:
            # File added in target
            result.file_changes.append(
                FileChange(
                    path=path,
                    original_path=target_file.get_original_path(),
                    change_type=ChangeType.ADDED,
                    source_size=None,
                    target_size=target_file.size,
                )
            )
        else:
            # File exists in both - check for modifications
            source_hash = None
            target_hash = None
            is_modified = False

            # Quick check: size difference means modified
            if source_file.size != target_file.size:
                is_modified = True
            elif compute_hashes:
                # Same size - compare content hashes
                source_hash = compute_file_hash(
                    source_file.get_content(), hash_algorithm
                )
                target_hash = compute_file_hash(
                    target_file.get_content(), hash_algorithm
                )
                is_modified = source_hash != target_hash

            change_type = ChangeType.MODIFIED if is_modified else ChangeType.UNCHANGED

            if include_unchanged or is_modified:
                result.file_changes.append(
                    FileChange(
                        path=path,
                        original_path=target_file.get_original_path(),
                        change_type=change_type,
                        source_size=source_file.size,
                        target_size=target_file.size,
                        source_hash=source_hash,
                        target_hash=target_hash,
                    )
                )

    if progress:
        progress("", total, total)

    # Compare folders (structural only - added/removed)
    all_folder_paths = set(source_folders.keys()) | set(target_folders.keys())

    for path in sorted(all_folder_paths):
        if not path:  # Skip root folder
            continue

        source_folder = source_folders.get(path)
        target_folder = target_folders.get(path)

        if source_folder and not target_folder:
            result.folder_changes.append(
                FolderChange(
                    path=path,
                    original_path=source_folder.get_original_path(),
                    change_type=ChangeType.REMOVED,
                )
            )
        elif target_folder and not source_folder:
            result.folder_changes.append(
                FolderChange(
                    path=path,
                    original_path=target_folder.get_original_path(),
                    change_type=ChangeType.ADDED,
                )
            )

    return result
