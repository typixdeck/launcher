# Launcher / Store 开发路线图

## 当前状态（2026-09-11）

已完成：

- CM4 旧 Launcher 参考快照与行为文档。
- `apps/launcher/src/typix_launcher/`：
  - `.desktop` 发现与 Exec 展开；
  - 原子启动请求；
  - supervisor 退出/恢复；
  - Header 设置入口；
  - systemd 用户级开机自启开关。
- 独立 `apps/store/src/typix_store/` 初版：
  - catalog schema 校验；
  - CM0/CM4/CM5 能力过滤；
  - artifact SHA-256/deb 元数据校验；
  - `pkcon install-local` 授权路径；
  - 远程 catalog 获取被签名配置硬阻塞。
- Launcher/Store/Reader/Gamer/MyAI 均可构建 deb。
- 单元测试覆盖 desktop 解析、autostart 命令、supervisor 恢复顺序、Store 兼容性和 Reader 本地格式。

未完成：

- CM4 真机恢复在线后的安装与 UI 回归。
- minisign/公钥轮换。
- Registry 服务端与发布 CI。
- Store 更新/卸载/失败恢复。
- 设备能力探测服务。

## M1：行为保持型重构

状态：**代码完成，待真机验收**。

必须验证：

- 相同 fixture 下应用顺序、名称、分类与旧实现一致。
- 键盘导航、触摸、中文 locale、损坏 `.desktop`、无效图标不崩溃。
- UI 与应用切换的事件顺序固定为 `ui-start -> ui-quit -> app-run -> ui-start`。
- Labwc Wayland 全屏和焦点抢占符合旧体验。

## M2：Launcher v1 收口

状态：**开发中**。

任务：

- 打磨设置页错误提示和状态刷新。
- 验证只显示桌面快捷方式，以及应用默认全屏与退出恢复。
- 补充 CM0 lite 模式：限制 tile 数量、关闭后台监控批量刷新、图标 48px。
- 在官方 Raspberry Pi OS ARM64 基础镜像验证 deb 依赖。
- 提供从旧 `typixnode-launcher` 的只读迁移说明，不改用户数据。

验收：CM4 真机安装 deb 后无需手工复制脚本即可启动。

## M3：Store Alpha

状态：**本地安全模型完成一部分，远程链路阻塞**。

任务：

1. 实现 minisign catalog 验签和两把公钥轮换。
2. Registry URL allowlist、重定向策略、过期处理。
3. 安装/更新/卸载状态机。
4. 下载取消、低存储、apt lock、电源状态保护。
5. 安装后验证 `.desktop` 是否出现。
6. 建立本地 HTTPS mock registry 与篡改 fixture。

验收：签名、哈希、架构、包名、版本任一不匹配都拒绝安装；断网不影响本地应用启动。

## M4：CM4 真机 Alpha

任务：

- 安装五个当前 deb。
- 采集冷启动、空闲内存、应用启动、Supervisor 恢复和 Store 事务数据。
- 验证 F9 开机自启开关。
- 验证电源、息屏、唤醒、触摸和 QMK 键盘。
- 验证 Store/Reader/Gamer/MyAI 均能让 Launcher 先退出、后恢复。

## M5：Registry 服务端与发布工具

任务：

- static catalog 生成器与原子发布。
- ARM64/Architecture-all 包 lint。
- 签名、测试、 changelog、回滚。
- 开发者接入文档。

## M6：CM5 / CM0 矩阵

- CM5：完整功能、图形性能、风扇策略不干预。
- CM0：lite UI、兼容过滤、后台任务单并发、内存/存储预算。
- CM0 模拟环境必须有失败用例，不允许只测 happy path。

## 建议顺序

1. 真机在线后先跑 `tools/collect-device.sh`，不部署。
2. 在可回滚镜像安装 `typix-launcher`，验证 v1。
3. 逐个安装 Store/Reader/Gamer/MyAI，验证 supervisor。
4. 完成 Store 签名链后才开始服务器和发布自动化。
