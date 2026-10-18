# PaperRadar 1.1.0 真实验证记录

本文件只记录**实际执行过**的验证。未执行的项在最后一节明确列出。

- 日期：2026-10-19
- 版本：PaperRadar 1.1.0（exe FileVersion 1.1.0.0）
- 环境：Windows 11（NT 10.0.22631），PowerShell 5.1，Python 3.11.7，SQLite 3.43.1，PySide6 6.7.3
- 打包方式：PyInstaller 6.10.0，onedir

---

## 1. 测试套件

```
.\.venv\Scripts\python.exe -m pytest tests -q
223 passed in 36.85s
```

覆盖：标题标准化/指纹、Windows 文件名与长路径、四层去重、模糊分桶、评分、SQLite（去重/冲突合并/连接生命周期/schema 迁移/损坏隔离）、PDF 校验（真 PDF / HTML / 错论文 / 扫描件 / 截断）、本地 HTTP 下载（成功 / 403 / 402 / 451 / 500 / 断网 / 回退 / 取消）、OA 分层、限流并发、provider fixture 解析、AppService 端到端、数据迁移、配置损坏恢复、CLI。

## 2. 打包产物

```
dist\PaperRadar\PaperRadar.exe           6,263,765 bytes
dist\PaperRadar\cli\PaperRadarCLI.exe
```

- 文件属性：`ProductName=PaperRadar`，`FileDescription=PaperRadar Academic Literature Radar`，`FileVersion=1.1.0.0`，`ProductVersion=1.1.0`
- `PaperRadarCLI.exe --version` → `PaperRadar 1.1.0`
- `build.ps1` 内置的 `--smoke` 自检通过（退出码 0）

## 3. 打包 GUI 真实下载（关键项）

命令（`--home` 指向临时沙箱，`--selftest-doi` 固定论文，保证可复现）：

```
PaperRadar.exe --home <temp>\pr_packaged_selftest ^
  --selftest <repo>\artifacts\packaged_selftest.json ^
  --selftest-doi 10.1109/lgrs.2025.3590959
```

结果（JSON 摘要）：

```json
{
  "ok": true,
  "query": "10.1109/lgrs.2025.3590959",
  "papers": 1,
  "downloaded": [{
    "title": "M 2 CD: A Unified MultiModal Framework for Optical-SAR Change Detection ...",
    "status": "VALIDATED",
    "path": "...\\library\\2025\\GRSL\\2025_Liu_m_2_cd_unified_multimodal_framework_optical_sar_change.pdf",
    "exists": true
  }]
}
```

