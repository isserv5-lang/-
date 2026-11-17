# 项目记忆胶囊与交接记录 (HANDOFF)

> 顶部始终为**最新权威交接胶囊**；新 AI 接手请以顶部胶囊与 `START_HERE.md` 为准。

## 最新记忆胶囊

```markdown
[记忆胶囊] (2026-11-18 06:22)
- 项目目标：本地优先的跨 AI 编剧知识库已完成 **Antigravity 暗黑交互图谱 + 三频平台标签体系（v2）** 升级、全链路回归验收、正式库平滑升级与 8765 端口热更新部署。
- 关键入口：
  - 可视化图谱工作台：`http://127.0.0.1:8765`（或双击 `D:\ScreenwriterKB\打开可视化知识库.cmd` / `screenwriter-kb\启动可视化面板.cmd`）
  - 聚合与拖拽接口：`GET /api/graph/universe`、`POST /api/upload-novel`
  - CLI：`uv run --python 3.12 swkb [mcp|web|import|lock-check|stats|init]`
  - 测试：`uv run --python 3.12 pytest -v`（目录 `screenwriter-kb/`）
- 当前状态：
  - [✓] `src/swkb/taxonomy.py` 沉淀三大主频（`女频` 16 / `男频` 15 / `通用` 14，共 45 个番茄/红果/QQ阅读真实标签）并完成 v1→v2 平滑迁移与 `mcp_server.py` / `web_api.py` 升级。
  - [✓] `src/swkb/static/` 重写为全屏 `#090a0d` 哑光暗黑点阵 Antigravity 胶囊图谱（`◉ 知识星图 / ◎ 互动支线`），支持拖放 `.txt`/`.docx` 到标签节点直传、血脉聚光灯、400px 极简滑出试菜抽屉及 `🔒 精话锁` 浮层（零外部 CDN）。
  - [✓] `tests/test_e2e.py` 已更新并 7/7 全绿通过（`7 passed`，覆盖三频 45 标签、v1→v2 平滑 `ALTER TABLE` 迁移、`/api/graph/universe`、`/api/upload-novel`、真实样本防盗坏章识别、试菜 ✓/✗ + FTS5、精话锁、8-gram 查重、互动影游死胡同诊断与 12 个 MCP 工具）。
  - [✓] `D:\ScreenwriterKB` 已通过 `scripts/connect_ai_clients.py` 完成平滑升级（`works=0` 保持干净空库，3 主频 / 45 标签 / 26 精话锁规则 / 1 个 4 节点演示项目就位）；`127.0.0.1:8765` 已重启为新版服务并实测返回最新星图数据与暗黑前端页面。
  - [~] 真实浏览器鼠标拖拽与三端桌面客户端 GUI 重启拉起 stdio MCP 交互仍待用户本机首次打开时人工目视体验。
- 关键决策与禁区：
  - [!] 正式库在 `D:\ScreenwriterKB`，测试与开发必须用 `SWKB_DATA_DIR` 或临时 `db_path` 隔离，绝不污染正式库或提交版权小说到 Git。
  - [!] 保留原标签 ID `mengwa`、`shoushi`、`chongsheng`、`xuanyi`、`hudong`；旧默认空抽屉 `dushi`/`guyan` 自动迁移至 `dushi_naodong`/`gongdou`。
  - [!] 所有 `.cmd` 启动脚本必须保持 CRLF 换行 + UTF-8 无 BOM。
- 下一步待办：
  1. 用户直接在浏览器刷新 `http://127.0.0.1:8765` 即可体验新版 Antigravity 暗黑知识星图，并把 `.txt`/`.docx` 小说直接拖到对应标签胶囊上传。
  2. 重启 Codex / Claude Desktop / Antigravity 后可通过升级后的 `kb_overview_and_drawers` 查看三大频与 45 个精准标签。
- 核心上下文：swkb.taxonomy, swkb.db, swkb.repository, swkb.web_api, swkb.mcp_server, swkb.static/app.js, novel-craft-feeder
```

---

## 阶段演进归档（倒序精简）
- **2026-11-18 (v2)**：升级番茄/红果/QQ阅读三频 45 标签体系（`taxonomy.py`）、`ALTER TABLE` 平滑迁移、`GET /api/graph/universe` 与 `POST /api/upload-novel`、Antigravity 风格纯黑动态交互图谱前端、E2E 7/7 回归通过及本机正式库/8765 服务刷新。
- **2026-11-18 (v1)**：完成 `screenwriter-kb` 首版核心引擎、多题材知识库与互动影游画布、FastAPI 可视化工作台、12 个 stdio MCP 工具、`novel-craft-feeder` Skill、7 项 E2E 测试验收、三端连接脚本执行及跨 AI 记忆胶囊归档。
