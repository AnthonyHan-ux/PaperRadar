"""Command-line interface.

The GUI is the primary product, but every capability is also reachable from the
CLI so that batch jobs (the 99-title audit) and CI-style smoke tests work
headlessly:

    PaperRadar.exe --cli search -q "optical SAR change detection" --from 2022 --to 2026
    PaperRadar.exe --cli download -q "..." --out audit.csv
    PaperRadar.exe --cli audit --titles titles.txt --out audit.csv --resume
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

from models.paper import Paper, PdfStatus, SearchMode
from providers.base import SearchQuery
from services.app_service import AppService
from utils.config import Settings, load_settings
from utils.title_normalizer import title_similarity

AUDIT_FIELDS = [
    "index", "input_title", "status", "pdf_status", "matched_title", "match_score",
    "year", "venue", "doi", "arxiv_id", "oa_status", "path", "url", "message", "elapsed_s",
]


def _status_icon(paper: Paper) -> str:
    if paper.local_pdf_path:
        return "OK"
    mapping = {
        PdfStatus.PAYWALLED_ONLY: "PAYWALL",
        PdfStatus.PUBLIC_BUT_BLOCKED: "BLOCKED",
        PdfStatus.NO_PUBLIC_COPY_FOUND: "NO-OA",
        PdfStatus.DOWNLOAD_FAILED: "FAILED",
        PdfStatus.WRONG_PAPER: "WRONG",
        PdfStatus.INVALID_PDF: "INVALID",
    }
    return mapping.get(paper.pdf_status, paper.pdf_status.value)


def _print_table(papers: Iterable[Paper], show_abstract: bool = False) -> None:
    for index, paper in enumerate(papers, 1):
        score = f"{paper.relevance_score:.0f}" if paper.relevance_score is not None else "-"
        line = (
            f"{index:>3}. [{_status_icon(paper):^8}] {score:>3}分  "
            f"{(paper.year or '----')}  {paper.title[:110]}"
        )
        print(line)
        detail = "     " + " | ".join(
            filter(None, [
                paper.venue or "",
                f"DOI {paper.doi}" if paper.doi else "",
                f"arXiv {paper.arxiv_id}" if paper.arxiv_id else "",
                f"OA {paper.oa_status.value}",
                f"PDF {paper.pdf_status.value}",
            ])
        )
        print(detail)
        if paper.pdf_urls:
            print(f"     PDF: {paper.pdf_urls[0]}")
        if show_abstract and paper.abstract:
            print(f"     摘要: {paper.abstract[:280]}…")


def _read_titles(path: Path) -> list[str]:
    titles: list[str] = []
    if not path.exists():
        raise FileNotFoundError(f"找不到标题文件：{path}")
    if path.suffix.lower() in (".csv", ".tsv"):
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            column = None
            if reader.fieldnames:
                for candidate in ("title", "标题", "Title", "TITLE"):
                    if candidate in reader.fieldnames:
                        column = candidate
                        break
            if column is None and reader.fieldnames:
                column = reader.fieldnames[0]
            for row in reader:
                value = (row.get(column or "") or "").strip()
                if value:
                    titles.append(value)
        return titles

    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        titles.append(text)
    return titles


def _best_match(papers: Iterable[Paper], title: str, threshold: float = 85.0) -> tuple[Optional[Paper], float]:
    best: Optional[Paper] = None
    best_score = 0.0
    for paper in papers:
        score = title_similarity(title, paper.title)
        if score > best_score:
            best, best_score = paper, score
        elif score == best_score and best is not None:
            if (paper.citation_count or 0) > (best.citation_count or 0):
                best = paper
    if best is not None and best_score >= threshold:
        return best, best_score
    return None, best_score


def _existing_audit_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists():
        return keys
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                value = (row.get("input_title") or "").strip()
                if value:
                    keys.add(value.lower())
    except (OSError, csv.Error):
        pass
    return keys


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_search(service: AppService, args, settings: Settings) -> int:
    query = SearchQuery(
        text=args.query,
        mode=SearchMode(args.mode),
        year_from=args.year_from,
        year_to=args.year_to,
        venues=args.venues or [],
        limit=args.limit,
    )
    query.extra["directions"] = []
    started = time.time()
    outcome = service.run_search(query)
    elapsed = time.time() - started

    _print_table(outcome.papers)
    print(f"\n{outcome.summary_text()}（{elapsed:.1f}s）")
    for name, error in outcome.errors():
        print(f"  ! {name}: {error}", file=sys.stderr)

    if args.save and outcome.papers:
        added, existing = service.add_many(outcome.papers)
        print(f"写入数据库：新增 {added}，合并 {existing}")

    if args.csv_out:
        service.export_papers(outcome.papers, "csv", Path(args.csv_out))
        print(f"CSV → {args.csv_out}")

    if args.json_out:
        payload = [paper.to_dict() for paper in outcome.papers]
        Path(args.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"JSON → {args.json_out}")
    return 0


def _progress_printer(title: str, done: int, total: int) -> None:
    if total <= 0:
        return
    percent = int(done * 100 / total)
    sys.stderr.write(f"\r  {percent:3d}%  {title[:60]:60s}")
    sys.stderr.flush()


def cmd_download(service: AppService, args, settings: Settings) -> int:
    query = SearchQuery(
        text=args.query,
        mode=SearchMode(args.mode),
        year_from=args.year_from,
        year_to=args.year_to,
        limit=args.limit,
    )
    outcome = service.run_search(query)
    papers = outcome.papers
    if not papers:
        print("没有检索结果。")
        return 1
    print(f"检索到 {len(papers)} 篇，开始查找公开 PDF…")
    results = service.download_many(papers, progress=_progress_printer)
    sys.stderr.write("\n")

    counts: dict[str, int] = {}
    for result in results:
        counts[result.status.value] = counts.get(result.status.value, 0) + 1
        icon = "OK" if result.ok else "--"
        print(f"[{icon}] {result.status.value:<20} {result.title[:80]}")
        if result.message:
            print(f"      {result.message}")
        if result.path:
            print(f"      → {result.path}")

    print("\n下载统计：" + "，".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if args.out:
        _write_audit(Path(args.out), papers, results, list(zip([p.title for p in papers], papers)))
        print(f"审计 CSV → {args.out}")
    return 0


def cmd_audit(service: AppService, args, settings: Settings) -> int:
    titles = _read_titles(Path(args.titles))
    if not titles:
        print("标题文件为空。")
        return 1
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    already = _existing_audit_keys(out_path) if args.resume else set()
    rows_written = 0
    write_header = not out_path.exists() or out_path.stat().st_size == 0

    with open(out_path, "a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AUDIT_FIELDS)
        if write_header:
            writer.writeheader()

        for index, title in enumerate(titles, 1):
            if title.lower() in already:
                print(f"[{index}/{len(titles)}] 跳过（已完成）：{title[:70]}")
                continue
            started = time.time()
            print(f"[{index}/{len(titles)}] {title[:100]}")
            row = {
                "index": index,
                "input_title": title,
                "status": "",
                "pdf_status": "",
                "matched_title": "",
                "match_score": "",
                "year": "",
                "venue": "",
                "doi": "",
                "arxiv_id": "",
                "oa_status": "",
                "path": "",
                "url": "",
                "message": "",
                "elapsed_s": "",
            }
            try:
                query = SearchQuery(
                    text=title,
                    mode=SearchMode.TITLE,
                    year_from=args.year_from,
                    year_to=args.year_to,
                    limit=max(3, args.limit),
                )
                outcome = service.run_search(query)
                match, score = _best_match(outcome.papers, title)
                row["match_score"] = f"{score:.1f}"
                if match is None:
                    row["status"] = "NO_MATCH"
                    row["message"] = f"未找到标题匹配（最佳 {score:.1f} 分）"
                    print("    → 未匹配到论文")
                else:
                    row.update(
                        {
                            "matched_title": match.title,
                            "year": match.year or "",
                            "venue": match.venue or "",
                            "doi": match.doi or "",
                            "arxiv_id": match.arxiv_id or "",
                        }
                    )
                    result = service.download_one(match)
                    row["status"] = "FOUND"
                    row["pdf_status"] = result.status.value
                    row["oa_status"] = match.oa_status.value
                    row["path"] = str(result.path) if result.path else ""
                    row["url"] = result.used_url
                    row["message"] = result.message[:500]
                    print(f"    → {result.status.value}  {result.message[:90]}")
            except Exception as exc:  # one bad title must not stop the batch
                row["status"] = "ERROR"
                row["message"] = f"{type(exc).__name__}: {exc}"
                print(f"    → 错误：{exc}")
            row["elapsed_s"] = f"{time.time() - started:.1f}"
            writer.writerow(row)
            handle.flush()
            rows_written += 1

    print(f"\n审计完成，本次写入 {rows_written} 行 → {out_path}")
    return 0


def _write_audit(path: Path, papers: list[Paper], results, pairs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    by_title = {result.title: result for result in results}
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AUDIT_FIELDS)
        writer.writeheader()
        for index, (title, paper) in enumerate(pairs, 1):
            result = by_title.get(title)
            writer.writerow({
                "index": index,
                "input_title": title,
                "status": "FOUND",
                "pdf_status": result.status.value if result else "",
                "matched_title": paper.title,
                "match_score": "",
                "year": paper.year or "",
                "venue": paper.venue or "",
                "doi": paper.doi or "",
                "arxiv_id": paper.arxiv_id or "",
                "oa_status": paper.oa_status.value,
                "path": str(result.path) if result and result.path else "",
                "url": result.used_url if result else "",
                "message": result.message if result else "",
                "elapsed_s": "",
            })


def cmd_stats(service: AppService, args, settings: Settings) -> int:
    stats = service.stats()
    count, total_bytes = service.library.disk_usage()
    print(f"数据库：{settings.paths.db_path}")
    print(f"论文总数：{stats.total}")
    print(f"本地 PDF：{stats.with_pdf}（已验证 {stats.validated}，磁盘文件 {count}）")
    print(f"仅付费：{stats.paywalled}    公开但被阻止：{stats.blocked}    无公开副本：{stats.no_public_copy}")
    print(f"文献库占用：{total_bytes / 1024 / 1024:.1f} MB")
    print("状态分布：")
    for status, number in sorted(stats.by_status.items()):
        print(f"  {status:<22} {number}")
    return 0


def cmd_profile(service: AppService, args, settings: Settings) -> int:
    profile = settings.profile
    print(f"方向：{profile.get('name')}")
    for key, label in (
        ("keywords", "核心关键词"),
        ("positive_terms", "加分词"),
        ("negative_terms", "排除词"),
        ("priority_venues", "优先期刊/会议"),
    ):
        values = profile.get(key) or []
        print(f"  {label}: {', '.join(str(v) for v in values)}")
    print(f"配置文件：{settings.paths.profile_path}")
    return 0


def cmd_export(service: AppService, args, settings: Settings) -> int:
    papers = service.library_papers(limit=100000, only_local=args.only_local)
    if not papers:
        print("没有可导出的论文。")
        return 1
    written = service.export_papers(papers, args.format, Path(args.out))
    print(f"已导出 {len(papers)} 篇 → {written}")
    return 0


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="PaperRadar", description="PaperRadar CLI")
    parser.add_argument("--home", help="数据目录（默认 exe/项目根目录）")
    sub = parser.add_subparsers(dest="command")

    search = sub.add_parser("search", help="检索论文")
    search.add_argument("-q", "--query", required=True)
    search.add_argument("-m", "--mode", default="keywords",
                        choices=[m.value for m in SearchMode])
    search.add_argument("--from", dest="year_from", type=int)
    search.add_argument("--to", dest="year_to", type=int)
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--venues", nargs="*", default=[])
    search.add_argument("--save", action="store_true", help="写入本地数据库")
    search.add_argument("--csv", dest="csv_out")
    search.add_argument("--json", dest="json_out")
    search.set_defaults(func=cmd_search)

    download = sub.add_parser("download", help="检索并下载公开 PDF")
    download.add_argument("-q", "--query", required=True)
    download.add_argument("-m", "--mode", default="keywords",
                          choices=[m.value for m in SearchMode])
    download.add_argument("--from", dest="year_from", type=int)
    download.add_argument("--to", dest="year_to", type=int)
    download.add_argument("--limit", type=int, default=10)
    download.add_argument("--out", help="写出审计 CSV")
    download.set_defaults(func=cmd_download)

    audit = sub.add_parser("audit", help="批量标题审计（断点续跑）")
    audit.add_argument("--titles", required=True, help="txt（每行一个标题）或 csv（title 列）")
    audit.add_argument("--out", default="audit.csv")
    audit.add_argument("--limit", type=int, default=8)
    audit.add_argument("--from", dest="year_from", type=int)
    audit.add_argument("--to", dest="year_to", type=int)
    audit.add_argument("--resume", action="store_true", help="跳过 out 中已存在的标题")
    audit.set_defaults(func=cmd_audit)

    stats = sub.add_parser("stats", help="显示数据库统计")
    stats.set_defaults(func=cmd_stats)

    profile = sub.add_parser("profile", help="显示研究画像")
    profile.set_defaults(func=cmd_profile)

    export = sub.add_parser("export", help="导出本地文献库")
    export.add_argument("--format", default="csv", choices=["csv", "bib", "ris"])
    export.add_argument("--out", required=True)
    export.add_argument("--only-local", action="store_true", help="只导出已有 PDF 的论文")
    export.set_defaults(func=cmd_export)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2

    settings = load_settings(home=Path(args.home) if args.home else None)
    service = AppService(settings=settings)
    try:
        return int(args.func(service, args, settings) or 0)
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130
    finally:
        service.close()
