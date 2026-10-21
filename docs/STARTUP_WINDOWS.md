# Windows 启动说明

1.3.1 程序安装在 `%LOCALAPPDATA%\Programs\PaperRadar-1.3.1`，桌面 `PaperRadar` 快捷方式和项目根目录 `Start-PaperRadar.bat` 均指向此处。数据仍按原规则保存在 `%LOCALAPPDATA%\PaperRadar\data`，或用户明确指定的 `PAPERRADAR_HOME`。旧版程序保留；若旧版正在打开，请退出后再从桌面入口启动新版。

2026-10-21 排查发现，桌面源码项目目录继承了 Low Mandatory Level 标签。该目录中的 exe 启动后为低完整性进程，无法写入正常用户数据目录；相同构建从工作目录启动则为中完整性。现使用普通用户程序目录安装，不修改目录 ACL、完整性标签或系统安全设置。桌面的源码和旧程序备份保留。

本机编译的程序没有数字签名，SmartScreen 可能显示“Windows 已保护你的电脑”。确认目标是此安装中的 PaperRadar 后，可选择“更多信息”→“仍要运行”。此操作与首次启动后的数据目录权限问题不同；不需要关闭 Defender 或 SmartScreen，也不需要管理员权限。

如果再次启动失败，双击 `Check-PaperRadar.bat`。它使用独立临时目录启动真实窗口并截图，成功应显示 `PASS`。失败会保留 `stdout.log`、`stderr.log` 和测试数据，显示其所在路径；不会使用真实文献库或个人密钥。临时测试不会检索网络。

1.3.0 增加检索选项和日历筛选，数据库升级至 v4 保存日期。非空旧库先备份，备份失败时停止升级。安装本身不打开真实数据库；升级发生在你首次运行新版时。
