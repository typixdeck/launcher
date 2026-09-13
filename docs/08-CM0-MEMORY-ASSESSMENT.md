# CM0 512MB 内存评估

## 结论

旧 GTK3 Python Launcher **有希望适配 CM0 512MB**，但不能按 CM4 的完整体验直接移植。

技术路线见 [ADR-0001](09-ADR-PYTHON-VS-RUST.md)：先做 Python 行为保持型重构和 CM0 lite 优化，不用 Rust 重写首版 UI。Python + GTK3 本身比 Electron/Chromium 轻很多；真正的压力点是小屏图形会话、应用网格控件数量、图标/截图解码、App Store catalog 体积，以及安装时 `apt`/`dpkg` 的额外内存。

因此 CM0 采用 `lite` 模式，目标是“可用、稳定、低内存”，不是完整视觉体验。

## 旧 Launcher 的有利条件

当前参考实现有几个对 CM0 友好的设计，后续必须保留：

1. **UI 与被启动应用不同时常驻**
   - 旧 supervisor 启动 UI。
   - 用户选择应用后，UI 进程退出并写入 request 文件。
   - supervisor 启动目标应用。
   - 目标应用退出后，UI 才重新启动。
   - 因此运行 Chromium、游戏或开发工具时，Launcher UI 的内存会被释放。

2. **没有常驻浏览器或 Node runtime**
   - 技术栈是 Python 3 + PyGObject + GTK3。
   - 不使用 Electron、Chromium、WebView。

3. **桌面目录使用事件监听**
   - `Gio.FileMonitor` 监听 `.desktop` 目录变化。
   - 不做高频轮询。

4. **实现规模小**
   - 参考实现约 530 行 Python。
   - 依赖主要是系统 GTK/PyGObject。
   - UI 冷启动时加载一次 CSS 与当前图标。

## 内存压力点

| 压力点 | 风险 | CM0 约束 |
| --- | --- | --- |
| 图形会话 | Labwc、输入、合成、 framebuffer/CMA 都会占用内存 | 目标基础会话 ≤180MB RSS 总量，需真机确认 |
| GTK Python Shell | Python 解释器、PyGObject、GTK 控件 | 目标 ≤80MB RSS |
| 应用 tile 数量 | `Gtk.FlowBox` 子控件和 Python wrapper 会随入口数量增长 | 每页最多 30 个 tile，增量加载 |
| 图标解码 | 图标文件过大时解码可能远大于显示尺寸 | 使用预缩放 48px 图标；拒绝超尺寸图片 |
| Store 截图 | 多张截图最容易造成内存尖峰 | CM0 禁用截图 |
| catalog JSON | 大 catalog 一次读入会产生完整对象树 | 分页/按需索引；限制单文件大小 |
| apt/dpkg 安装 | 安装事务可能额外占用数十到一百多 MB | 串行事务；安装时进入极简进度页 |
| 应用本身 | 浏览器、IDE、多媒体应用可能超过 CM0 能力 | Registry 必须标记不适用或实验性 |

512MB 物理内存还可能被 GPU/CMA、firmware 预留、zram 配置和内核参数影响。Linux 用户空间看到的总内存不一定等于 512MB，不能只看型号。

## CM0 内存预算

这是设计预算，不是已验收数据；必须用真机或等效限制环境测量后修正。

| 项目 | CM0 lite 目标 |
| --- | --- |
| Launcher UI 空闲 RSS | ≤80MB |
| Store 额外占用 | ≤40MB |
| 图形会话 + Launcher 总 RSS | ≤250MB |
| 安装事务期间 UI RSS | ≤50MB |
| 保留给应用/内核/文件缓存 | ≥150MB |
| 单个图标显示尺寸 | 48×48 |
| 单页 tile 数 | ≤30 |
| 截图 | 禁用 |
| 并发下载 | 1 |
| 并发外部命令 | 1 |
| 后台索引 | 禁用 |

安装或升级时可以销毁商店列表页，切换到只含标题、进度条、按钮和错误摘要的极简页面，降低 UI 与 apt 同时运行时的压力。

## 必须实现的 CM0 策略

1. **Profile 驱动**
   - `coreFamily == cm0` 或可用内存不足时使用 `lite`。
   - 探测失败时使用 `safe`，只显示本地应用。

2. **分页渲染**
   - 本地应用和商店应用都分页。
   - 每页最多 30 个 tile。
   - 搜索使用防抖和增量更新。
   - 不把数千个 widget 一次性插入 `Gtk.FlowBox`。

3. **图片限制**
   - 图标 48×48。
   - Registry 预先生成小图标。
   - 拒绝超过限定像素或字节大小的图片。
   - 不加载截图、宣传图和高清主题。

4. **Catalog 限制**
   - CM0 只下载必要字段。
   - 首版可以仍下载完整 catalog，但必须设置最大体积。
   - 后续演进为分页 index 或字段裁剪 API。
   - catalog 解析后立即丢弃未使用的多语言长文本。

5. **安装事务串行**
   - 同时只允许一个下载、一个 apt/dpkg 事务。
   - 安装时关闭其他 Store 操作。
   - 显示“请勿断电”与可恢复错误。
   - 不在安装期间渲染大列表。

6. **保留旧 supervisor 优点**
   - 普通应用启动前退出 Launcher UI。
   - 应用退出后再恢复 Launcher。
   - 避免目标应用与 Launcher 争内存。

7. **应用兼容声明**
   - `minMemoryMB` 必须真实。
   - CM0 只显示兼容应用。
   - 不兼容应用只显示原因，不提供安装按钮。

## 测量方法

在目标设备上执行：

```bash
systemd-run --user --scope -p MemoryHigh=300M -p MemoryMax=350M \
  /usr/bin/typix-launcher --ui
```

采集：

```bash
systemctl --user status typix-launcher.service
ps -eo pid,rss,vsz,comm,args --sort=-rss | head -30
free -h
cat /proc/meminfo
grep -E 'MemTotal|MemAvailable|SwapTotal|SwapFree|Cached|AnonPages' /proc/meminfo
```

实验室测试可用 cgroup 或容器模拟限制，但 GPU/CMA、Wayland 合成和真实 I/O 仍必须最终在 CM0 真机验证。

## 判定标准

CM0 支持不能只看“能启动”。验收必须同时满足：

1. 图形会话 + Launcher 稳定运行 30 分钟无 OOM。
2. 100 个本地 `.desktop` fixture 下分页正常，RSS 不超过预算。
3. mock Store 浏览、搜索、安装、卸载不触发 OOM。
4. 启动一个第三方应用时 Launcher UI 退出，应用可用内存明显恢复。
5. 断网、catalog 签名失败、apt lock、低磁盘状态均不崩溃。
6. 没有高频 CPU 轮询和后台索引。

## 风险判断

- **旧 Launcher 本体**：中低风险，预计可优化到 CM0 可用。
- **完整 App Store UI**：中高风险，必须 lite 化。
- **图片密集型 Store**：高风险，CM0 禁用。
- **apt 安装事务**：中高风险，必须串行并减少并发 UI。
- **大型应用兼容**：由 Registry 硬性过滤，不由用户试错。
