# v1.3.1 - Explain empty searches and expand known bilingual terms

- Show progress, completion, source failures and per-stage filter counts in the results tab.
- Distinguish no enabled sources, all-source failure, no candidates and candidates excluded by filters.
- Add explicit relaxation actions that preserve the remaining query settings.
- Add image degradation/restoration terms and common multimodal aliases, with visible English query expansion.
- Cover the reported search and relaxation clicks through real Qt worker callbacks.

# v1.3.0 - Search suggestions and calendar filters

- Add editable bilingual topic and venue suggestions linked to eight broad disciplines.
- Add contains-completion, multi-select filters and fuzzy/phrase matching controls.
- Pass inclusive day-precision ranges to Crossref, OpenAlex, arXiv and Semantic Scholar.
- Preserve partial date precision, disclose arXiv submission dates and offer uncertain-date inclusion.
- Store date metadata with a backed-up schema v4 migration; abort when the required backup fails.
- Support direction-only and journal-only searches and preserve partial results after route failures.
- Show and export bibliographic dates, sort within the same year, and apply safer Crossref request spacing.

# v1.2.0 - Reliability and general-purpose search

- Guard title-based deduplication in both provider merging and database lookups.
- Require title-area evidence for PDF identity and disclose author/DOI conflicts.
- Correct Crossref singleton DOI parameters and preserve provider errors.
- Recover stale PDF candidates through one bounded OA discovery pass.
- Retry unfinished audit entries and record attempt number/time in new CSV files.
- Rank by the current query, with explicit profile/year alternatives.
- Use discipline-neutral defaults and retain remote sensing as optional examples.
- Preserve existing user profiles, database schema and stored library files.

# Changelog

## 1.1.0 — 2026-10-19（Production Release）

### 数据安全（P0）
- 运行数据从程序目录迁移到 `%LOCALAPPDATA%\PaperRadar\data`，程序目录可只读（Program Files / U 盘可用）。
- 新增一次性自动迁移：备份 → SQLite 在线备份复制 → 完整性检查 + 论文数比对 → 复制文献库/配置 → `migration.json` 幂等标记；失败则继续使用旧目录。
- 启动时 `PRAGMA quick_check`；损坏数据库改名隔离（不删除）后以空库启动。
- 配置文件解析失败时备份为 `*.broken.<时间>.yaml` 并回退默认值。
- schema 升级前自动备份（保留最近 3 个），`meta.schema_version` 记录版本（v3）。
- 新增 `MISSING_LOCAL_FILE` 状态：本地 PDF 丢失时保留记录并标记，不再静默重置。

### 正确性修复
- 检索硬截止：provider 卡死不再拖住整轮检索（上限 `provider_timeout_seconds × 1.5`）。
- 限流器改为按 key 预约时隙、锁外 sleep：arXiv 的 3.2s 间隔不再阻塞 OpenAlex/Crossref。
- OA 语义：无结构化证据时保持 `UNKNOWN` + `NO_PUBLIC_COPY_FOUND`，只有明确 closed 才是 `PAYWALLED_ONLY`；402/451 归为付费墙，网络错误不再误判为 `PUBLIC_BUT_BLOCKED`。
- 去重：新增 `title_key` 指纹分桶，模糊比对不再受「同年 ±1 / 前 800 行」限制；跨年预印本可合并。
- DOI/arXiv 写库统一规范化（大小写），否则唯一索引会被绕过。
- DOI/arXiv 撞唯一索引时自动合并重复行并迁移 provenance，不再抛 `IntegrityError`。
- venue 归一化扩到 JSTARS/GRSL/RS(MDPI)/JAG/PE&RS 等，未知会场记录日志。

### 工程与发布
- 数据库连接生命周期修复：worker 结束即关闭，退出时统一 `close_all()`（Windows 可备份/重命名）。
- Provider 离线 fixture 测试（真实响应录制）+ AppService 端到端测试。
- CLI：UTF-8 输出、`--version`、`stats --downloads-csv`、`cache clear`、下载日志记录 `http_status`。
- Windows 发布：`1.1.0.0` 版本信息、应用图标、`build.ps1` 自动 `--smoke` 自检。
- GUI 自检模式 `--selftest [--selftest-doi]`：打包后真实走 UI 下载链路。
- 文档：README 重写，新增 ARCHITECTURE / MIGRATION / VALIDATION / RELEASE_CHECKLIST / HANDOFF_PROMPT。
- 测试：223 个离线测试。

### 兼容性
- V1.0 数据目录自动迁移；旧 `config/config.yaml` 作为 `user_config.yaml` 的来源继续生效。
- `PAPERRADAR_HOME` 仍可覆盖数据目录（此时不自动迁移）。

## 1.0.0 — 初始版本
- 多源检索、四层去重、可解释相关性评分、OA 解析、批量下载、PDF 五层校验、SQLite 文献库、CSV/BibTeX/RIS 导出、CLI 批量审计、PyInstaller onedir 打包。

- Access/locked/read-only database errors no longer trigger corruption quarantine.
