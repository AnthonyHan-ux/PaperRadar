# PaperRadar 1.1.0 发布检查清单

发布前逐项执行；每项都要记录**真实结果**，不能只勾选。

## A. 代码与测试

- [ ] `git status --short` 为空（工作区干净）
- [ ] `git log --oneline` 包含 baseline 与每个修复的独立 commit
- [ ] `.\.venv\Scripts\python.exe -m pytest tests -q` 全绿（记录数量）
- [ ] 测试不依赖网络：`pytest tests -q -k "not live"` 同样通过
- [ ] 无 `TODO` 遗留关键路径（`grep -r "TODO" services database utils`）

## B. 关键语义

- [ ] `NO_PUBLIC_COPY_FOUND` 论文的 `oa_status` 为 `UNKNOWN`（不是 `CLOSED`）
- [ ] 只有结构化 closed 证据才产生 `PAYWALLED_ONLY`
- [ ] 403 产生 `PUBLIC_BUT_BLOCKED`；超时/5xx 产生 `DOWNLOAD_FAILED`
- [ ] 卡死 provider 不会拖长整轮检索（≤ `provider_timeout × 1.5`）
- [ ] arXiv 限流等待不阻塞 OpenAlex/Crossref

## C. 数据安全

- [ ] 新安装：数据创建在 `%LOCALAPPDATA%\PaperRadar\data`
- [ ] V1.0 沙箱数据自动迁移：论文数、PDF 数不减少
- [ ] 再次启动不重复迁移（`migration.json` 生效）
- [ ] 迁移失败时仍能用旧目录启动（不进入空库）
- [ ] 数据库损坏：隔离为 `*.corrupt.*`，不删除，程序可用
- [ ] 配置损坏：备份为 `*.broken.*.yaml`，回退默认值
- [ ] schema 升级前存在 `paperradar.db.pre-migration.*` 备份
- [ ] worker 结束后数据库文件可重命名（无句柄泄漏）
- [ ] `DB` 冲突（DOI 撞索引）不冒 `IntegrityError` 到 UI

## D. 打包

- [ ] `powershell -ExecutionPolicy Bypass -File build.ps1 -Clean` 成功
- [ ] `dist\PaperRadar\PaperRadar.exe` 存在
- [ ] `dist\PaperRadar\cli\PaperRadarCLI.exe` 存在
- [ ] exe 属性显示 ProductName/FileVersion/ProductVersion = 1.1.0.0 / 1.1.0
- [ ] exe 有图标
- [ ] 打包自检 `--smoke` 通过（build.ps1 自动执行）
- [ ] `PaperRadarCLI.exe --version` 输出 `PaperRadar 1.1.0`

## E. 真实网络验证

- [ ] 多源检索返回结果（OpenAlex/Crossref/arXiv 至少三个源有数据）
- [ ] 真实 OA 解析与下载：`--selftest` 产出 `VALIDATED`
- [ ] PDF 文件存在于 `library\` 且大小 > 10KB
- [ ] 重启 GUI 后论文仍在本地文献库
- [ ] 一个 provider 429 时其他源仍正常返回

## F. 离线与异常

- [ ] 断网启动不崩溃，本地库仍可浏览
- [ ] 断网搜索有明确错误提示，不无限 loading
- [ ] 长路径（>260 字符）下载/复制/导出正常或优雅降级

## G. 文档

- [ ] `README.md` 面向用户可读（安装/数据位置/状态/CLI/迁移/排障）
- [ ] `docs/ARCHITECTURE.md` 数据流正确
- [ ] `docs/MIGRATION.md` 与实现一致
- [ ] `docs/VALIDATION.md` 只写真实执行结果（含日期、版本、命令、输出）
- [ ] `docs/RELEASE_CHECKLIST.md`（本文件）已逐项核对
- [ ] 打包目录包含 `docs\`、`README.md`、`requirements.txt`、`titles_sample.txt`

## H. 收尾

- [ ] 清理测试数据：`%TEMP%` 沙箱、`.selftest-home`
- [ ] 删除 `dist` 中的运行期目录（database/library/logs/config/cache/tmp/exports）
- [ ] release commit：`release: PaperRadar 1.1.0`
- [ ] 最终交付清单与已知限制写入回复
