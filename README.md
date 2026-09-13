# TypixDeck Launcher

面向官方 Raspberry Pi OS ARM64 的原生全屏启动器，主验收设备为 CM4。只显示用户桌面上的 `.desktop` 快捷方式；保持深色 GTK3、约 800×600 逻辑布局、三列应用网格和键盘导航。启动应用后释放 Launcher 界面，应用关闭后返回桌面。

- 平台：官方 Raspberry Pi OS ARM64 Bookworm / Trixie，兼容性与可用功能按运行时探测。
- UI：Python 3 + PyGObject + GTK3；禁止 WebView/Electron/Node。
- Store：由独立 `typix-store` 包提供；在桌面添加其 `.desktop` 快捷方式后出现在 Launcher 中。
- 内存策略：进入应用前 UI 先退出，应用期间只保留轻量 supervisor；资源不足时按实际能力降级。

<!-- app-screenshots:start -->

![桌面快捷方式首页与键盘焦点](docs/screenshots/home.png)

桌面快捷方式首页与键盘焦点。

![开机自动启动设置](docs/screenshots/settings.png)

开机自动启动设置。

<!-- app-screenshots:end -->

## 一键安装 Launcher 和 Store

在官方 **Raspberry Pi OS ARM64 Bookworm / Trixie** 的普通桌面用户终端中运行：

```sh
curl -fsSL https://raw.githubusercontent.com/typixdeck/launcher/main/install.sh | sh
```

脚本同时安装完整的 `typix-launcher` 和 `typix-store` deb；按 `dpkg` 架构、系统版本和官方镜像标记探测兼容性，不按 CM 型号硬编码。需要系统 Python 3、`dpkg`、`apt`、`sudo` 和 HTTPS CA 证书；签名组件 `python3-cryptography` 缺失时，会通过普通的 apt 授权流程安装，应用运行依赖也由 apt 解析。sudo 和 apt 保留正常终端提示，GUI 始终以普通用户运行。

想先审查脚本或只检查、不安装：

```sh
curl -fsSL https://raw.githubusercontent.com/typixdeck/launcher/main/install.sh -o install.sh
less install.sh
sh install.sh --check
# 检查通过后安装
sh install.sh
```

`--check` 会真实下载两包，验证签名、元数据、磁盘空间、已安装版本并执行 apt 模拟；不调用 sudo，不修改软件包、配置、桌面或服务。检查模式缺少签名组件时会明确报错，不自行安装。`sh install.sh --help` 查看选项。

下载固定使用 `typixdeck/store` 仓库 `main/debs/`，不接受 catalog 指定任意下载地址。脚本内置 Ed25519 公钥，验证 catalog 的原始字节、仓库、频道与有效期，再核对两包的完整包标记、字节数、SHA-256、Package / Version / Architecture / Depends / 系统兼容字段。授权阶段将文件复制到只有 root 能修改的临时目录，重新验证后才交给 `apt-get --no-remove install`；拒绝降级和未完成的 dpkg 事务。公钥不从本次下载中获取；安装脚本本身的初始信任来自上面的 Launcher GitHub HTTPS 地址，建议需要审查时先下载再执行。

首次安装仅在路径缺失时建立 `/usr/share/typix-store/keys/bootstrap.pem` 和 `/etc/typix-store/github.json`。已有等价源与匹配公钥可继续使用；冲突的自定义源或公钥会保留并中止安装，要求管理员先检查。安装器保留现有配置、用户数据和自定义桌面快捷方式，只在缺失时添加 Store 桌面入口，不添加 Launcher 自身 tile。

默认不启动、重启或启用 Launcher 服务，避免覆盖正在使用的应用。准备好后运行：

```sh
systemctl --user start typix-launcher.service
# 也可以明确选择在安装完成后启动：
sh install.sh --start
```

开机启动仍由 Launcher 设置页（`F9`）控制。同版本再次运行会重新验证，apt 保持已安装版本；更高的已安装版本不会被降级。下载有大小、读取超时与空间限制，断网或取消后可重新运行；包事务开始后请等待 apt 完成，安装器不会强制终止 dpkg，也不会自动重放失败事务。

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

该设置不需要 root 或 sudoers。包安装后首次手动体验：

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

## 真机验证

已在 CM4 Labwc / Wayland 会话验证桌面快捷方式筛选、全屏显示、键盘启动及应用关闭后的 Launcher 恢复。应用主窗口请求全屏，文件选择、确认和授权对话框保持正常尺寸。电源与息屏能力取决于系统可用接口，应在目标镜像上单独验证。

通用全屏采用启动时的 Wayland foreign-toplevel v3 请求，支持 Labwc 下的原生 Wayland 与 XWayland 窗口，不更改 compositor 全局规则。包装启动脚本可用 `X-TypixDeck-FullscreenAppId` 指定实际 app ID，见 [全屏策略](fullscreen.md)。

本仓库只发布当前应用代码、构建文件和可公开文档；历史设备快照、凭据、设备采集记录及本地运行数据不在提交范围内。

## 仓库目录

| 路径 | 用途 |
| --- | --- |
| `app.json` | 应用描述、完整 deb 版本与 SHA-256、截图索引 |
| `README.md` | 功能、真机截图、安装与使用说明 |
| `src/` | 当前程序源码或启动入口 |
| `packaging/` | desktop 与打包辅助文件 |
| `tests/` | 功能与边界验证 |
| `docs/screenshots/` | 可公开的真实运行截图 |
| `build-deb.sh` | 本地构建入口 |
| `dist/` | 构建生成的完整 deb；不提交 Git |

Launcher 保留 `packaging/debian/`、`config/`、`registry/` 与 `install.sh` 的现有入口。安装脚本及其授权行为见上方“一键安装”一节。

构建后核对并更新 `app.json` 的版本、SHA-256 和截图索引。Store 发布工具读取声明并校验完整软件包；构建不会自动签名、上传或安装。 发布格式见 [应用仓库约定](https://github.com/typixdeck/store/blob/main/docs/APP-REPOSITORY.md)。应用仓库不包含用户数据、凭据、私钥或设备采集记录。
