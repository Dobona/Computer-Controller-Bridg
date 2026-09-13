# Computer Controller Bridge 又名 Codex操（作）伴（侣）

一个运行在 Windows 本机的轻量级电脑控制服务：通过 [MCP](https://modelcontextprotocol.io/)（Model Context Protocol，stdio 传输）把真实的鼠标、键盘与屏幕理解能力暴露给 AI 客户端（如 Codex、Claude 等支持 MCP 的应用）。

> 适合场景：让 AI 助手在受控的本机桌面上完成“看屏幕 → 定位 → 点击/输入 → 验证结果”的自动化操作，例如打开应用、填表、拖拽文件、发送消息、按快捷键保存等。
>
> 注意：本工具可真实操控电脑。请仅在你拥有或已获授权的机器与环境中使用，重要、不可逆的操作（删除、支付、关机等）请务必人工确认。

- 运行平台：Windows 10 / 11（x64）
- 环境要求：Python 3.11+
- 输入后端：Windows SendInput（系统级输入）
- 传输方式：MCP stdio（本机使用，不提供远程访问）

---

## 功能特性

- 鼠标：移动、单击 / 双击 / 右键 / 中键、按下与松开、拖拽、长按、滚动、位置查询
- 键盘：Unicode 文本输入（中文 / emoji）、组合快捷键、单键点按 / 长按
- 屏幕理解：截图 + 结构化读屏（窗口列表、UIA 可交互元素、本地 OCR 文本，均带物理像素坐标）
- 可选外接视觉模型（VLM）：通过任意 OpenAI 兼容的 `chat/completions` 接口做整体描述、图标识别与带坐标的元素定位（默认关闭）
- 组合定位工具 `find_text`：本地 OCR → UIA 名称 → VLM 兜底，一次返回全部匹配位置
- 安全兜底：全局急停热键、动作超时、崩溃后看门狗自动释放按键、操作审计日志
- 一键启停：`start.bat` / `stop.bat`，可选托盘图标、开机自启

---

## 架构概览

```text
AI 客户端 (Codex 等)
        │  MCP (stdio)
        ▼
  MCP 服务层 (FastMCP)          ← 工具注册、参数校验、统一返回
        │
        ├── 核心输入层 (SendInput)
        │    鼠标 / 键盘 / 动作调度（拖拽、长按，可被急停中断）
        ├── 屏幕理解层
        │    窗口枚举 / UIA 元素树 / 本地 OCR / 截图
        ├── 可选外接 VLM 适配器（默认关闭，截图不外发）
        └── 安全层
             急停热键 / 看门狗 / 审计日志
```

所有坐标统一使用**物理像素 + Windows 虚拟桌面坐标系**（主屏左上角为原点，副屏可为负坐标），保证“截图上的位置 = 鼠标落点”。

### 目录结构

```text
computer-controller/
├─ README.md                  使用与开发说明（本文件）
├─ config.example.json        配置模板（复制为 config.json 使用，无密钥）
├─ requirements.txt           运行依赖（版本锁定）
├─ requirements-dev.txt       测试依赖
├─ .gitignore
├─ src/
│  ├─ main.py                 服务入口：自检 → 安全层 → 托盘 → MCP stdio
│  ├─ core/                   鼠标、键盘、截图、窗口、UIA、OCR、动作调度、坐标
│  ├─ server/                 MCP 服务层、参数 Schema、配置加载与校验、日志
│  ├─ safety/                 急停热键、看门狗、审计日志
│  ├─ vision/                 外接 VLM 适配器（默认关闭）
│  └─ ui/                     托盘图标
├─ tests/                     单元测试（154 个用例）
└─ scripts/
   ├─ start.bat / start-admin.bat / stop.bat / install.bat
   └─ step*_check.py 等       真机验证脚本（可选）
```

---

## 快速开始

### 1. 准备环境

安装 [Python 3.11 或更高版本](https://www.python.org/downloads/)，安装时勾选 **Add python.exe to PATH**。

### 2. 获取配置

首次启动前把配置模板复制为实际配置（也可以直接运行 `start.bat`，脚本会自动完成这一步）：

```bat
copy config.example.json config.json
```

`config.json` 已加入 `.gitignore`，**不会也不应提交到仓库**。

### 3. 启动服务

双击运行 `scripts\start.bat`：

- 首次运行会自动创建虚拟环境 `.venv` 并安装依赖（需要几分钟）；
- 启动成功后项目根目录生成 `status.json`，托盘区出现绿色圆点图标；
- 如需控制管理员权限的应用（UAC 弹窗、管理员程序），改用 `scripts\start-admin.bat`。

### 4. 接入 MCP 客户端

在任意支持 MCP（stdio）的客户端中注册本项目，启动命令为：

```text
<项目目录>\.venv\Scripts\python.exe src\main.py
```

工作目录设为项目根目录。以 Codex 为例，配置完成后即可直接对话，例如：

> 打开记事本，输入一段中文，然后按 Ctrl+S 保存。

### 5. 停止服务

双击 `scripts\stop.bat`，或右键托盘图标 → **立即停止**。服务会优雅退出并释放所有按键。

可选：运行 `scripts\install.bat` 创建桌面快捷方式并注册登录自启。

---

## MCP 工具总览

所有工具返回统一结构 `{ok, message, data, duration_ms}`（`screenshot` 直接返回 PNG 图片）。

| 类别 | 工具 | 说明 |
| --- | --- | --- |
| 鼠标 | `mouse_move` | 平滑移动（可被急停中断） |
| 鼠标 | `mouse_click` / `mouse_double_click` / `mouse_right_click` | 单击 / 双击 / 右键 |
| 鼠标 | `mouse_down` / `mouse_up` | 按住 / 松开原语 |
| 鼠标 | `mouse_drag` | 拖拽（可被急停中断） |
| 鼠标 | `mouse_long_press` | 长按（可被急停中断） |
| 鼠标 | `mouse_scroll` | 滚动滚轮 |
| 鼠标 | `cursor_position` | 查询鼠标位置 |
| 键盘 | `keyboard_type` | 输入文本（中文 / emoji） |
| 键盘 | `keyboard_hotkey` | 组合快捷键 |
| 键盘 | `keyboard_key` | 单键点按 / 长按 |
| 屏幕 | `screenshot` | 截取全屏并返回图片 |
| 屏幕 | `screen_info` | 屏幕布局 / DPI 信息 |
| 屏幕 | `screen_analyze` | 结构化读屏：窗口 + 可交互元素 + 文本（含坐标） |
| 屏幕 | `vlm_describe` | 外接 VLM：整体 / 区域描述或结构化元素定位（默认关闭） |
| 屏幕 | `find_text` | 按文本定位：OCR → UIA → VLM 兜底 |
| 其他 | `wait` | 等待（可被急停中断） |
| 安全 | `emergency_stop` | 急停：中止动作、释放按键、拒绝新指令 |
| 其他 | `self_test` | 环境自检（dry-run） |

### 结构化读屏（screen_analyze）

返回内容包含：

- `windows`：可见顶层窗口（标题、类名、矩形、PID、是否前台）；
- `elements`：UIA 可交互元素（按钮 / 输入框 / 菜单等，带名称与坐标）；
- `texts`：本地 OCR 识别的文本（含坐标）；
- `enabled` / `degraded` / `reasons`：本次启用层、请求了但不可用的层及其原因。

定位建议顺序：先用 `screen_analyze` 看窗口 → 用 UIA / OCR 找目标 → 对定位不到的自绘控件 / 图标再用 `vlm_describe(structured=true)`。每次操作前后各观察一次并验证结果，不要假设操作成功。

---

## 配置说明

`config.example.json` 中常用字段：

| 配置项 | 说明 |
| --- | --- |
| `safety.panic_hotkey` | 全局急停热键组合，默认 `Ctrl+Alt+Shift+Esc` |
| `safety.confirm_mode` | 预留的敏感操作确认开关 |
| `input.max_action_duration_s` | 单个动作最大时长（秒），超时自动中止并释放按键 |
| `screen.save_screenshots` | 为 `true` 时截图保存到 `logs/screenshots/` |
| `screen.uia.enabled` / `screen.ocr.enabled` | `screen_analyze` 默认是否启用对应层 |
| `screen.vlm.enabled` | 外接 VLM 总开关（默认 `false`） |
| `screen.vlm.endpoint` | OpenAI 兼容的 VLM 接口地址（默认留空，自行填写） |
| `screen.vlm.api_key` | VLM API 密钥（也可通过环境变量覆盖） |
| `screen.vlm.model` | 视觉模型名（代码内置示例模型名，可替换为其他 OpenAI 兼容模型） |
| `log.level` | 日志级别：debug / info / warning / error |

VLM 密钥支持环境变量覆盖（优先级高于配置文件），避免把密钥写进 `config.json`：

```bat
set SILICONFLOW_API_KEY=your-key-here
```

> **隐私提醒**：启用 `screen.vlm` 后，截图会被发送到你配置的外部端点。该功能默认关闭；请仅在明确知情并同意后开启，并把 API 密钥保存在环境变量或本地 `config.json` 中，不要提交到仓库。

---

## 安全设计

- **急停热键**：任何时刻按 `Ctrl+Alt+Shift+Esc` 立即中止拖拽 / 长按 / 文本输入等动作，释放所有按键与鼠标按钮，并把鼠标移回屏幕中心；再按一次复位。
- **工具级急停**：`emergency_stop` 在任何急停状态下仍可调用，与热键等效。
- **看门狗**：即使服务进程被强杀（例如任务管理器结束），独立看门狗子进程也会根据状态文件自动释放按键，避免“键卡住”。
- **动作超时**：动作默认 30 秒超时自动中止（可配置），防止长时间失控。
- **审计日志**：每次工具调用写入 `logs/audit-YYYYMMDD.jsonl`，便于追溯。
- **权限边界**：普通权限下无法向管理员（更高权限）窗口发送输入（Windows UIPI 限制）；需要控制这类窗口时用管理员方式启动。

---

## 开发与测试

```bat
:: 安装开发依赖（虚拟环境需先由 start.bat 创建）
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

:: 运行全部单元测试
.venv\Scripts\python.exe -m pytest tests -q
```

单元测试不依赖真实屏幕与外部服务（154 个用例）。`scripts/` 下另有面向真实桌面的可选验证脚本（`step*_check.py`、`poc_check.py`），运行时会真实移动鼠标 / 按键，请在无重要任务的桌面上执行；`step13_check.py`、`bench_vlm.py` 需要自行配置 VLM 密钥并会真实调用外部接口、产生少量费用。

### 常见问题

- **提示找不到 Python**：安装 Python 3.11+ 并勾选 “Add python.exe to PATH”。
- **托盘图标不显示**：展开系统托盘“隐藏图标”，或查看 `logs\server.log`；托盘不可用不影响服务。
- **按键卡住 / 服务崩溃后仍有残留**：按急停热键；进程已不在时看门狗会自动释放；仍异常可运行 `stop.bat` 或重启电脑。
- **杀毒软件拦截**：本项目使用系统级 SendInput API，请将项目目录加入信任列表。
- **虚拟 / 远程桌面下拖不动窗口标题栏**：这是注入式输入的环境限制；应用内拖拽、控件拖拽不受影响。

---

## 免责声明

本项目仅用于受控环境下的桌面自动化。使用者须确保拥有对目标机器的操作权限，并对所有操作及其后果负责。作者不对因误用、误操作造成的任何数据损失或安全事件承担责任。
