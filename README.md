# PaperRadar · 遥感文献雷达

本地运行的**学术文献雷达 + 开放获取 PDF 下载器 + 本地文献库**。  
从一个关键词出发：多源检索 → 去重合并 → 相关性打分 → 查找合法公开全文 → 下载 → 校验真伪 → 入库 → 导出。

![PaperRadar 主界面](docs/screenshot.png)

> 只用合法公开来源（arXiv / OpenAlex / Semantic Scholar / Crossref / Unpaywall / OpenReview）。
> **不访问 Sci-Hub，不绕过付费墙，不破解验证码。** 找不到就明确告诉你为什么找不到。

---

## 1. 功能一览（V1）

| 能力 | 说明 |
| --- | --- |
| 多源检索 | OpenAlex、Semantic Scholar、Crossref、arXiv 并行查询，任一源失败不影响其他源 |
| 检索方式 | 关键词 / 论文标题 / 作者 / DOI / arXiv ID / 研究方向 |
| 过滤 | 年份区间、会议期刊多选、研究方向（Change Detection、Optical-SAR、Disaster…） |
| 去重 | DOI → arXiv ID → 标题精确匹配 → 标题模糊匹配（rapidfuzz ≥ 95） |
| 元数据合并 | 一个源有摘要、另一个源有 PDF 链接 → 自动合并成一条完整记录 |
| 相关性评分 | 可解释的 0–100 分（标题 40 / 摘要 30 / 关键词 15 / 研究方向 10 / 期刊 5，排除词扣分），悬停即可看到每一分的来源 |
| OA 查找 | 按顺序尝试：自带链接 → arXiv → OpenAlex → Semantic Scholar → Unpaywall → OpenReview，全部去重 |
| PDF 下载 | 多候选回退、断点续传、超时重试、403/404 分类、`.part` 临时文件、验证通过才入库 |
| PDF 校验 | 文件头 `%PDF-`、文件大小、PyMuPDF 可打开、首页文本层、标题模糊匹配（DOI/arXiv ID 命中可直接通过） |
| 本地文献库 | SQLite（WAL）+ 按 `年份/期刊` 分层存储，Windows 非法字符与 MAX_PATH 全处理 |
| 导出 | CSV（UTF-8 BOM，Excel 直接打开）、BibTeX、RIS |
| 后台执行 | 所有网络/解析/下载都在 QThreadPool 里，GUI 永不卡死；可随时取消 |
| 断点续跑 | 批量审计写入一行就 flush，`--resume` 跳过已完成的标题 |
| 日志 | GUI 实时日志 + `logs/app.log`（轮转）+ 下载记录表 |

V2/V3 预留（代码结构已就位，未实现）：Playwright 网页补漏、作者主页/GitHub/机构仓库、新论文雷达（7/30 天）、下载队列持久化、更漂亮的 GUI。

---

## 2. 快速开始

### 2.1 直接运行 exe（推荐）

