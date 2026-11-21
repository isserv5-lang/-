# 项目接手入口 (START_HERE)

> 最后更新：2026-11-21
> 本文件只记录**当前有效**的权威状态。接手顺序：先读本文件与 `ARCHITECTURE.md`，再看 `HANDOFF.md` 顶部最新 `[记忆胶囊]`。

## 1. 项目定位与技术栈
- **项目名称**：`screenwriter-kb`（编剧灵感保险箱）
- **权威代码路径**：`D:\Antigravity\zahuo-repo\screenwriter-kb`（Git 仓库根：`D:\Antigravity\zahuo-repo`；严禁写回旧 C 盘路径）
- **一句话目标**：本地优先、零云端上传的多题材小说拆解知识库（支持**一书多标签 + 单梗精准归位防串味**）、AI 去油「精话锁」、本地 8-gram 原创度查重与多支线互动影游编剧工作台。
- **技术栈 / 运行环境**：
  - Python 3.12（64 位），使用 `uv` 管理虚拟环境与依赖（缓存与临时目录固定放 D 盘：`UV_CACHE_DIR=D:\ScreenwriterKB\.uv-cache`、`TEMP=D:\ScreenwriterKB\.tmp`）
  - 数据存储：SQLite + FTS5（配合 `rjieba` 中文分词）+ 本地 Markdown 梗卡/导出文件
  - Web 可视化：FastAPI + Uvicorn + 零外部 CDN 原生 HTML5/CSS3/Vanilla JS（`127.0.0.1:8765`）
  - AI 协议：标准 `stdio` MCP Server（13 个工具，兼容 `mcp` 1.x 与 2.x `MCPServer` 回退），内置 `novel-craft-feeder` Skill

## 2. 快速启动与验证命令
以下均在 `D:\Antigravity\zahuo-repo\screenwriter-kb` 目录执行（建议先设置 D 盘缓存环境变量）：

```powershell
$env:UV_CACHE_DIR="D:\ScreenwriterKB\.uv-cache"
$env:TEMP="D:\ScreenwriterKB\.tmp"
```

- **安装 / 同步依赖**：
  ```powershell
  uv sync --python 3.12 --all-extras
  ```
- **运行全链路自动化测试（临时隔离目录，不污染正式库）**：
  ```powershell
  uv run --python 3.12 pytest -v
  ```
- **初始化/升级正式库并一键接通 Codex / Claude Desktop / Antigravity 三端**：
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
- **端到端测试**：`tests/test_e2e.py` 共 9 个全链路测试 100% 全绿通过（`9 passed`），完整覆盖三大主频 45 标签与 v1→v2→v3 平滑升级、真实小说与伪 `.docx` 防盗坏章识别、试菜 ✓/✗ + FTS5 检索 + 按行回读、精话锁拦截与动态新增规则、原创度查重、互动影游死胡同诊断与导出、FastAPI 全核心接口与 13 个 MCP 工具、**一书多标签自动识别（含真实长篇《玄学小祖宗》实测）、`PUT /api/works/{id}/tags` 增删改签、试菜跨标签防串味归位与 `/api/graph/universe` 的 `work_tag_links`**。
- **正式本地保险箱（`D:\ScreenwriterKB`）**：已通过 `scripts/connect_ai_clients.py` 完成 `work_tags` 建表与三频标签同步，保持干净空库（`works=0`、`work_tags=0`），预置 3 大主频、45 个平台精准标签、26 条「精话锁」初始黑名单规则、1 个 4 节点 3 选项互动影游演示项目。
- **Antigravity 多标签动态星图工作台（`http://127.0.0.1:8765`）**：基于 D 盘仓库运行中，支持主标签发光实线弧线 + 副标签柔和虚线跨列弧线、悬停书本跨频点亮所有归属标签与频道、喂小说弹窗勾选 0~4 个副标签与 `✦ AI 自动识别副标签` 开关、书籍抽屉内联增删标签、试菜卡片右上角 `⇄` 一键切换单条对白归属标签防串味。
- **三端连接**：Codex（`~/.codex/config.toml`）、Claude Desktop（`%APPDATA%\Claude\claude_desktop_config.json`）、Antigravity（`~/.gemini/config/mcp_config.json`）的 `screenwriter-kb` MCP 路径已 100% 切至 `D:/Antigravity/zahuo-repo/screenwriter-kb`（注入 D 盘 `SWKB_DATA_DIR` / `UV_CACHE_DIR` / `TEMP`，无任何旧 C 盘项目路径残留），并已同步 `novel-craft-feeder` Skill 与留存 `.bak` 备份。
- **尚未验证 / 边界说明**：本地桌面客户端重启后在 GUI 中实际拉起 stdio MCP 子进程的交互，以及真实桌面浏览器内多标签弧线悬停/点击动效仍待用户本机首次打开时人工目视体验。

## 4. 红线与注意事项（不可破坏）
- **严禁写 C 盘项目旧路径**：权威代码仅在 `D:\Antigravity\zahuo-repo\screenwriter-kb`，禁止再向旧路径 `C:\Users\visser\Downloads\ZaHuo\...` 写入文件；运行 `uv` 命令务必配置 `UV_CACHE_DIR=D:\ScreenwriterKB\.uv-cache` 与 `TEMP=D:\ScreenwriterKB\.tmp`。
- **环境变量 `SWKB_DATA_DIR`**：默认指向 `D:\ScreenwriterKB`。任何自动化测试或实验必须覆盖 `SWKB_DATA_DIR` 或传入临时 `db_path`，严禁直接写入正式库。
- **严禁污染仓库**：绝对不要将 `D:\ScreenwriterKB` 内的个人小说、`kb.sqlite`、`.venv` 或包含 API 密钥的本地客户端配置提交进 Git。
- **Windows 批处理编码**：所有 `.cmd` 脚本必须保持 **CRLF 换行 + UTF-8 无 BOM**（否则 Windows `cmd.exe` 解析首行 `@echo off` 或中文路径会报错）。
- **防盗坏章隔离**：绝对不能从 `chapters.is_corrupted = 1` 的章节中抽对白、建人物或做原创度比对基线。
