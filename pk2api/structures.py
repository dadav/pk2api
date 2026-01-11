"""
Binary structures for PK2 file format (JMXPACK).
Based on: https://github.com/DummkopfOfHachtenduden/SilkroadDoc/wiki/JMXPACK
"""
import struct
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import ClassVar


class PackFileEntryType(IntEnum):
    """Entry type in a PK2 file block."""
    EMPTY = 0
    FOLDER = 1
    FILE = 2


# Windows FILETIME epoch (January 1, 1601) to Unix epoch difference in seconds
FILETIME_UNIX_DIFF = 116444736000000000


def filetime_to_datetime(filetime: int) -> datetime:
    """Convert Windows FILETIME to Python datetime."""
    if filetime <= 0:
        return datetime(1601, 1, 1)
    try:
        timestamp = (filetime - FILETIME_UNIX_DIFF) / 10000000
        return datetime.fromtimestamp(timestamp)
    except (OSError, OverflowError, ValueError):
        return datetime(1601, 1, 1)


def datetime_to_filetime(dt: datetime) -> int:
    """Convert Python datetime to Windows FILETIME."""
    try:
        timestamp = dt.timestamp()
        return int(timestamp * 10000000 + FILETIME_UNIX_DIFF)
    except (OSError, OverflowError, ValueError):
        return 0


@dataclass
class PackFileHeader:
    """
    PK2 file header structure (256 bytes).

    Fields:
        signature: "JoyMax File Manager!\\n" (30 bytes)
        version: File format version (4 bytes)
        is_encrypted: Whether the file is encrypted (1 byte)
        checksum: Used to validate the blowfish key (16 bytes)
        reserved: Reserved space (205 bytes)
    """
    SIZE: ClassVar[int] = 256

    signature: str = "JoyMax File Manager!\n"
    version: bytes = field(default_factory=lambda: bytes([2, 0, 0, 1]))
    is_encrypted: bool = True
    checksum: bytes = field(default_factory=lambda: bytes(16))
    reserved: bytes = field(default_factory=lambda: bytes(205))

    def to_bytes(self) -> bytes:
        """Serialize header to bytes."""
        sig_bytes = self.signature.encode("ascii").ljust(30, b"\x00")[:30]
        version_bytes = self.version[:4].ljust(4, b"\x00")
        encrypted_byte = b"\x01" if self.is_encrypted else b"\x00"
        checksum_bytes = self.checksum[:16].ljust(16, b"\x00")
        reserved_bytes = self.reserved[:205].ljust(205, b"\x00")
        return sig_bytes + version_bytes + encrypted_byte + checksum_bytes + reserved_bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> "PackFileHeader":
        """Deserialize header from bytes."""
        signature = data[:30].rstrip(b"\x00").decode("ascii", errors="replace")
        version = data[30:34]
        is_encrypted = data[34] != 0
        checksum = data[35:51]
        reserved = data[51:256]
        return cls(signature, version, is_encrypted, checksum, reserved)

    @classmethod
    def get_default(cls) -> "PackFileHeader":
        """Create a header with default values."""
        return cls()


@dataclass
class PackFileEntry:
    """
    PK2 file entry structure (128 bytes).

    Fields:
        type: Entry type (empty, folder, or file)
        name: Entry name (89 bytes)
        creation_time: Creation time as Windows FILETIME
        modification_time: Modification time as Windows FILETIME
        offset: Offset to block (folder) or file data
        size: File size (0 for folders)
        next_block: Offset to next block in chain
        padding: Padding bytes (2 bytes)
    """
    SIZE: ClassVar[int] = 128

    entry_type: PackFileEntryType = PackFileEntryType.EMPTY
    name: str = ""
    creation_time: datetime = field(default_factory=lambda: datetime(1601, 1, 1))
    modification_time: datetime = field(default_factory=lambda: datetime(1601, 1, 1))
    offset: int = 0
    size: int = 0
    next_block: int = 0
    padding: bytes = field(default_factory=lambda: bytes(2))

    def to_bytes(self) -> bytes:
        """Serialize entry to bytes."""
        result = bytearray(128)
        result[0] = int(self.entry_type)
        name_bytes = self.name.encode("ascii", errors="replace")[:88]
        result[1:1 + len(name_bytes)] = name_bytes
        struct.pack_into("<q", result, 90, datetime_to_filetime(self.creation_time))
        struct.pack_into("<q", result, 98, datetime_to_filetime(self.modification_time))
        struct.pack_into("<q", result, 106, self.offset)
        struct.pack_into("<I", result, 114, self.size)
        struct.pack_into("<q", result, 118, self.next_block)
        result[126:128] = self.padding[:2]
        return bytes(result)

    @classmethod
    def from_bytes(cls, data: bytes) -> "PackFileEntry":
        """Deserialize entry from bytes."""
        entry_type = PackFileEntryType(data[0])
        # Name is null-terminated C string - find the first null byte
        name_bytes = data[1:90]
        null_pos = name_bytes.find(b"\x00")
        if null_pos != -1:
            name_bytes = name_bytes[:null_pos]
        name = name_bytes.decode("ascii", errors="replace")
        creation_time = filetime_to_datetime(struct.unpack_from("<q", data, 90)[0])
        modification_time = filetime_to_datetime(struct.unpack_from("<q", data, 98)[0])
        offset = struct.unpack_from("<q", data, 106)[0]
        size = struct.unpack_from("<I", data, 114)[0]
        next_block = struct.unpack_from("<q", data, 118)[0]
        padding = data[126:128]
        return cls(entry_type, name, creation_time, modification_time, offset, size, next_block, padding)

    @classmethod
    def get_default(cls) -> "PackFileEntry":
        """Create an entry with default values."""
        return cls()


@dataclass
class PackFileBlock:
    """
    PK2 file block structure (2560 bytes).
    Contains 20 entries.
    """
    SIZE: ClassVar[int] = 2560
    ENTRY_COUNT: ClassVar[int] = 20

    entries: list[PackFileEntry] = field(default_factory=list)

    def __post_init__(self):
        """Ensure we always have exactly 20 entries."""
        while len(self.entries) < self.ENTRY_COUNT:
            self.entries.append(PackFileEntry.get_default())
        self.entries = self.entries[:self.ENTRY_COUNT]

    def to_bytes(self) -> bytes:
        """Serialize block to bytes."""
        result = bytearray(self.SIZE)
        for i, entry in enumerate(self.entries):
            offset = i * PackFileEntry.SIZE
            result[offset:offset + PackFileEntry.SIZE] = entry.to_bytes()
        return bytes(result)

    @classmethod
    def from_bytes(cls, data: bytes) -> "PackFileBlock":
        """Deserialize block from bytes."""
        entries = []
        for i in range(cls.ENTRY_COUNT):
            offset = i * PackFileEntry.SIZE
            entries.append(PackFileEntry.from_bytes(data[offset:offset + PackFileEntry.SIZE]))
        return cls(entries)

    @classmethod
    def get_default(cls) -> "PackFileBlock":
        """Create a block with default entries."""
        return cls([PackFileEntry.get_default() for _ in range(cls.ENTRY_COUNT)])