1. 解压/复制 `dist\PaperRadar\` 整个目录（**不要只拿 exe**，`_internal` 是依赖）
2. 双击 `PaperRadar.exe`
3. 首次运行会在 exe 同目录创建 `config/ library/ database/ logs/ cache/ tmp/ exports/`

### 2.2 从源码运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

### 2.3 一步打包

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

产物：

```
dist\PaperRadar\PaperRadar.exe            # 图形界面（onedir，启动快）
dist\PaperRadar\cli\PaperRadarCLI.exe     # 命令行（批量审计用，与 GUI 共用数据目录）
```

> 两个 exe 都是 onedir（GUI 在根目录、CLI 在 `cli\` 子目录），**分发时要整个 `dist\PaperRadar\` 目录一起拷走**。
> 国内网络下 `build.ps1` 默认走清华镜像；如需官方源加 `-OfficialPyPI`。

---

## 3. 界面与使用

**左侧**：检索模式、关键词、每个来源取多少篇、年份、会议/期刊多选、研究方向多选、`搜索论文` / `我的方向` / `重置条件`。

**右侧四个标签页**：

- **搜索结果**：状态、标题、年份、作者、期刊、DOI、arXiv、相关性、OA、PDF 状态、本地。
  点表头排序（默认按相关性降序）；可搜索框二次筛选、只看公开、只看本地；右键有「下载 / 查看详情 / 打开来源 / 复制标题」。
- **本地文献库**：数据库里的全部论文，勾选「只看已下载 PDF」可过滤；双击打开 PDF 或来源页。
- **下载队列**：逐篇状态 + 总进度条 + 取消。
- **日志**：与 `logs/app.log` 同源，出错时先看这里。

**底部按钮**：下载选中 / 下载全部 / 加入文献库 / 导出 CSV / BibTeX / RIS / 打开文件夹 / 打开来源页。

### 3.1 相关性评分怎么算

| 通道 | 权重 | 饱和条件 |
| --- | --- | --- |
| 标题命中 | 40 | 命中 2 个不同关键词即满 |
| 摘要命中 | 30 | 命中 4 个即满 |
| 关键词字段命中 | 15 | 命中 3 个即满 |
| 研究方向匹配 | 10 | 命中 1 个即满 |
| 优先期刊/会议 | 5 | 命中即得 |
| 排除词 | 每命中 1 个 −15（最多 −30） | — |

一个词只算一次，取它出现的最强字段（标题 > 摘要 > 关键词），避免重复计分。  
分数区间：`≥90 高度相关`、`70–89 较相关`、`50–69 可能相关`、`<50 低相关`。

### 3.2 PDF 状态语义（重要）

| 状态 | 含义 |
| --- | --- |
| `VALIDATED` | 已下载并通过标题/DOI 校验，已在文献库 |
| `TITLE_WARNING` | 已下载，标题相似度 80–90 或 PDF 无文本层（扫描件），需人工确认 |
| `PUBLIC_BUT_BLOCKED` | **确认存在公开副本**，但服务器 403/401 拒绝自动下载 → 请在浏览器打开 |
| `PAYWALLED_ONLY` | 只有付费出版社版本，未发现合法公开副本 |
| `NO_PUBLIC_COPY_FOUND` | 未找到任何公开副本（不代表它一定付费） |
| `DOWNLOAD_FAILED` | 有候选链接但下载失败（网络/文件不可用） |
| `WRONG_PAPER` | 下到的 PDF 不是目标论文（标题不符） |
| `INVALID_PDF` | 不是有效 PDF（HTML 错误页/登录页/损坏/过小） |

后四种永远不会被塞进文献库——**宁缺毋滥**。

---

## 4. 配置

`config/config.yaml`（首次运行自动生成，改完重启生效）：

```yaml
api:
  openalex_mailto: "you@example.com"   # 加入 OpenAlex 礼貌池，10 req/s
  unpaywall_email: "you@example.com"   # 启用 Unpaywall（必须有邮箱）
  semantic_scholar_api_key: ""          # 可选，显著缓解 429
  timeout_seconds: 30
  cache_ttl_hours: 24                   # 查询结果缓存，重复搜索秒回
download:
  max_workers: 3
  min_pdf_bytes: 10000
  validate_title_threshold: 90
scoring:
  weights: {title: 40, abstract: 30, keywords: 15, direction: 10, venue: 5}
```

**务必填 `openalex_mailto`**：OpenAlex 对无邮箱请求限流更严，Unpaywall 没有邮箱直接不可用（程序会在日志里提示）。

### 4.1 研究方向

`config/research_profile.yaml`，或点界面上的 **我的方向** 直接编辑：

```yaml
name: Degradation-Robust Multimodal Remote Sensing
keywords: [optical SAR, change detection, building damage, degradation, fog, cloud, ...]
positive_terms: [cross-event, generalization, missing modality, ...]
negative_terms: [medical, autonomous driving, ...]
priority_venues: [ISPRS Journal of Photogrammetry and Remote Sensing, IEEE TGRS, CVPR, ...]
```

---

## 5. 批量审计（你手上那 99 篇）

准备 `titles.txt`（每行一个标题，`#` 开头会被跳过）或 CSV（需要 `title` 列），然后：

```powershell
cd dist\PaperRadar
.\cli\PaperRadarCLI.exe audit --titles titles.txt --out audit.csv --resume
```

流程：逐条按标题检索 → 选标题相似度最高的记录（<85 分记为 `NO_MATCH`）→ 查找公开 PDF → 下载 → 校验 → **每行立刻写盘**。

中途断了直接重跑，`--resume` 会跳过 `audit.csv` 里已有的标题。

`audit.csv` 字段：

```
index, input_title, status, pdf_status, matched_title, match_score,
year, venue, doi, arxiv_id, oa_status, path, url, message, elapsed_s
```

收尾统计：

```powershell
.\cli\PaperRadarCLI.exe stats
```

会输出总数、已有 PDF、仅付费、公开但被阻止、无公开副本，以及文献库磁盘占用。

其他 CLI：

```powershell
.\cli\PaperRadarCLI.exe search -q "optical SAR change detection" --from 2023 --to 2026 --limit 20 --save
.\cli\PaperRadarCLI.exe profile
.\cli\PaperRadarCLI.exe export --format bib --out library.bib --only-local
```

