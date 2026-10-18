# 从 V1.0 升级到 V1.1（数据迁移）

## 1. 为什么要迁移

V1.0 把运行数据放在**程序目录**：

```
<程序目录>\database\paperradar.db
<程序目录>\library\...
<程序目录>\config\config.yaml, research_profile.yaml
```

这与源码包 `database/` 同名，曾经导致「清理运行数据时把源码包一起删掉」。V1.1 把所有运行数据迁到：

```
%LOCALAPPDATA%\PaperRadar\data\
```

程序目录因此可以只读（Program Files、U 盘、只读分发目录都能运行）。

## 2. 自动迁移做了什么

首次启动 V1.1 时（且 `%LOCALAPPDATA%\PaperRadar\data\migration.json` 不存在）：

1. **检测旧目录**：程序目录（源码运行时还有项目根目录），条件是存在 `database\paperradar.db`、含 PDF 的 `library\`，或（冻结版）`config\research_profile.yaml`。
2. **备份**：在 `%LOCALAPPDATA%\PaperRadar\data\migration_backup_<YYYYmmdd_HHMMSS>\` 复制旧数据库与 config。
3. **数据库**：用 SQLite 在线备份 API（WAL 安全）复制到新位置，然后：
   - `PRAGMA quick_check` 必须返回 `ok`；
   - 新旧论文数量必须一致。
   任一检查失败 → 中止迁移，新库删除，旧目录保持原样。
4. **文献库**：复制（**不移动、不删除**）`library\`，并核对 PDF 数量。
5. **配置**：复制 `research_profile.yaml`；把旧的 `config.yaml` 复制为 `user_config.yaml`（V1.1 仍会读取旧 `config.yaml` 作为兜底）。
6. **导出目录**：复制 `exports\`。
7. **写标记**：`migration.json`（包含时间、来源、论文数、PDF 数）。

迁移完成后，**旧目录原封不动地保留**，你可以确认无误后自行删除。

## 3. 幂等与失败

- `migration.json` 存在 → 直接跳过，不会重复搬运。
- 迁移失败 → 程序**继续使用旧目录**（不进入空库），错误写入 `logs\app.log`，下次启动会重试。
- 目标数据库已存在但有标记缺失 → 保留现有数据库，只补文献库/配置，不覆盖。

## 4. 手动迁移（可选）

如果自动迁移不可用，可以手动执行：

1. 关闭 PaperRadar。
2. 创建 `%LOCALAPPDATA%\PaperRadar\data`。
3. 把旧 `database\`、`library\`、`exports\` 整个复制进去。
4. 把旧 `config\research_profile.yaml` 复制到 `...\data\config\`。
5. （可选）把旧 `config\config.yaml` 改名为 `user_config.yaml`。
6. 启动程序；若 `migration.json` 不存在，程序会再检查一次，但不会覆盖已有数据库。

## 5. 回滚

迁移不做破坏性操作，回滚很简单：

1. 关闭程序；
2. 删除 `%LOCALAPPDATA%\PaperRadar\data`（或改名保留）；
3. 把程序目录换回 V1.0 版本，数据仍在程序目录。

## 6. 迁移后检查清单

- [ ] GUI 顶部「本地文献」数量与旧版一致
- [ ] `library\` 下 PDF 数量一致（可用 `dir /s /b *.pdf | find /c ".pdf"` 粗查）
- [ ] `我的方向` 内容仍是你自己的关键词
- [ ] `user_config.yaml` 里的 API 邮箱/Key 还在
- [ ] `stats` 输出的 `VALIDATED` 数量没有减少

## 7. 不迁移的内容

| 内容 | 原因 |
| --- | --- |
| `cache\` | 纯 API 缓存，可重新生成 |
| `logs\` | 历史日志，按需自行保留 |
| `tmp\*.part` | 未完成的下载，重新下载即可 |
