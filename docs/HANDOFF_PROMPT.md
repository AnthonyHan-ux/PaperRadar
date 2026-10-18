# PaperRadar 交接说明（v1.1.0）

给接手的人 / 下一个会话的上下文提示词。

## 1. 这是什么

Windows 本地科研工具：多源检索 → 去重 → 相关性排序 → 找合法公开 PDF → 下载 → 校验 → 本地文献库 → 导出。
Python 3.11 + PySide6 6.7.3 + SQLite WAL + httpx + rapidfuzz + PyMuPDF，PyInstaller onedir 打包。

## 2. 关键事实（不要重新猜）

- 运行数据在 `%LOCALAPPDATA%\PaperRadar\data`，程序目录只读也能跑。
- V1.0 数据在首次启动时自动迁移（`services/migration_service.py`，`migration.json` 幂等）。
- 状态语义是硬约束：
  - 无 OA 证据 → `oa_status=UNKNOWN` + `pdf_status=NO_PUBLIC_COPY_FOUND`
  - 只有 OpenAlex/Unpaywall 明确 closed → `PAYWALLED_ONLY`
  - 401/403 → `PUBLIC_BUT_BLOCKED`；超时/5xx → `DOWNLOAD_FAILED`
  - `WRONG_PAPER`/`INVALID_PDF` 永不入库
- 检索硬截止 = `search.provider_timeout_seconds × 1.5`；超时 provider 被放弃（`shutdown(wait=False)`）。
- 限流按 key 独立预约时隙，sleep 在锁外。
- 去重：DOI → arXiv → 规范化标题 → `title_key` 指纹桶 + RapidFuzz(≥95)；不再按年份/前 800 行扫描。
- `Database.close_thread()` 由 worker 调用，`close_all()` 退出时释放所有句柄（Windows 重命名/备份依赖它）。
- DOI/arXiv 在写库时统一规范化（小写），否则唯一索引大小写不一致会绕过。
- `upsert`/`update` 撞唯一索引时合并重复行（canonical 优先级：已验证 PDF > 本地文件 > DOI > arXiv > 更早 id），并迁移 provenance。
- schema_version = 3（meta 表），升级自动备份 + 补列/回填。
- 数据库损坏 → 改名隔离后空库启动；配置文件损坏 → 备份后回退默认。
- GUI 下载自检：`PaperRadar.exe --selftest out.json --selftest-doi <DOI>`（真实 GUI 路径，无需人工点击）。

## 3. 代码地图

```
main.py                 GUI 入口 / --cli / --smoke / --selftest
cli.py                  search download audit stats(--downloads-csv) export cache clear profile --version
app/                    main_window（装配）、workers（QThreadPool）、theme（QSS+图标）
ui/                     search_panel / result_table / library_panel / download_panel / log_panel / models / dialogs
models/paper.py         Paper + OaStatus/PdfStatus（含 MISSING_LOCAL_FILE）
providers/              base/http + 6 个源；http 负责限流、重试、缓存、错误分类
services/               search / merge / relevance / oa_finder / download / pdf_validator /
                        library / export / migration / app_service
database/               db.py（schema v3、迁移、备份、冲突合并、quick_check）+ schema.sql
utils/                  title_normalizer（含 title_fingerprint）/ filename / rate_limiter /
                        config（数据目录+迁移触发）/ venues / logger
tests/                  240+ 离线测试 + fixtures（真实 API 响应，Unpaywall 为手写）
tools/                  gui_smoke.py、fetch_fixtures.py、make_icon.py、cli_entry.py
assets/                 paperradar.ico、version_info*.txt
docs/                   ARCHITECTURE / MIGRATION / VALIDATION / RELEASE_CHECKLIST / HANDOFF_PROMPT
```

## 4. 常用命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe main.py --cli search -q "optical SAR change detection" --limit 10
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Clean
.\dist\PaperRadar\cli\PaperRadarCLI.exe --version
.\dist\PaperRadar\PaperRadar.exe --selftest out.json --selftest-doi 10.1109/lgrs.2025.3590959
```

## 5. 已知限制（V1.1 有意不做）

- 不做网页爬取补漏（CVF/PMLR/作者主页/GitHub 等），避免不稳定与合规风险。
- 不做 OCR；扫描版 PDF 只能 `TITLE_WARNING`。
- S2 未认证时仍可能 429（已降级，不阻塞）。
- 会场归一化覆盖常见遥感/视觉 venues，未知归 `Other`。
- `title_key` 前 3 个实词分桶：前缀不同的近似标题不会合并（为避免 O(n²)）。

## 6. 修改前请先读

- `docs/ARCHITECTURE.md`（数据流与不变式）
- `docs/MIGRATION.md`（数据目录规则）
- `assets/version_info.txt`（版本号唯一来源在 `utils/config.py: APP_VERSION`，PyInstaller 版本文件需同步）
