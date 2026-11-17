# 项目接手入口 (START_HERE)

> 最后更新：2026-11-18
> 本文件只记录**当前有效**的权威状态。接手顺序：先读本文件与 `ARCHITECTURE.md`，再看 `HANDOFF.md` 顶部最新 `[记忆胶囊]`。

## 1. 项目定位与技术栈
- **项目名称**：`screenwriter-kb`（编剧灵感保险箱）
- **一句话目标**：本地优先、零云端上传的多题材小说拆解知识库、AI 去油「精话锁」、本地 8-gram 原创度查重与多支线互动影游编剧工作台。
- **技术栈 / 运行环境**：
  - Python 3.12（64 位），使用 `uv` 管理虚拟环境与依赖
  - 数据存储：SQLite + FTS5（配合 `rjieba` 中文分词）+ 本地 Markdown 梗卡/导出文件
  - Web 可视化：FastAPI + Uvicorn + 零外部 CDN 原生 HTML5/CSS3/Vanilla JS（`127.0.0.1:8765`）
  - AI 协议：标准 `stdio` MCP Server（兼容 `mcp` 1.x 与 2.x `MCPServer` 回退），内置 `novel-craft-feeder` Skill

## 2. 快速启动与验证命令
以下均在 `screenwriter-kb/` 目录执行：

- **安装 / 同步依赖**：
  ```powershell
  uv sync --python 3.12 --all-extras
  ```
- **运行全链路自动化测试（临时隔离目录，不污染正式库）**：
  ```powershell
  uv run --python 3.12 pytest -v
  ```
- **初始化正式库并一键接通 Codex / Claude Desktop / Antigravity 三端**：
  ```powershell
  uv run --python 3.12 python scripts/connect_ai_clients.py
  ```
- **启动本地可视化工作台（默认 `http://127.0.0.1:8765`）**：
  - 方式一：双击 `D:\ScreenwriterKB\打开可视化知识库.cmd` 或 `screenwriter-kb\启动可视化面板.cmd`
  - 方式二：终端运行 `uv run --python 3.12 swkb web`
- **常用 CLI 命令**：
  - `uv run --python 3.12 swkb stats`：查看当前知识库统计与抽屉列表
  - `uv run --python 3.12 swkb lock-check "满屋杀气瞬间卡壳"`：命令行运行精话锁体检
  - `uv run --python 3.12 swkb mcp`：启动标准 `stdio` MCP 服务
- **主入口文件**：
  - CLI：`src/swkb/cli.py`（`swkb.cli:main`）
  - MCP Server：`src/swkb/mcp_server.py`（`swkb.mcp_server:main`）
  - Web 工作台：`src/swkb/web_api.py`（`swkb.web_api:app` / `run_server`）

## 3. 当前有效状态摘要
- **端到端测试**：`tests/test_e2e.py` 共 7 个全链路测试全部通过（`7 passed`），覆盖 7 大抽屉预置/新建、`.txt` 与伪 `.docx` 真实小说读取及第 18 章后防盗坏章识别、试菜 ✓/✗ + FTS5 检索 + 按行回读、精话锁拦截与动态新增规则、原创度查重、互动影游死胡同诊断与导出、FastAPI `TestClient` 全核心接口 200 与 12 个 MCP 工具函数调用。
- **正式本地保险箱（`D:\ScreenwriterKB`）**：已初始化为干净空库（占用约 140.52 KB），预置 7 大类型抽屉、26 条「精话锁」初始黑名单规则、1 个 4 节点 3 选项互动影游演示项目（`works` 表为空，等待用户自行喂书）。
- **三端连接**：已同步配置 Codex（`~/.codex/config.toml`）、Claude Desktop（`%APPDATA%\Claude\claude_desktop_config.json`）、Antigravity（`~/.gemini/config/mcp_config.json`）的 `screenwriter-kb` MCP 与 `novel-craft-feeder` Skill（均已留存 `.bak` 备份）。
- **尚未验证 / 边界说明**：本地桌面客户端（Codex / Claude Desktop / Antigravity）重启后实际拉起 stdio MCP 子进程的 UI 交互未在 GUI 中手动点击测试；可视化前端交互通过 FastAPI `TestClient`、DOM ID 校验与 `node --check` 验证，未驱动真实桌面浏览器点击。

## 4. 红线与注意事项（不可破坏）
- **环境变量 `SWKB_DATA_DIR`**：默认指向 `D:\ScreenwriterKB`。任何自动化测试或实验必须覆盖 `SWKB_DATA_DIR` 或传入临时 `db_path`，严禁直接写入正式库。
- **严禁污染仓库**：绝对不要将 `D:\ScreenwriterKB` 内的个人小说、`kb.sqlite`、`.venv` 或包含 API 密钥的本地客户端配置提交进 Git。
- **Windows 批处理编码**：所有 `.cmd` 脚本必须保持 **CRLF 换行 + UTF-8 无 BOM**（否则 Windows `cmd.exe` 解析首行 `@echo off` 或中文路径会报错）。
- **防盗坏章隔离**：绝对不能从 `chapters.is_corrupted = 1` 的章节中抽对白、建人物或做原创度比对基线。
