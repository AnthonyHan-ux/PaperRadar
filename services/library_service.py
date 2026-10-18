"""Local library layout and file naming.

    library/
        2026/
            ISPRS_JPRS/
                2026_Zhang_Optical_SAR_Change_Detection.pdf
            arXiv/
        2025/
        unknown/

The year/venue directory structure is deliberate: it keeps folders browsable in
Explorer, and it bounds the path length (a flat folder with 5000 PDFs is both
slow and unreadable).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from models.paper import Paper
from utils.config import Settings
from utils.filename import (
    build_pdf_filename,
    long_path,
    make_unique_path,
    shorten_for_windows,
)
from utils.venues import venue_slug

log = logging.getLogger(__name__)


class LibraryService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.paths.library_dir

    # ------------------------------------------------------------------ #
    def venue_dir(self, paper: Paper) -> Path:
        year = str(paper.year) if paper.year else "unknown"
        return self.root / year / venue_slug(paper.venue)

    def destination_for(self, paper: Paper, prefix: Optional[str] = None) -> Path:
        """Absolute path (not yet guaranteed unique) for the paper's PDF."""
        directory = self.venue_dir(paper)
        filename = build_pdf_filename(
            paper.year,
            paper.authors,
            paper.title,
            prefix=prefix,
        )
        return shorten_for_windows(directory / filename)

    def reserve_destination(self, paper: Paper, prefix: Optional[str] = None) -> Path:
        """Create the directory and return an unused path."""
        path = self.destination_for(paper, prefix=prefix)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("cannot create library directory %s: %s", path.parent, exc)
        return make_unique_path(path)

    # ------------------------------------------------------------------ #
    def relative_display(self, path: Optional[str]) -> str:
        if not path:
            return ""
        try:
            return str(Path(path).relative_to(self.root))
        except (ValueError, OSError):
            return str(path)

    def file_exists(self, path: Optional[str]) -> bool:
        if not path:
            return False
        try:
            return Path(path).is_file() and Path(path).stat().st_size > 0
        except OSError:
            return False

    def open_in_explorer(self, path: Optional[str]) -> bool:
        """Reveal a file in Explorer (or open the folder when path is a dir)."""
        if not path:
            return False
        target = Path(path)
        try:
            if target.is_file():
                os.startfile(str(target.parent))  # noqa: S606 - intentional shell open
                return True
            if target.is_dir():
                os.startfile(str(target))
                return True
        except OSError as exc:
            log.warning("cannot open %s: %s", target, exc)
        return False

    def open_file(self, path: Optional[str]) -> bool:
        if not path or not Path(path).exists():
            return False
        try:
            os.startfile(str(path))  # noqa: S606
            return True
        except OSError as exc:
            log.warning("cannot open %s: %s", path, exc)
            return False

    def disk_usage(self) -> tuple[int, int]:
        """(file_count, total_bytes) of the local library."""
        count = 0
        total = 0
        if not self.root.exists():
            return 0, 0
        for dirpath, _dirnames, filenames in os.walk(long_path(self.root)):
            for name in filenames:
                if not name.lower().endswith(".pdf"):
                    continue
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                    count += 1
                except OSError:
                    continue
        return count, total
