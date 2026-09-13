# TypixDeck Launcher + App Store 产品需求（PRD）

## 1. 背景与目标

TypixDeck 目前的 CM4 真机已经运行一个 GTK3 全屏 Launcher：深色三列应用网格、支持 `.desktop` 扫描、键盘启动和电源操作。本项目不是从零重写一套陌生桌面环境，而是沿用这套已验证的交互与视觉，补齐设备化应用商店能力。

项目目标：

1. 保留现有“开机即进入 Launcher、启动应用后返回”的使用方式。
2. 继续发现并展示 Raspberry Pi OS / Debian 传统桌面应用。
3. 增加 TypixDeck App Store：浏览、搜索、查看详情、下载、安装、更新、卸载面向 TypixDeck 优化的 deb 应用。
4. 以 CM4 真机为主要开发和验收平台，同时为 CM5 与 CM0 定义兼容/降级策略。
5. 保持低资源占用：GTK3 + PyGObject，不引入 Electron、Chromium、WebView 或常驻浏览器。
6. 基于 Raspberry Pi OS 官方镜像和官方驱动栈开发，不要求用户为了 Launcher 换发行版或自编内核。

## 2. 用户与场景

### 手持设备最终用户

- 打开设备后直接看到应用网格。
- 用触摸、QMK 键盘方向键或 Enter 打开应用。
- 在 Store 中安装已适配的应用，不需要命令行。
- 断网时已安装应用必须继续可用。

### TypixDeck 应用开发者

- 能把应用打包为 ARM64 deb。
- 能声明应用对 CM0/CM4/CM5、内存、GPU、显示、输入、音频、电源的兼容性。
- 能通过 Registry 发布稳定版、测试版和开发版。

### 系统维护者

- Launcher 自身也以 deb 和 systemd unit 部署。
- 失败时可 SSH 诊断，日志可在 `journalctl --user` 查看。
- 不需要手动复制散落在 Home 目录的脚本。

## 3. 平台基线

| 平台 | 定位 | 最低体验 |
| --- | --- | --- |
| CM4 | 主开发与验收平台 | 完整 Launcher + App Store；800×600/1024×768 逻辑界面；键盘、触摸、音频可用 |
| CM5 | 一等兼容目标 | 完整 Launcher + App Store；允许更高分辨率、更大缩放和更强图形应用 |
| CM0 | 降级兼容目标 | 同一 UI 与数据模型，但强制低资源模式；关闭动画、缩略图和重型索引；Store 只加载必要元数据 |

官网公开配置中 CM0 为 Quad A53 @1GHz / 512MB，CM4 为 Quad A72 @1.5GHz / 2–8GB，CM5 为 Quad A76 @2.4GHz / 4–16GB。设备实际能力仍以运行时探测为准。

## 4. 功能需求

### M1：Launcher 基线保留

- 全屏、无边框启动。
- 只扫描系统桌面目录中的 `.desktop` 快捷方式（`xdg-user-dir DESKTOP`，回退 `~/Desktop`），不扫描任何 `applications` 目录。
- 遵守 FreeDesktop 规则：跳过 `Type != Application`、`Hidden=true`、`NoDisplay=true`。
- 保留 `X-TypixDeck-Exclude=true` 或兼容的 `X-TypixNode-Exclude=true` 排除字段。
- 显示本地化 Name、Comment/分类、图标。
- 激活应用时 Launcher UI 必须先退出；应用退出后 supervisor 自动恢复 Launcher。
- 应用主窗口启动后默认全屏；文件选择、确认和授权对话框保留可用的正常尺寸。自有应用提供键盘和触控可达的返回/退出入口。
- 设置页提供“开机自动启动”开关，默认不强制覆盖用户选择。
- 息屏、重启、关机入口保留，但新实现必须使用 logind/polkit 授权，不使用常驻 root。

### M2：App Store 客户端

- Store 页面展示 Registry 中的应用：名称、摘要、分类、图标、版本、大小、兼容性。
- 支持分类浏览、关键字搜索、详情页。
- 支持下载和安装 `.deb`。
- 支持检查更新、显示已安装/可升级状态。
- 支持卸载 Registry 安装的应用。
- 安装、升级、卸载前显示明确确认，安装中显示进度和可取消策略。
- 网络失败、校验失败、依赖失败、磁盘不足、电源不足时给出可理解错误。
- 已下载 deb 必须校验 SHA-256/大小；Registry manifest 必须验签。
- Store 不自动上传设备标识、安装列表或使用统计。

### M3：传统应用兼容

- 普通 Linux 应用不必进入 Registry；用户将其 `.desktop` 快捷方式添加到桌面后即可出现在 Launcher。
- Registry 应用安装后也通过标准 `.desktop` 暴露，不引入私有启动数据库。
- Store 将安装状态与桌面快捷方式分别管理；安装本身不等于显示在 Launcher。
- Store 状态只作为增强元数据；`.desktop` 缺失或损坏时不能拖垮 Launcher。

### M4：设备适配

- 启动时探测 CM 型号、内存、存储、架构、OS release、显示会话、输入和音频。
- Registry 条目按能力过滤：
  - 硬性不兼容：不显示或显示“不适用”。
  - 软性受限：显示“可安装但体验受限”。
- CM0 自动启用低资源模式。
- CM4/CM5 使用完整模式。
- 应用可声明后台服务、设备权限、非 GUI 组件，Store 必须在安装前提示。

## 5. 非目标

- 不替代 Raspberry Pi OS 桌面、文件管理器或系统设置。
- 不做 Flatpak、Snap、AppImage、容器或云端游戏串流协议。
- 不做 root 常驻 GUI，不绕过 Debian 包依赖。
- 不在第一版做账号体系、评论、评分、推荐算法或遥测。
- 不直接控制电池充放电、硬断电、固件刷写或屏幕 MUX。
- 不强制所有系统应用都经过 TypixDeck 审核。

## 6. 关键验收标准

1. CM4 真机上从官方镜像基础环境安装 Launcher deb 后，用户可在设置中开启 Labwc 图形会话自动进入 Launcher；关闭后不自动启动。
2. 至少 20 个系统/用户 `.desktop` 应用能被扫描、分页/滚动、键盘启动。
3. 启动一个 GUI 应用并退出后，Launcher 自动重新显示。
4. Store 在 mock Registry 上完成浏览、搜索、下载、安装、启动、更新、卸载闭环。
5. manifest 被篡改、deb 校验失败、架构不匹配时拒绝安装。
6. 断网后已安装应用和 Launcher 正常使用，Store 显示离线状态。
7. CM0 模拟环境（限制 512MB 内存）下 UI 能启动，空闲 RSS 不超过项目定义的性能预算。
8. Store 安装操作没有常驻 root；授权路径和日志可审计。

## 7. 成功指标

- Launcher 冷启动到可交互：CM4 ≤ 3 秒，CM0 目标 ≤ 8 秒。
- Launcher 空闲 RSS：CM4 ≤ 120MB，CM0 目标 ≤ 90MB。
- Store 浏览页加载后额外 RSS：CM4 ≤ 80MB，CM0 目标 ≤ 50MB。
- 应用启动到 Launcher 恢复无死锁。
- 桌面入口变化在 2 秒内刷新，不使用高频轮询。