- 下载来源：arXiv（OA 解析层命中 arXiv + OpenReview）
- 文件大小：973,166 bytes
- 校验结果：`VALIDATED`（首页包含 DOI/arXiv ID，标题相似度 100.0）
- 落盘目录：`library\2025\GRSL\`（说明 venue 归一化把 IEEE GRSL 认成 `GRSL`）
- 该路径经过真实的 `MainWindow → TaskRunner → DownloadBridge → DownloadPanel` 代码

## 4. 批量审计（打包 CLI）

```
PaperRadarCLI.exe --home <temp> audit --titles examples\titles_sample.txt --out <temp>\audit.csv --resume
```

结果（3 条标题）：

| # | 匹配 | pdf_status | 说明 |
| --- | --- | --- | --- |
| 1 | 100.0 | `NO_PUBLIC_COPY_FOUND` | 无任何合法公开 PDF |
| 2 | 88.5（标题验证 98.3） | `VALIDATED` | arXiv 下载 973,166 bytes |
| 3 | 100.0 | `PUBLIC_BUT_BLOCKED` | 公开链接被 403 拒绝 |

三态分类与 V1 一致，且 `audit.csv` 逐行落盘、`--resume` 可续跑。

## 5. 旧数据迁移（V1.0 → V1.1）

构造 V1.0 布局沙箱（`tools/make_legacy_sandbox.py`）：3 篇论文 + 3 个 PDF + research_profile + 旧 config，放在打包目录内（模拟旧安装）。

```
LOCALAPPDATA=<temp>\localappdata legacy_app\PaperRadar.exe --smoke smoke.png
```

结果：

```
migrated tree: cache config database exports library logs migration_backup_20261019_002155 tmp migration.json
papers: 3
rows: Legacy Sandbox Paper 0/1/2  (pdf_status=VALIDATED, doi 保留)
migrated pdfs: 3
legacy pdfs still there: 3
profile: name: Migrated From V1.0
schema: database ready ... (schema v3)   ← 启动时自动补齐 title_key
packaged CLI stats: 论文总数 3，本地 PDF 3（已验证 3）
second run: marker unchanged=True（未重复迁移）
```

- 迁移前自动创建 `migration_backup_<时间>` 备份
- 旧目录文件**未被删除**
- `migration.json` 幂等生效

## 6. 数据库安全

- 损坏数据库（写入随机字节后启动）：被改名为 `paperradar.db.corrupt.<时间>`，以空库启动，程序可用（`tests/test_recovery.py`）
- 配置损坏：`user_config.broken.<时间>.yaml` 备份 + 默认值回退（同上）
- schema 迁移：V1 schema 数据库自动补 `title_key` 并回填；迁移前存在 `paperradar.db.pre-migration.*`
- worker 结束后数据库文件可被重命名（无句柄泄漏，`tests/test_db_connections.py`）
- DOI 大小写混写不会绕过唯一索引（写库统一规范化）
- DOI 撞索引时自动合并到 canonical 行，不抛 `IntegrityError`（`tests/test_db_conflicts.py`）

## 7. 超时、限流与 OA 语义

- 卡死 provider 测试：`provider_timeout_seconds=0.2` 时整轮检索 `< 0.9s` 返回，其他 provider 结果正常（`tests/test_search_deadline.py`）
- 限流并发测试：arXiv（0.4s 间隔）等待时 OpenAlex 调用 `< 0.2s` 返回（`tests/test_rate_limiter_concurrency.py`）
- 无 OA 证据：`oa_status=UNKNOWN` + `pdf_status=NO_PUBLIC_COPY_FOUND`（单测 + AppService 集成测试）
- 明确的 closed 证据：`PAYWALLED_ONLY`（AppService 集成测试）
- 403 → `PUBLIC_BUT_BLOCKED`；500/断网 → `DOWNLOAD_FAILED`（本地 HTTP 服务器测试）

## 8. 真实网络（多源）

- 真实检索（OpenAlex + Crossref + arXiv 三源均返回，Semantic Scholar 因 429 自动降级）：
  ```
  合并后 24 篇 ... 用时 9.3s；来源：OpenAlex 10，Crossref 10，arXiv 10；失败：Semantic Scholar
  ```
- 最近一次 `--selftest` 搜索（DOI 模式）：1 篇结果，OA 层依次命中 arXiv、OpenReview 后下载成功。
- S2 429 未被误报为程序错误，其他源结果完整返回。

## 9. 离线与异常

- 断网（HTTP(S)_PROXY 指向关闭端口）：
  ```
  合并后 0 篇 ... 用时 17.0s；失败：OpenAlex，Semantic Scholar，Crossref，arXiv
  search exit=0
  ```
  搜索不崩溃、不无限 loading，退出码 0 并在日志/状态栏给出原因。
- 离线启动 GUI `--smoke` 成功渲染。
- 离线 `stats` 正常读取本地数据库（不依赖网络）。

## 10. 长路径

- 190 字符深的 literature root 下真实创建 PDF 成功，最终路径 `< 260` 字符（`tests/test_long_path.py`）
- 超长文件名被截断至 `MAX_PATH` 预算内，目录部分不动
- `\\?\` 前缀 helper 在 `>250` 字符时生效

## 11. 未验证 / 不适用

- **1 万篇以上**真实库的去重性能：只有 900 条合成数据的分桶测试（单次查找 < 0.5s）。
- **真实 99 篇**用户语料审计：尚未拿到用户的标题文件，只跑了 3 条样例。
- Windows 10（只在 Windows 11 22631 验证）。
- 代理服务器（本机只测了「代理不可用」场景）。
- 未配置真实 `unpaywall_email` / `semantic_scholar_api_key` 的启用路径（无凭据）。
- OCR（V1.1 不包含）。
