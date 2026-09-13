# ADR-0001：Launcher 主体继续用 Python/GTK3，不做首版 Rust 重写

日期：2026-09-11

状态：已接受

## 背景

当前 CM4 真机 Launcher 已经使用 Python 3 + PyGObject + GTK3 实现，并且交互、CSS、systemd、Labwc 集成已经跑通。TypixDeck 还需要支持 CM0 512MB 内存，因此需要评估是否应该用 Rust 重写以降低内存。

## 结论

1. **需要重构**：把旧单文件脚本拆成可测试模块，保留现有行为和视觉布局。
2. **不做首版整仓 Rust 重写**：M1–M5 的 Launcher UI 继续使用 Python 3 + PyGObject + GTK3。
3. **允许局部使用 Rust**：优先考虑把未来的 `typix-store-helper`、catalog 校验器或离线诊断工具做成独立 Rust 小工具。
4. **只有实测不达标才重新评估 UI 语言**：CM0 优化后仍无法满足内存/启动预算，才启动 Rust UI 原型对比。

## 原因

### Python 不是当前最大内存项

Launcher 内存大致来自：

1. GTK / GdkPixbuf / Pango / 图形栈。
2. Wayland/Labwc 合成与 framebuffer/CMA。
3. 图片解码和 tile 数量。
4. Python 解释器与 PyGObject wrapper。
5. Store catalog、下载队列、apt/dpkg 事务。

其中 1–3 与 UI 语言无关；5 与 Launcher 语言无关。Rust 主要减少第 4 项，通常只能带来有限的十几到几十 MB 级别收益，不足以弥补首版重写成本。

### 旧架构已有内存优势

旧 supervisor 在启动第三方应用前会让 UI 进程退出，应用退出后再恢复 Launcher。因此运行用户应用时，Python Launcher 不会继续占用 GUI 内存。这个行为必须保留。

### 现有代码保真度更重要

当前需求明确要求沿用真机 Launcher 的布局和行为。PyGObject 可以直接迁移：

- GTK3 widget 结构
- CSS provider
- `Gio.FileMonitor`
- `Gio.DesktopAppInfo`
- GLib idle/timeout 事件
- Wayland/XWayland 会话行为

Rust 虽有 GTK3 绑定，但生态重心已转向 GTK4；换成 Rust/GTK4 会同时改变 widget、CSS、事件和打包链路，容易偏离“保持现有布局”的目标。

### 安全边界可以拆开处理

未来安装 deb 需要 root 权限。这个部分不应该做成常驻 root Python GUI，也不应该用宽松 shell 参数。无论 UI 用什么语言，都应拆成短生命周期 helper。Rust 适合承担这种小型、强校验、低依赖的 helper，但不要求 UI 一起重写。

## 首版重构方案

继续 Python，但必须模块化：

```text
src/typix_launcher/
├── desktop.py        .desktop 扫描、解析、排序、去重
├── supervisor.py     UI/应用切换状态机
├── device.py         能力探测与 UI profile
├── ui/               GTK3 页面和控件
├── store/
│   ├── registry.py   catalog 下载、验签、缓存
│   ├── model.py      数据模型与兼容性判断
│   └── installer.py  下载队列与 helper 协议
└── power.py          logind/polkit 边界
```

重构原则：

- 先提取纯逻辑并加测试，再动 UI。
- 不改变用户可见布局。
- 不引入常驻后台索引。
- CM0 分页、小图标、禁截图。
- 安装时销毁商店大列表，切换极简进度页。

## 何时重新评估 Rust UI

满足以下条件时才启动对比原型：

1. Python 版已实现 CM0 lite 模式。
2. 分页、图片限制、catalog 裁剪均已实现。
3. CM0 或等效 cgroup 测试仍超出预算：
   - Launcher UI RSS > 80MB；
   - Store 附加 RSS > 40MB；
   - 安装事务期间 UI RSS > 50MB；
   - 冷启动超过 8 秒。
4. `smaps_rollup` / PSS 显示 Python wrapper 和解释器占比确实高。
5. Rust 原型使用同一 GTK3 布局和同一 fixture，而不是换成 GTK4 后比较不同 UI。

## 测量要求

不能只比较 RSS，因为 GTK 共享库会重复计入。至少采集：

```bash
cat /proc/$PID/status
cat /proc/$PID/smaps_rollup
ps -eo pid,rss,vsz,pss,comm,args --sort=-rss
free -h
```

重点看：

- RSS
- PSS
- USS/PSS 差异
- GPU/CMA 预留
- swap/zram
- 图形会话总内存
- 冷启动时间

## 局部 Rust 候选

| 组件 | 是否适合 Rust | 原因 |
| --- | --- | --- |
| GTK Launcher UI | 暂不适合 | 迁移成本高，当前保真要求强 |
| `typix-store-helper` | 适合 | 短生命周期、权限敏感、需要严格参数和协议解析 |
| catalog 签名校验器 | 适合 | 独立、低依赖、可复用 |
| 设备诊断 CLI | 可选 | Python 也足够，发布体积要求高时再换 |
| 下载器 | 不急 | Python/GIO 可满足首版；Rust 需要额外 TLS/HTTP 依赖 |

## 决策结果

先做 Python 行为保持型重构和 CM0 lite 优化；等实测数据出来后再决定是否局部或进一步替换。不要在没有内存 profile 的情况下直接重写整个 Launcher。
