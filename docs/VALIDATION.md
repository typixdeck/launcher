# 验证说明

主验收环境是官方 Raspberry Pi OS ARM64 的 CM4、GTK3 与 Labwc。Launcher 默认仅显示 Desktop 快捷方式；Store 与 Reader 原生全屏，正常关闭后返回 Launcher。

在仓库根目录执行 `./build-deb.sh` 构建；有 tests 的应用执行 `PYTHONPATH=src python3 -m unittest discover -s tests -v`。Store 的公开签名应用源由仓库中的 deb 发布工具构建，安装通过系统授权。不会上传设备标识、安装清单或使用统计。

设备采集记录和用户数据不随源码发布。