> `PaperRadar.exe` 是窗口程序，stdout 不可见；命令行请用 `cli\PaperRadarCLI.exe`。
> 两者共用 `dist\PaperRadar\` 下的数据库和文献库（默认数据目录 = exe 所在目录，CLI 会自动上溯到 GUI 目录）。

---

## 6. 数据目录

```
PaperRadar/
  config/      config.yaml、research_profile.yaml
  library/     2026/CVPR/2026_Zhang_Beyond_Attention_....pdf
  database/    paperradar.db（SQLite WAL）
  logs/        app.log（5MB × 5 轮转）
  cache/       API 响应缓存（24h）
  tmp/         *.part 下载临时文件
  exports/     导出默认目录
```

- 文件名：`年份_第一作者_简化标题.pdf`，非法字符/保留名/超长路径全部处理
- 同一篇论文只会入库一次（DOI → arXiv → 标题 → 模糊标题）
- PDF 只有通过校验才会从 `tmp/*.part` 原子改名进入 `library/`

`PAPERRADAR_HOME` 环境变量可以整体迁移数据目录（多套文献库时很有用）。

---

## 7. 项目结构

```
main.py                 入口：GUI / --cli / --smoke
cli.py                  search / download / audit / stats / export / profile
app/                    main_window.py、workers.py（QThreadPool）、theme.py
ui/                     search_panel、result_table、library_panel、download_panel、log_panel、models、dialogs
models/paper.py         Paper / PdfStatus / PdfCandidate（枚举状态机）
providers/              base（统一接口）、http（限流+重试+缓存）、openalex、semantic_scholar、crossref、arxiv、unpaywall、openreview
services/               search / merge / relevance / oa_finder / download / pdf_validator / library / export / app_service
database/               db.py + schema.sql
utils/                  title_normalizer、filename、rate_limiter、logger、config、venues
tools/                  gui_smoke.py（无头 GUI 自检）、cli_entry.py
tests/                  127 个离线测试
```

关键设计：**providers/services 不依赖 Qt**，所以核心逻辑可以脱离界面测试；GUI 只做展示与调度。

---

## 8. 开发与测试

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q          # 127 passed
.\.venv\Scripts\python.exe tools\gui_smoke.py --out artifacts\gui.png
.\.venv\Scripts\python.exe tools\gui_smoke.py --live --out artifacts\gui_live.png
```

测试覆盖：标题标准化与相似度、Windows 文件名清洗、DOI/arXiv/标题/模糊去重、相关性评分、SQLite 增删改查与去重、PDF 真伪校验（真 PDF / HTML 冒充 / 错论文 / 扫描件 / 截断文件）、本地 HTTP 服务器上的下载全流程（成功 / 403 / HTML / 错论文 / 回退 / 取消）、OA 分层与去重、限流与缓存、CLI 辅助函数。

---

## 9. 已知限制

1. **Semantic Scholar 未认证时极易 429**（共享 IP）。配置 `semantic_scholar_api_key` 可显著改善；失败会自动降级，不影响其他源。
2. **Unpaywall 必须配置邮箱**，否则该层直接跳过。
3. **V1 不做网页爬取**：CVF/PMLR/作者主页/GitHub 仓库那一层（规范里的第 7–11 层）留到 V3，用 Playwright 做，且只作为最后兜底。
4. **雾/云/夜间这类主题的歧义**：关键词检索天然会带回不相关论文，用「排除词 + 研究方向过滤 + 相关性排序」压制，但不可能 100% 干净。
5. **极长的中文/日文标题**在 Windows 下会被截断到安全长度（信息仍保留在数据库和导出里）。

---

## 10. 故障排查

| 现象 | 处理 |
| --- | --- |
| 检索很慢 / 某些源总是失败 | 看日志；S2 429 属正常限流，配置 API key 或直接减少来源 |
| 全是 `PUBLIC_BUT_BLOCKED` | 目标站点反爬（Elsevier/MDPI 常见），用「打开来源页」手动下载后放进 library |
| 全是 `NO_PUBLIC_COPY_FOUND` | 确认填了 `unpaywall_email`；很多遥感期刊论文确实没有 OA 版本 |
| `TITLE_WARNING` | 扫描版 PDF 无文本层，打开肉眼确认即可 |
| 双击 exe 没反应 | 先单独运行 `PaperRadar.exe --smoke smoke.png`，再看 `logs/app.log` |
| 数据要迁移 | 设置环境变量 `PAPERRADAR_HOME=D:\PaperRadarData` |

---

## 11. 许可与合规

本工具只做**合法公开**文献的自动化整理：不绕过任何访问控制，不访问侵权镜像站，不破解验证码，不伪造身份。所有下载行为等同于你在浏览器里点开一个公开 PDF。
