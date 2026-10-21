"""PDF authenticity validation.

The single most important lesson from automated PDF harvesting: **HTTP 200
means nothing**.  Publisher error pages, Cloudflare challenges and login walls
all arrive as ``200 text/html`` and, if you save them naively, you end up with a
library full of fake PDFs.

Layers implemented here:

1. magic bytes ``%PDF-`` in the first KB
2. file size floor (default 10 KB)
3. PyMuPDF can open it and reports >= 1 page
4. first page yields a text layer
5. normalised title fuzzy-matches the first-page text
   (plus a DOI/arXiv-id bonus when present)

Outcomes: ``VALIDATED`` / ``TITLE_WARNING`` / ``WRONG_PAPER`` / ``INVALID_PDF``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from models.paper import PdfStatus, Paper, clean_doi, clean_arxiv_id
from utils.title_normalizer import contains_term, normalize_text, title_similarity

log = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"
HEADER_PROBE_BYTES = 2048
FIRST_PAGE_CHARS = 6000


@dataclass
class ValidationResult:
    status: PdfStatus
    reason: str
    title_similarity: float = 0.0
    page_count: int = 0
    file_size: int = 0
    has_text: bool = False
    matched_title: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (PdfStatus.VALIDATED, PdfStatus.TITLE_WARNING)

    @property
    def is_fake(self) -> bool:
        return self.status in (PdfStatus.INVALID_PDF, PdfStatus.WRONG_PAPER)


class PdfValidator:
    def __init__(
        self,
        min_bytes: int = 10_000,
        title_threshold: float = 90.0,
        warning_threshold: float = 80.0,
    ) -> None:
        self.min_bytes = max(0, int(min_bytes))
        self.title_threshold = float(title_threshold)
        self.warning_threshold = float(warning_threshold)

    # ------------------------------------------------------------------ #
    def check_header(self, path: Path) -> bool:
        try:
            with open(path, "rb") as handle:
                head = handle.read(HEADER_PROBE_BYTES)
        except OSError:
            return False
        # Some servers prepend whitespace/BOM before the magic bytes.
        return PDF_MAGIC in head

    def _open_document(self, path: Path):
        import fitz  # imported lazily: PyMuPDF initialisation is not free

        return fitz.open(str(path))

    # ------------------------------------------------------------------ #
    def validate(self, path: Path, paper: Optional[Paper] = None,
                 expected_title: Optional[str] = None) -> ValidationResult:
        path = Path(path)
        title = expected_title or (paper.title if paper else "")

        try:
            size = path.stat().st_size
        except OSError as exc:
            return ValidationResult(PdfStatus.INVALID_PDF, f"文件不存在或无法访问：{exc}")

        if not self.check_header(path):
            return ValidationResult(
                PdfStatus.INVALID_PDF,
                "文件头不是 %PDF-，很可能是 HTML/登录页/错误页",
                file_size=size,
            )
        if size < self.min_bytes:
            return ValidationResult(
                PdfStatus.INVALID_PDF,
                f"文件仅 {size} 字节，低于可信下限 {self.min_bytes} 字节",
                file_size=size,
            )

        try:
            document = self._open_document(path)
        except Exception as exc:  # fitz raises a variety of errors
            return ValidationResult(
                PdfStatus.INVALID_PDF, f"PyMuPDF 无法打开：{exc}", file_size=size
            )

        try:
            if getattr(document, "needs_pass", False):
                return ValidationResult(
                    PdfStatus.INVALID_PDF, "PDF 已加密，需要密码", file_size=size
                )
            page_count = document.page_count
            if page_count < 1:
                return ValidationResult(
                    PdfStatus.INVALID_PDF, "PDF 没有任何页面", file_size=size
                )
            try:
                first_page = document.load_page(0)
                page_text = first_page.get_text("text") or ""
            except Exception as exc:  # pragma: no cover - malformed page
                return ValidationResult(
                    PdfStatus.INVALID_PDF, f"第一页无法解析：{exc}",
                    page_count=page_count, file_size=size,
                )
        finally:
            try:
                document.close()
            except Exception:
                pass

        page_text = page_text[:FIRST_PAGE_CHARS]
        has_text = len(normalize_text(page_text)) > 40
        if not has_text:
            return ValidationResult(
                PdfStatus.TITLE_WARNING,
                "PDF 无文本层（可能是扫描件），无法自动校验标题，请人工确认",
                page_count=page_count,
                file_size=size,
                has_text=False,
            )

        similarity = self._title_similarity(title, page_text)
        title_region = self._title_region(page_text)
        doi_bonus = self._identifier_present(title_region, paper)
        header_dois = {clean_doi(match.group()) for match in re.finditer(
            r'(?<![A-Za-z0-9])10\.\d{4,9}/[^\s<>"\'}]+', title_region, re.I)}
        conflict = bool(paper and paper.doi and header_dois and paper.doi not in header_dois)
        authors_missing = bool(paper and paper.authors and not doi_bonus and not any(
            contains_term(normalize_text(title_region), normalize_text(author.split(',')[0] if ',' in author else author).split()[-1])
            for author in paper.authors if normalize_text(author)))
        if similarity >= self.title_threshold and (conflict or authors_missing):
            return ValidationResult(
                PdfStatus.TITLE_WARNING,
                "标题匹配，但" + ("首页 DOI 与目标不一致，请核对版本" if conflict else "未找到预期作者，请人工确认身份"),
                title_similarity=similarity, page_count=page_count, file_size=size,
                has_text=True, matched_title=self._guess_title_line(page_text),
            )

        if similarity >= self.title_threshold:
            return ValidationResult(
                PdfStatus.VALIDATED,
                f"标题相似度 {similarity:.1f} ≥ {self.title_threshold:.0f}"
                + ("；DOI/arXiv ID 精确匹配" if doi_bonus else ""),
                title_similarity=similarity,
                page_count=page_count,
                file_size=size,
                has_text=True,
                matched_title=self._guess_title_line(page_text),
            )
        if similarity >= self.warning_threshold:
            return ValidationResult(
                PdfStatus.TITLE_WARNING,
                f"标题相似度 {similarity:.1f} 处于警告区间"
                f"（{self.warning_threshold:.0f}-{self.title_threshold:.0f}）",
                title_similarity=similarity,
                page_count=page_count,
                file_size=size,
                has_text=True,
                matched_title=self._guess_title_line(page_text),
            )
        return ValidationResult(
            PdfStatus.WRONG_PAPER,
            f"首页标题与目标论文不符（相似度 {similarity:.1f}），可能下载成了别的论文",
            title_similarity=similarity,
            page_count=page_count,
            file_size=size,
            has_text=True,
            matched_title=self._guess_title_line(page_text),
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _title_similarity(title: str, page_text: str) -> float:
        if not title:
            return 0.0
        norm_title = normalize_text(title)
        if not page_text or not norm_title:
            return 0.0
        # Restrict evidence to the title area; a citation in the abstract or
        # bibliography must never identify the document that contains it.
        head = PdfValidator._title_region(page_text)
        lines = [line.strip() for line in head.splitlines() if line.strip()][:20]
        scores = [title_similarity(title, ' '.join(lines[start:start + length]))
                  for start in range(len(lines)) for length in range(1, 7)
                  if start + length <= len(lines)]
        return max(scores, default=0.0)

    @staticmethod
    def _title_region(page_text: str) -> str:
        return re.split(r'(?im)^\s*(?:abstract|references|bibliography|'
                        r'(?:\d+[.\s]+)?introduction|摘要|参考文献)\b', page_text, maxsplit=1)[0]

    @staticmethod
    def _identifier_present(page_text: str, paper: Optional[Paper]) -> bool:
        if paper is None:
            return False
        doi_values = {clean_doi(match.group()) for match in
                      re.finditer(r'(?<![A-Za-z0-9])10\.\d{4,9}/[^\s<>"\'}]+', page_text, re.I)}
        if paper.doi and paper.doi in doi_values:
            return True
        arxiv_values = {clean_arxiv_id(match.group()) for match in re.finditer(
            r'(?<![\w.])(?:\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[a-z]{2})?/\d{7}(?:v\d+)?)(?![\w.])',
            page_text, re.I)}
        return bool(paper.arxiv_id and paper.arxiv_id in arxiv_values)

    @staticmethod
    def _guess_title_line(page_text: str) -> str:
        lines = [line.strip() for line in page_text.splitlines() if line.strip()]
        for i in range(min(len(lines), 12)):
            candidate = " ".join(lines[i : i + 2]).strip()
            if len(candidate) >= 15:
                return candidate[:200]
        return (lines[0] if lines else "")[:200]
