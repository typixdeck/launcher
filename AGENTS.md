# Launcher 开发约定

- 先读 README.md 和 docs/VOICE-CODING.md。以当前 Git checkout 为准，编辑 src/typix_launcher/；不要改系统安装目录或 reference 快照。
- 面向官方 Raspberry Pi OS ARM64，主验收设备 CM4。GTK3 + PyGObject；保持 Desktop 快捷方式筛选、键盘操作与全屏体验。
- 开工先看 git status，干净 main 才 git pull --ff-only；同一任务使用同一分支。保留用户未提交的修改，不自动 reset、强推或用 rsync 覆盖工作区。
- 界面修改优先用 python3 tools/preview.py。该入口使用演示数据，电源、息屏、启动应用、自动启动设置均隔离；它不验证生产设备交互。
- 验证：PYTHONPATH=src python3 -m unittest discover -s tests -v；GTK 会话中运行 tools/check-status-ui.py 检查布局。
- 修改完成只提交相关源码、测试和文档；不提交账号、SSH 密钥、真实设备清单、运行日志或用户数据。
- 代码同步、预览与 deb 安装/发布是不同动作。用户只要求编辑/预览时，不停止现有 Launcher 或安装软件包。电源、MUX、固件写入另按工作区 docs/03-integration-surfaces.md 的设备维护边界处理。
