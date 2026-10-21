# Windows 启动说明

## 刚克隆仓库或下载源码 ZIP

请先按 [Windows 源码安装指南](INSTALL_WINDOWS.md) 安装 64 位 Python 3.12 和项目依赖，再运行 `.\.venv\Scripts\python.exe main.py`。源码 ZIP 不包含 exe，也不会自动创建桌面快捷方式。

## 已有编译安装

本仓库附带的 `Start-PaperRadar.bat` 和 `Check-PaperRadar.bat` 寻找 `%LOCALAPPDATA%\Programs\PaperRadar-1.3.1\PaperRadar.exe`。只有安装到该位置时才能使用这两个入口。自行打包后也可直接运行生成目录中的 `PaperRadar.exe`，并完整保留 `_internal` 等依赖文件。

数据默认保存在 `%LOCALAPPDATA%\PaperRadar\data`；`PAPERRADAR_HOME` 或 `--home` 可指定其他数据目录。退出旧版窗口后再启动新版。数据库 v4 升级在首次打开旧库时发生；非空库先备份，备份失败则停止升级。

## 启动排查

`Check-PaperRadar.bat` 适用于上述已有安装，使用独立临时目录启动真实窗口并截图。成功显示 `PASS`；失败保留诊断日志并显示位置，不会使用真实文献库。

源码用户可运行安装指南中的 `--smoke` 命令。正常日志位于 `%LOCALAPPDATA%\PaperRadar\data\logs\app.log`，或指定数据目录下的 `logs\app.log`。

未签名的本地构建可能触发 SmartScreen。若出现“Windows 已保护你的电脑”，先核对下载来源和程序；这与 Python 缺失或数据目录权限不足是不同问题。不要为运行本程序关闭系统安全保护。若日志显示数据目录写入被拒绝，请使用自己有写权限的数据目录，保留错误日志供排查。
