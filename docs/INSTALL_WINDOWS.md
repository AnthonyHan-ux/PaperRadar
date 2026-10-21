# Windows 源码安装指南

适用环境：Windows 10/11 x64、64 位 Python 3.12。本仓库提供源码；GitHub 下载的源码 ZIP 不包含 exe。

## 1. 安装前提

安装 [Python 3.12](https://www.python.org/downloads/windows/) 的 Windows x64 版本，保留 Python Launcher（`py`）。若使用 Git 克隆，还需安装 [Git for Windows](https://git-scm.com/downloads/win)。

在 PowerShell 检查：

```powershell
py -3.12 --version
```

应显示 `Python 3.12.x`。出现 `No suitable Python runtime found` 表示该版本未安装或未被 Launcher 识别；先修复 Python 安装。如果只配置了 `python` 命令，先用 `python --version` 确认是 3.12，再把下一节创建虚拟环境的命令换成 `python -m venv .venv`。

## 2. 获取源码

两种方式任选其一：

- Git：`git clone https://github.com/AnthonyHan-ux/PaperRadar.git`，然后 `cd PaperRadar`。
- 浏览器：仓库页面选择 Code → Download ZIP，完整解压；在包含 `README.md`、`main.py` 和 `requirements.txt` 的文件夹打开 PowerShell。解压目录通常叫 `PaperRadar-main`。

GitHub 连接失败属于网络问题，可改用 ZIP 或稍后重试。

## 3. 创建环境并安装依赖

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

首次安装需要下载 Qt 等较大的依赖，请等待命令成功结束。遇到超时、下载不完整或哈希校验失败，重新下载；也可使用镜像：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

保留 TLS 和哈希校验。虚拟环境中的依赖只属于此项目，不要求激活环境或调整 PowerShell 执行策略。

## 4. 启动与检索

```powershell
.\.venv\Scripts\python.exe main.py
```

窗口打开后可搜索 `medical image segmentation`，先保留默认条件，确认有候选结果，再逐项增加方向、期刊和日期限制。已收录的中英别名可扩展为英文；自定义中文不保证被通用翻译。没有结果时查看结果区的来源状态和筛选说明。

默认模板不含他人的 API Key。基础检索可以先不配置个人凭据；Unpaywall 全文补查需要填写自己的邮箱，Semantic Scholar 的 API Key 是可选项。所有来源仍受网络、服务额度及限流影响；检索结果存在不代表一定能取得公开 PDF。

命令行等价用法：

```powershell
.\.venv\Scripts\python.exe main.py --cli --version
.\.venv\Scripts\python.exe main.py --cli search -q "medical image segmentation" --limit 20
```

`--limit` 控制各来源的检索量，多来源合并后的总数可能超过该值。

以后启动时回到此目录，再运行 `main.py` 即可，不必每次重新安装依赖。

## 5. 验证安装

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe main.py --home "$env:TEMP\PaperRadar-startup-check" --smoke "$env:TEMP\PaperRadar-startup.png"
```

最后一条命令用独立数据目录打开窗口、截图并退出；截图应出现在系统临时目录。它不会使用个人文献库。测试和图形启动不能替代联网检索检查。

## 6. 数据与已有安装

正常运行的数据默认保存在 `%LOCALAPPDATA%\PaperRadar\data`，包含数据库、下载的 PDF、个人配置和日志；备份时保留整个目录。API 凭据放在个人配置中，不要写入仓库默认模板或提交到 Git。

`Start-PaperRadar.bat` 和 `Check-PaperRadar.bat` 用于已有的 `%LOCALAPPDATA%\Programs\PaperRadar-1.3.1` 程序安装。直接克隆源码的用户按本指南启动；若需要自己生成 exe，按 README 的打包章节操作。
