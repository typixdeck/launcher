# TypixDeck Launcher

面向 TypixDeck CM4/CM5/CM0 的原生全屏 Launcher。第一版从 CM4 真机正在运行的 `typixnode_launcher.py` 迁移，保持深色 GTK3、800×600、三列 FlowBox、键盘优先和电源栏体验；同时把扫描、设置和 supervisor 拆成可测试模块。

- 平台：Raspberry Pi OS / Debian 13 ARM64，优先官方镜像和驱动。
- UI：Python 3 + PyGObject + GTK3；禁止 WebView/Electron/Node。
- Store：由独立 `typix-store` 包提供；在桌面添加其 `.desktop` 快捷方式后出现在 Launcher 中。
- 内存策略：进入应用前 UI 先退出，应用期间只保留轻量 supervisor；CM0 继续走降级配置。

## 当前实现

```text
src/typix_launcher/
├── desktop.py       FreeDesktop 入口发现、Exec 展开、原子启动请求
├── settings.py      systemd 用户级 autostart 状态与命令
├── supervisor.py    UI 启动、前台应用等待、UI 恢复
├── app.py           CM4 旧布局迁移 + 设置入口
└── typix-launcher.css
```

行为：

1. `typix-launcher --supervisor` 启动 `typix_launcher --ui`。
2. 用户激活 tile 时，UI 先原子写入 `.desktop` 路径，再调用 `quit()` 退出。
3. supervisor 读取请求并展开 Exec，启动前台应用。
4. 应用退出后 supervisor 重新启动 UI。
5. UI 崩溃或无请求退出时，supervisor 延后重启，systemd 只在 supervisor 自身失败时介入。

测试 `test_supervisor.py` 明确断言事件顺序是：

```text
ui-start -> ui-quit -> app-run -> ui-start
```

## 开机自动启动

包内安装用户级 unit：

```text
/usr/lib/systemd/user/typix-launcher.service
```

Launcher Header 新增 `设置` 按钮，快捷键 `F9`。设置页提供 `开机自动启动` 开关，底层只执行用户级授权命令：

```bash
systemctl --user enable typix-launcher.service
systemctl --user disable typix-launcher.service
```

该设置不需要 root、sudoers 或修改 `/home/pi`。包安装后首次手动体验：

```bash
systemctl --user daemon-reload
systemctl --user start typix-launcher.service
# 打开 Launcher -> 设置 -> 开机自动启动
```

包默认不强制 enable，避免覆盖用户选择。

## 桌面入口扫描

只扫描系统桌面目录的 `*.desktop` 快捷方式。通过 `xdg-user-dir DESKTOP` 获取路径（兼容中文“桌面”或自定义桌面位置），命令不可用时回退到 `~/Desktop`。

不扫描用户或系统的 `applications` 目录，也不因安装了软件就自动加入 Launcher。普通 Linux 应用只需把标准 `.desktop` 快捷方式放到桌面，无需注册 Store。删除桌面快捷方式只会移除 Launcher 入口，不会卸载软件。

支持复制的 `.desktop`、符号链接，以及 Link 指向的本地绝对路径和 `file://` URL。保留 `Hidden`、`NoDisplay`、`TryExec`、本地化 Name/Comment、`X-TypixNode-Exclude` 和 `X-TypixDeck-Exclude` 过滤。旧的多目录环境变量不再用于扩展扫描范围。

## 构建

```bash
./build-deb.sh
```

产物：

```text
dist/typix-launcher_0.2.0-1_all.deb
```

运行依赖：

```text
python3, python3-gi, gir1.2-gtk-3.0, xdg-user-dirs
```

推荐安装 `wlopm` 以支持息屏/唤醒。

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

覆盖 catalog 样例、desktop 解析、autostart 命令和 supervisor 恢复顺序。

## 文档索引

1. [产品需求](docs/01-PRD.md)
2. [系统架构](docs/02-ARCHITECTURE.md)
3. [UI 规格](docs/03-UI-SPEC.md)
4. [App Store Registry 与打包](docs/04-APPSTORE-REGISTRY.md)
6. [开发路线图](docs/06-ROADMAP.md)
8. [CM0 512MB 内存评估](docs/08-CM0-MEMORY-ASSESSMENT.md)
9. [Python vs Rust 技术决策](docs/09-ADR-PYTHON-VS-RUST.md)

## 待真机验收

- CM4 Labwc Wayland 下全屏、焦点、触摸和键盘导航。
- Store/Reader/Gamer/MyAI 通过 supervisor 启动后，Launcher 是否完全退出并在应用退出后恢复。
- `systemctl --user enable/disable` 与图形会话 D-Bus 环境。
- `wlopm` 息屏/唤醒。
- 桌面快捷方式添加/删除时刷新；`applications` 目录的增减不影响 Launcher。
- 应用启动默认全屏，退出后回到 Launcher；文件选择、确认和授权对话框保持正常尺寸。

通用全屏采用启动时的 Wayland foreign-toplevel v3 请求，支持 Labwc 下的原生 Wayland 与 XWayland 窗口，不更改 compositor 全局规则。包装启动脚本可用 `X-TypixDeck-FullscreenAppId` 指定实际 app ID，见 [全屏策略](fullscreen.md)。

本仓库只发布当前应用代码、构建文件和可公开文档；历史设备快照、凭据、设备采集记录及本地运行数据不在提交范围内。
