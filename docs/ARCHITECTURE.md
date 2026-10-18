# PaperRadar 架构说明（v1.1.0）

## 1. 分层

```
GUI (PySide6)          app/  ui/
        │  Qt signals / QThreadPool
        ▼
AppService             services/app_service.py      —— 唯一门面（GUI 与 CLI 共用）
        │
        ├── SearchService ──► providers/*           —— 并行检索、超时、合并、过滤、打分
        ├── OAFinder      ──► providers/*           —— 6 层 OA 解析
        ├── DownloadService                         —— 候选回退 / 续传 / 校验后入库
        ├── PdfValidator                            —— 5 层真伪校验
        ├── LibraryService                          —— 年份/会场目录 + 文件名
        ├── ExportService                           —— CSV / BibTeX / RIS
        └── Database (SQLite WAL)                   —— 去重、状态、日志、统计
```

`providers/` 与 `services/` **不依赖 Qt**，可以在无界面环境下测试；GUI 只负责展示与调度。

## 2. 一次检索的数据流

```
SearchQuery
  ├─ providers 并行（ThreadPoolExecutor，硬截止 = provider_timeout × 1.5）
  │    OpenAlex / Semantic Scholar / Crossref / arXiv（+ 可选 Unpaywall / OpenReview）
  ├─ MergeService：DOI → arXiv → 标题精确 → 标题指纹桶 + RapidFuzz(≥95)
  ├─ 过滤：年份、会议/期刊、研究方向
  ├─ RelevanceService：标题40/摘要30/关键词15/方向10/期刊5，排除词−15
  └─ SearchOutcome（papers + 每个 provider 的耗时/错误/超时标记）
```

单个 provider 卡死或报错不会影响其他 provider；结果为空时也保留错误原因用于日志。

## 3. 一次下载的数据流

```
Paper
  ├─ OAFinder（无候选时）
  │    1 搜索结果自带链接 → 2 arXiv（已知 id 免网络）→ 3 OpenAlex
  │    4 Semantic Scholar → 5 Unpaywall → 6 OpenReview
  ├─ DownloadService：按候选顺序尝试
  │    · 401/403 → PUBLIC_BUT_BLOCKED
  │    · 402/451 → PAYWALLED_ONLY
  │    · 5xx/超时/断网 → DOWNLOAD_FAILED（绝不误判为 blocked）
  │    · Range 续传；服务器忽略 Range 返回 200 时重新完整下载
  │    · 流式写入 tmp/*.part，超过大小上限立即中止
  ├─ PdfValidator：%PDF- 魔数 → 大小 → PyMuPDF 可开 → 文本层 → 标题/DOI 匹配
  │    通过 → 原子改名进 library/<年>/<会场>/
  │    失败 → 删除 .part，尝试下一个候选
  └─ Database：更新 pdf_status / local_path / downloaded_at，写 download_logs
```

## 4. 状态机不变式

- `NO_PUBLIC_COPY_FOUND` ≠ `PAYWALLED_ONLY`：只有 OpenAlex/Unpaywall 明确返回 closed 才允许后者。
- `PUBLIC_BUT_BLOCKED` 只由 401/403 产生，且表示「已确认公开」；网络错误一律 `DOWNLOAD_FAILED`。
- `WRONG_PAPER` / `INVALID_PDF` 永不写入文献库。
- 已有本地 PDF 的记录不会被 OA 解析降级为 `OPEN_ACCESS_FOUND`（`pdf_status.has_local_file` 保护）。
- 文档`MISSING_LOCAL_FILE`只表示「有记录、文件没了」，记录本身不删除。

## 5. 数据库

- SQLite WAL；每线程一个连接，`check_same_thread=False` 以便关机时统一关闭。
- `Database.close_thread()` 由 worker 在任务结束时调用；`close_all()` 在退出时释放全部句柄（Windows 下这样备份/迁移才能重命名文件）。
- `upsert` 遇到 DOI/arXiv 唯一索引冲突时，按优先级选择 canonical 行（已验证 PDF > 本地文件 > DOI > arXiv > 更早创建）并迁移 `paper_sources` / `paper_tags` / `download_logs`，绝不把 `IntegrityError` 抛给 UI。
- `schema_version` 存于 `meta` 表；迁移前自动备份，`title_key` 指纹列按版本补齐。
- 启动时 `PRAGMA quick_check`；损坏则改名隔离（不删除）后以空库启动。

## 6. 线程与取消

- GUI 所有网络/解析/下载都在 `QThreadPool`（`app/workers.py`）。
- 下载取消通过 `threading.Event` 合作式检查，每个 chunk 一次；取消后删除 `.part`，不留脏文件。
- 检索超时后 executor 以 `shutdown(wait=False, cancel_futures=True)` 放弃等待，UI 立即返回。

## 7. 数据目录

```
%LOCALAPPDATA%\PaperRadar\data\
    config\user_config.yaml, research_profile.yaml
    database\paperradar.db
    library\<year>\<venue>\<year>_<author>_<title>.pdf
    logs\app.log
    cache\*.json
    tmp\*.part
    exports\
    migration.json           （迁移完成标记）
```

`PAPERRADAR_HOME` 可整体覆盖（测试与便携部署使用）。
