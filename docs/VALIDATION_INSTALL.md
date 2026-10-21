# 源码安装验证（2026-10-21）

## 环境与范围

从公开仓库匿名下载源码 ZIP，提取到全新目录；应用源码 1.3.1 的 120 个文件逐一核对 Git blob 哈希一致。创建全新 Python 3.12.15 x64 虚拟环境，不继承既有环境的依赖和个人配置。后续只修改本次安装说明，不改变应用运行代码。

本次在 Windows 上实际验证源码安装和启动，没有重新构建 exe，也没有把测试生成的数据、日志或 PDF 放进仓库。

## 实际结果

| 检查 | 结果 |
| --- | --- |
| 依赖安装 | 官方 PyPI 的一次 Qt 大包下载不完整，哈希校验失败并中止；改用清华镜像后完整安装成功，未跳过 TLS 或哈希校验 |
| `python -m pip check` | `No broken requirements found.` |
| `python -m PyInstaller --version` | `6.10.0`，这里只检查构建工具可加载 |
| `python main.py --cli --version` | `PaperRadar 1.3.1` |
| `python -m pytest tests -q` | 297 passed，16.35 秒 |
| `python main.py --home <独立目录> --smoke <截图路径>` | 真实 Windows GUI 正常渲染、创建 schema v4 测试库并自动退出，进程退出码 0 |
| README 的联网检索示例 | OpenAlex、Crossref、arXiv 各返回 20 条记录；合并及筛选后 54 篇候选；Semantic Scholar 返回 429，错误保留并显示；总耗时约 15.6 秒 |

联网示例使用空白个人配置，不含 API Key：

```powershell
.\.venv\Scripts\python.exe main.py --cli search -q "medical image segmentation" --limit 20
```

`--limit` 控制各来源的检索量，合并后的总数可能超过该值。54 篇是这一次服务返回下的候选数量，不是人工相关性评审或总体召回率，也不证明每篇都能下载公开全文。

## 本次修正的说明问题

- 写清 Windows x64、Python 3.12 和 Python Launcher 的前提，解释 `No suitable Python runtime found`。
- 区分源码 ZIP、源码启动命令和编译后的 exe；提供 GitHub 直连失败时的 ZIP 获取方式。
- 将源码 CLI 示例改为已有入口 `main.py --cli`。直接运行模块文件 `cli.py` 不会启动 CLI，之前的示例会静默退出，现已纠正。
- 加入经过实际安装验证的镜像重试命令，以及隔离数据目录的启动检查。

## 结果边界

这次验证不等于所有 Windows 版本、处理器架构、网络和未来依赖版本都已验证。联网来源、公开 PDF 链接和限流政策可能变化。首次安装仍需要 Python 和依赖；找到检索候选不保证能取得公开全文。

完整步骤见 [Windows 源码安装指南](INSTALL_WINDOWS.md)。
