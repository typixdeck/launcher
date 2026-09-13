# TypixDeck Launcher UI 规格

## 1. 设计约束

UI 必须延续当前 CM4 真机已经运行的 GTK3 Launcher，而不是重新设计一套陌生界面。

| 项目 | 基线 |
| --- | --- |
| 工具链 | Python 3 + PyGObject + GTK 3 |
| 窗口 | 无边框、全屏、启动时抢占焦点 |
| 默认逻辑尺寸 | 800×600，随真实屏幕自适应 |
| 布局 | Header / 可滚动应用网格 / Footer |
| 网格 | 宽度 ≥700px 为 3 列，<700px 为 2 列 |
| 应用 tile | 最小 180×150，圆角 20px，图标 56px |
| 主题 | 深色底、青色 focus、低动画 |
| 输入 | QMK 键盘、方向键优先，同时支持触摸 |
| 会话 | Labwc Wayland，兼容 XWayland 应用 |

禁止引入 Chromium、Electron、Node.js、WebView 或常驻浏览器作为 UI 壳。

## 2. 视觉 token

以下值来自当前真机参考 CSS，优先保持不变：

```text
background/root      #071018
foreground           #eef8ff
title                #f5fbff
muted                #8da6b8
accent/focus         #43d5ff
tile background      #0d1d29
tile border          #1c3747
tile hover bg        #122937
tile hover border    #3b6d82
button background    #102331
button border        #274354
button hover bg      #17384a
danger text          #ffced2
danger border        #74414a
danger hover border  #ff6673
```

排版：

- 字体：`Noto Sans CJK SC` → `Noto Sans` → system sans-serif。
- 标题：30px / 800。
- 副标题、提示、数量：14px。
- 应用名：18px / 700，超长省略。
- 分类：13px / 600，青色。
- Header / Footer 高度以 48px 按钮为最低触控高度。
- 全局禁用复杂 transition；CM0 模式禁用所有非必要动画。

## 3. 页面结构

保持 Launcher 的单页桌面快捷方式网格；添加 `typix-store` 的桌面 `.desktop` 快捷方式后，它出现在网格中，点击时同样走 supervisor 退出/恢复流程。

```text
┌──────────────────────────────────────────────┐
│ TypixDeck 标题/副标题       N 个应用 刷新 设置 │
├──────────────────────────────────────────────┤
│                                              │
│                应用 tile 网格                 │
│                3 columns × scroll             │
│                                              │
├──────────────────────────────────────────────┤
│ 快捷键提示                    息屏 重启 关机    │
└──────────────────────────────────────────────┘
```

### 应用页

- 标题为 `TypixDeck`，副标题为 `选择桌面快捷方式开始`。
- 只展示系统桌面目录中的 `.desktop` 快捷方式；不展示全部已安装应用。
- tile 内容：图标、名称、中文分类。
- 按目录优先级和名称排序。
- Header 的 `刷新` 保留，快捷键 F5 保留。
- `设置` 键盘可达，快捷键 F9。

### Store 入口

- Store 是独立 `typix-store` 包和独立 GTK 进程，不把 WebView 嵌入 Launcher。
- Store 图标、名称、分类来自标准 `.desktop` 文件。
- 用户点击 Store tile 时，Launcher UI 先退出；Store 退出后 Launcher UI 恢复。
- 所有应用主窗口默认全屏；确认、文件选择与授权对话框保持正常尺寸，所有全屏自有应用提供返回 Launcher 的按钮与快捷键。
- Store 内部列表/详情视觉可复用本文件的 tile 尺寸、焦点和状态规范。

### 设置

第一版提供 Launcher 自身的关键会话设置：

- `开机自动启动`：
  - 读取 `systemctl --user is-enabled typix-launcher.service`。
  - 开启执行 `systemctl --user enable typix-launcher.service`。
  - 关闭执行 `systemctl --user disable typix-launcher.service`。
  - 不请求 root，不写 sudoers，不修改用户 Home 中硬编码路径。
- `systemctl` 不存在或用户 session bus 不可用时，开关禁用并显示原因。
- 保存失败必须保留原状态并弹出错误详情。

后续 Store channel、缓存清理和设备能力摘要归 `typix-store` 设置页，不混入 Launcher v1。

## 4. 详情对话框

尺寸：

- 800×600 屏幕：约 720×520。
- 更小屏幕：全屏。
- 无传统窗口装饰，顶部保留返回按钮。

结构：

```text
返回    应用名                   版本 / 状态
─────
图标   摘要
       开发者 · 许可 · 体积 · 兼容性
       描述
       权限与系统影响
       更新日志
[安装/更新/打开/卸载]  [取消]
```

安装前必须展示：

- 下载体积与安装后体积。
- 将额外安装的依赖。
- 是否需要设备权限：串口、输入、音频、后台服务。
- 是否只兼容 CM4/CM5 或也支持 CM0。
- 电源状态未知或过低时的警告。

## 5. 键盘交互

保留现有按键：

| 按键 | 行为 |
| --- | --- |
| ←/→/↑/↓ | 网格与电源区移动 |
| Enter / Space / KP_Enter | 激活焦点项 |
| Home / End | 到第一个 / 最后一个 tile |
| F5 | 刷新应用网格 |
| F9 | 打开 Launcher 设置 |
| F10 | 进入电源按钮区 |
| F11 / Escape | 恢复全屏或关闭覆盖层 |
| 任意键 / 触摸 | 唤醒屏幕 |

新增按键：

| 按键 | 行为 |
| --- | --- |
| F9 | 打开设置页 |
| Back | 关闭设置对话框 |

搜索框聚焦时，方向键行为由文本光标优先；Escape 返回列表。搜索、分类和 Store 页快捷键属于独立 Store 应用，不改变 Launcher v1 的单页模型。

## 6. Store 状态视觉

- 可安装：普通 tile，主按钮青色描边。
- 已安装：右上角细描边标签。
- 可更新：分类行显示 `可更新`，Header 显示数量。
- 下载中：按钮变为不可用，显示百分比。
- 安装中：显示不确定进度条，不阻塞已安装应用列表滚动。
- 失败：红色描边标签；重试入口在详情页。
- 不适用：降低前景透明度，点击后只显示原因。

进度文案必须区分：

- 正在下载 manifest
- 正在下载软件包
- 正在校验 SHA-256
- 正在解析依赖
- 正在安装
- 正在刷新桌面入口
- 正在卸载

## 7. 空状态与错误

| 状态 | 文案 |
| --- | --- |
| 无本地应用 | 桌面还没有应用快捷方式；添加 .desktop 快捷方式后会显示在这里 |
| 商店离线 | 商店暂不可用，已安装应用不受影响 |
| catalog 验签失败 | 目录签名无效，已禁止安装 |
| 磁盘不足 | 存储空间不足，需要清理后重试 |
| 架构不匹配 | 此包不支持当前设备架构 |
| CM0 内存不足 | 此应用需要更多内存，CM0 不适用 |
| apt 忙 | 系统正在维护软件包，请稍后重试 |

错误对话框必须包含可复制的技术摘要，例如 app id、manifest revision、HTTP 状态、校验结果、apt 退出码。

## 8. 无障碍与低资源

- 所有可交互控件必须可聚焦。
- 焦点宽度至少 3px。
- 触控目标至少 48×48。
- 文本不得只靠颜色表达状态。
- 图标缺少时使用 `application-x-executable`。
- CM0：
  - 关闭截图加载。
  - 图标统一 48px。
  - 列表增量插入。
  - 不显示装饰性背景图。
  - Store 页面一次最多渲染 30 个 tile。
