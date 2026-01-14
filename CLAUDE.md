# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PK2API is a pure-Python library for reading and writing Silkroad Online PK2 file archives (JMXPACK format). Zero external dependencies - uses only Python standard library.

## Development Commands

```bash
# Install for development
pip install -e .

# Build package
pip install build
python -m build

# Verify syntax
python3 -m py_compile pk2api/*.py pk2api/**/*.py

# CLI usage (after install)
pk2 list Media.pk2                    # List all files
pk2 list Media.pk2 -p "**/*.txt"      # List files matching glob
pk2 extract Media.pk2 -o ./output     # Extract all files
pk2 extract Media.pk2 -f data -o ./out # Extract specific folder
pk2 add Media.pk2 ./files -t target   # Import directory
pk2 info Media.pk2                    # Show archive stats
pk2 validate Media.pk2                # Check integrity

# Compare archives
pk2 compare old.pk2 new.pk2           # Text diff output
pk2 compare old.pk2 new.pk2 -f json   # JSON output for tools
pk2 cmp old.pk2 new.pk2 --quick       # Size-only comparison (faster)

# Copy between archives
pk2 copy src.pk2 dst.pk2 path/file.txt        # Copy single file
pk2 copy src.pk2 dst.pk2 "**/*.xml"           # Copy files matching glob
pk2 copy src.pk2 dst.pk2 data/folder -r       # Copy folder recursively
pk2 cp src.pk2 dst.pk2 folder -r -d backup    # Copy to different destination
```

No test framework is currently configured.

## Architecture

The library has a simple flat structure with clear separation of concerns:

```
pk2api/
├── pk2_stream.py      # Main public API - handles all I/O operations
├── pk2_file.py        # File wrapper with lazy content loading
├── pk2_folder.py      # Folder wrapper with path resolution
├── structures.py      # Binary format definitions (header, entry, block)
├── comparison.py      # Archive comparison (diff) functionality
├── cli.py             # Command-line interface
└── security/
    └── blowfish.py    # Silkroad-specific Blowfish cipher implementation
```

**Key classes:**
- `Pk2Stream`: Entry point. Opens archives in read-only or read-write mode. Uses context manager pattern.
- `PackFileHeader/Entry/Block` in structures.py: Binary format structs (256/128/2560 bytes respectively)
- `Blowfish`: Custom cipher with Silkroad Online key derivation (XOR with base key)

**Data flow:**
1. `Pk2Stream` opens file, validates header via Blowfish checksum
2. Reads block chain, builds in-memory folder/file dictionaries for O(1) lookups
3. `Pk2File.get_content()` lazy-loads actual file data on demand
4. Write operations allocate space with 4096-byte alignment

## Key Technical Details

- Block structure: 20 entries per 2560-byte block, chained via next_block pointer
- Encryption: Blowfish ECB mode, 8-byte blocks, hardcoded base key for key derivation
- Paths normalized to lowercase internally, but `original_name` preserves case from archive
- Windows FILETIME format for timestamps

## API Methods

**Pk2Stream** core methods:
- `get_file(path)`, `get_folder(path)` - O(1) lookups
- `add_file(path, data)`, `add_folder(path)` - Create entries
- `remove_file(path)`, `remove_folder(path)` - Delete entries

**Pk2Stream** utility methods:
- `iter_files()`, `iter_folders()` - Iterate all entries
- `glob(pattern)` - Find files by glob pattern
- `get_stats()` - Return file/folder counts and sizes
- `extract_all(output_dir, progress=None)` - Extract entire archive
- `extract_folder(folder_path, output_dir, progress=None)` - Extract subfolder
- `import_from_disk(source_dir, target_path="", progress=None)` - Bulk import
- `validate()` - Check archive integrity

**Pk2Stream** copy methods (inter-archive operations):
- `copy_file_from(source, source_path, target_path=None)` - Copy single file from another archive
- `copy_folder_from(source, source_path, target_path=None, progress=None)` - Copy folder recursively
- `copy_files_from(source, paths, target_base="", progress=None)` - Copy multiple files

**Comparison** (in comparison.py):
- `compare_archives(source, target, compute_hashes=True, ...)` - Compare two archives
- `ComparisonResult` - Result object with `added_files`, `removed_files`, `modified_files` properties
- `FileChange`, `FolderChange` - Change detail dataclasses
- `ChangeType` - Enum: ADDED, REMOVED, MODIFIED, UNCHANGED

**Pk2File/Pk2Folder** properties:
- `original_name` - Case-preserved name from archive
- `get_original_path()` - Full path with original case
