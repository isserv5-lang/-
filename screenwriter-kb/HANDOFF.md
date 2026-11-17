# 项目记忆胶囊与交接记录 (HANDOFF)

> 顶部始终为**最新权威交接胶囊**；新 AI 接手请以顶部胶囊与 `START_HERE.md` 为准。

## 最新记忆胶囊

```markdown
[记忆胶囊] (2026-11-18 05:57)
- 项目目标：本地优先的跨 AI 编剧知识库（小说防盗过滤拆解、精话锁去油、原创度查重、互动影游多支线可视化工作台）已完成首版全链路研发、验收与三端接通。
- 关键入口：
  - 可视化面板：`D:\ScreenwriterKB\打开可视化知识库.cmd` 或 `screenwriter-kb\启动可视化面板.cmd`（`http://127.0.0.1:8765`）
  - CLI：`uv run --python 3.12 swkb [mcp|web|import|lock-check|stats|init]`
  - 测试：`uv run --python 3.12 pytest -v`（目录 `screenwriter-kb/`）
- 当前状态：
  - [✓] 核心模块与 Web/MCP/Skill 全部合龙打通（`uv sync --python 3.12 --all-extras` 通过）。
  - [✓] `tests/test_e2e.py` 7 项端到端测试全绿通过（含真实样本 `云糯糯.txt` 11 章切分及伪 `.docx`《兽世万人嫌？可我是死遁的白月光..docx》236 章导入与第 18 章起 44 个防盗坏章精准标红隔离；精话锁拦截与动态加词；8-gram 查重；互动影游死胡同诊断与导出；FastAPI 全接口 200 与 12 个 MCP 工具调用）。
  - [✓] `scripts/connect_ai_clients.py` 已实机执行：`D:\ScreenwriterKB` 初始化为空库（7 抽屉 + 26 条精话锁规则 + 1 个 4 节点演示项目，`works=0`，占用约 140.52 KB）；Codex / Claude Desktop / Antigravity 三端 MCP 与 `novel-craft-feeder` Skill 已同步并校验配置语法合法（均已备份 `.bak`）。
  - [~] 桌面客户端 GUI 实际重启拉起 MCP 及真实浏览器点选操作待用户本机首次打开时人工确认。
- 关键决策与禁区：
  - [!] 正式库在 `D:\ScreenwriterKB`，测试与开发必须用 `SWKB_DATA_DIR` 或临时 `db_path` 隔离，绝不污染正式库或提交版权小说到 Git。
  - [!] `web_api.py` 的 `/api/works/{work_id}/span` 已放开 `chapter_num >= 0`，以兼容 `前言/简介`（第 0 章）读取；`ingest.py` 已兼容无“第X章”标题的数字小节体短篇。
  - [!] 所有 `.cmd` 启动脚本必须保持 CRLF 换行 + UTF-8 无 BOM。
- 下一步待办：
  1. 用户可重启 Codex / Claude Desktop / Antigravity 后在对话中直接喂入小说路径体验「10 句试菜 → 精读入库 → 写稿精话锁去油」。
  2. 用户可双击 `D:\ScreenwriterKB\打开可视化知识库.cmd` 预览 7 大抽屉与示例互动影游画布。
- 核心上下文：swkb.config, swkb.db, swkb.ingest, swkb.jinghua_lock, swkb.originality, swkb.repository, swkb.branching, swkb.web_api, swkb.mcp_server, novel-craft-feeder
```

---

## 阶段演进归档（倒序精简）
- **2026-11-18**：完成 `screenwriter-kb` 首版核心引擎、多题材知识库与互动影游画布、FastAPI 可视化工作台、12 个 stdio MCP 工具、`novel-craft-feeder` Skill、7 项 E2E 测试验收、三端连接脚本执行及跨 AI 记忆胶囊归档。
