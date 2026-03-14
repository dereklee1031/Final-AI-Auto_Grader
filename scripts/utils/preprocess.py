"""
Pre-processing utility: deduplicates source files before the pipeline runs.

Deduplication rule: a file whose stem ends with _N (e.g. "report_1.pdf")
is skipped when its original ("report.pdf") exists in the same directory.

All supported file types (including .docx and .pptx) are copied as-is;
the pipeline's native extractors handle each format directly.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from core.config import SUPPORTED_EXTENSIONS

# Directories that should never be staged (mirrors pipeline.py EXCLUDED_DIRS)
DEFAULT_EXCLUDED_DIRS: set[str] = {"Scores", "Supplemental_Material"}


def is_duplicate(file_path: Path) -> bool:
    """Return True if this file is a _N copy and its original exists alongside it."""
    m = re.search(r"^(.*?)_(\d+)$", file_path.stem)
    if not m:
        return False
    original = file_path.with_name(m.group(1) + file_path.suffix)
    return original.exists()


def has_duplicates(data_dir: Path, excluded_dirs: set[str] | None = None) -> bool:
    """Return True if data_dir contains any duplicate (_N) files."""
    if excluded_dirs is None:
        excluded_dirs = DEFAULT_EXCLUDED_DIRS
    for p in data_dir.rglob("*"):
        if not p.is_file():
            continue
        if any(part in excluded_dirs for part in p.parts):
            continue
        if p.suffix.lower() in SUPPORTED_EXTENSIONS and is_duplicate(p):
            return True
    return False


def build_staging_dir(
    data_dir: Path,
    staging_dir: Path,
    excluded_dirs: set[str] | None = None,
) -> Path:
    """
    Build a staging directory that mirrors data_dir with duplicates removed.

    Copies all supported files, skipping:
    - Hidden files
    - Excluded directories
    - _N duplicate files (where the original also exists)

    Returns staging_dir (the new effective data_dir for the pipeline).
    """
    if excluded_dirs is None:
        excluded_dirs = DEFAULT_EXCLUDED_DIRS

    staging_dir.mkdir(parents=True, exist_ok=True)
    counts = {"copied": 0, "dupes": 0, "skipped": 0}

    for src in sorted(data_dir.rglob("*")):
        if not src.is_file():
            continue
        if src.name.startswith("."):
            continue
        if any(part in excluded_dirs for part in src.parts):
            counts["skipped"] += 1
            continue
        if src.suffix.lower() not in SUPPORTED_EXTENSIONS:
            counts["skipped"] += 1
            continue
        if is_duplicate(src):
            counts["dupes"] += 1
            continue

        dest = staging_dir / src.relative_to(data_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        counts["copied"] += 1

    print(
        f"[preprocess] Staging complete → {staging_dir}\n"
        f"  copied={counts['copied']}  dupes_skipped={counts['dupes']}  "
        f"unsupported_skipped={counts['skipped']}"
    )
    return staging_dir
