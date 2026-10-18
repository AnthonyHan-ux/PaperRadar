"""Windows-safe file naming helpers.

Academic titles are hostile input: they contain ``:``, ``?``, ``/``, emoji,
200+ character subtitles and trailing dots.  Everything that ends up on disk
goes through :func:`sanitize_component` / :func:`build_pdf_filename` so that no
other module has to think about it.
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Iterable, Optional

from utils.title_normalizer import normalize_text

# Characters that are illegal in a Windows file name, plus control chars.
_ILLEGAL_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Reserved device names (case-insensitive, with or without extension).
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_SPACES_RE = re.compile(r"\s+")
_UNDERSCORE_RUN_RE = re.compile(r"[_\-]{2,}")

# Conservative per-component budget: leaves room for the directory prefix so we
# stay under MAX_PATH even without long-path support enabled.
MAX_COMPONENT = 120
MAX_PATH = 240


def sanitize_component(
    value: Optional[str],
    max_len: int = MAX_COMPONENT,
    fallback: str = "untitled",
    allow_unicode: bool = True,
) -> str:
    """Return a safe single path component (no separators, no trailing dots)."""
    if value is None:
        value = ""
    text = unicodedata.normalize("NFC", str(value))
    if not allow_unicode:
        text = text.encode("ascii", "ignore").decode("ascii")
    text = _ILLEGAL_RE.sub("", text)
    text = text.replace("\u00a0", " ")
    text = _SPACES_RE.sub(" ", text).strip()
    # Windows silently strips trailing dots/spaces -> strip them ourselves.
    text = text.rstrip(" .")
    if not text:
        text = fallback
    if text.lower() in _RESERVED:
        text = f"_{text}"
    if len(text) > max_len:
        text = text[:max_len].rstrip(" ._")
    return text or fallback


def first_author_surname(author: Optional[str]) -> str:
    """Best-effort surname extraction for file names.

    Handles ``"Zhang, Wei"``, ``"Wei Zhang"`` and ``"W. Zhang"``.
    """
    if not author:
        return ""
    name = author.strip()
    if not name:
        return ""
    if "," in name:
        last = name.split(",", 1)[0].strip()
    else:
        parts = name.split()
        last = parts[-1] if parts else ""
        # Degenerate single-token or "Last, First" leftovers
        if len(last) <= 2 and len(parts) > 1:
            last = parts[-2]
    last = _ILLEGAL_RE.sub("", last)
    return sanitize_component(last, max_len=28, fallback="")


def words_for_filename(title: str, max_words: int = 9, max_chars: int = 70) -> str:
    """Short, readable title slug (``Beyond_Attention_Conv_Global_Context``)."""
    clean = normalize_text(title)
    if not clean:
        return "paper"
    stop = {"a", "an", "the", "of", "for", "and", "on", "in", "to", "with", "by",
            "from", "at", "as", "is", "are", "via", "using"}
    words: list[str] = []
    for word in clean.split():
        if word in stop:
            continue
        words.append(word)
        if len(words) >= max_words:
            break
    if not words:
        words = clean.split()[:max_words]
    slug = "_".join(words)
    if len(slug) > max_chars:
        slug = slug[:max_chars].rstrip("_")
    return slug or "paper"


def build_pdf_filename(
    year: Optional[int],
    authors: Iterable[str],
    title: Optional[str],
    prefix: Optional[str] = None,
    max_len: int = 110,
    extension: str = ".pdf",
) -> str:
    """``2026_Zhang_Beyond_Attention_Conv_Global_Context.pdf``."""
    author_list = [a for a in authors if a]
    surname = first_author_surname(author_list[0]) if author_list else "anon"
    parts = [
        prefix or "",
        str(year) if year else "nd",
        surname or "anon",
        words_for_filename(title or ""),
    ]
    stem = "_".join(p for p in parts if p)
    stem = _UNDERSCORE_RUN_RE.sub("_", stem).strip("_")
    budget = max_len - len(extension)
    if len(stem) > budget:
        stem = stem[:budget].rstrip("_")
    return sanitize_component(stem, max_len=budget, fallback="paper") + extension


def make_unique_path(path: Path, max_attempts: int = 999) -> Path:
    """``foo.pdf`` -> ``foo_2.pdf`` when the target already exists."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(2, max_attempts + 1):
        candidate = path.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Cannot find a free filename for {path}")


def shorten_for_windows(path: Path, max_path: int = MAX_PATH) -> Path:
    """Truncate the file-name component if the full path exceeds ``max_path``.

    The directory part is never modified (it is derived from year/venue and is
    always short), only the stem is shortened.
    """
    full = str(path)
    if len(full) <= max_path:
        return path
    overflow = len(full) - max_path
    stem = path.stem
    if len(stem) <= overflow + 8:
        # Nothing sensible left to trim: fall back to a hash-ish short name.
        stem = stem[:8]
    new_stem = stem[: max(8, len(stem) - overflow)].rstrip("_")
    return path.with_name(new_stem + path.suffix)


def long_path(path: os.PathLike[str] | str) -> str:
    r"""Return a ``\\?\``-prefixed path on Windows when it is long.

    Used for every file-system call in the download path so that >260 char
    targets still work on machines without the long-path policy enabled.
    """
    text = str(Path(path).absolute())
    if sys.platform != "win32":
        return text
    if text.startswith("\\\\?\\"):
        return text
    if len(text) < 250:
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text.lstrip("\\")
    return "\\\\?\\" + text


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"
