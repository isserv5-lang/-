# 项目记忆胶囊与交接记录 (HANDOFF)

> 顶部始终为**最新权威交接胶囊**；新 AI 接手请以顶部胶囊与 `START_HERE.md` 为准。

## 最新记忆胶囊

```markdown
[记忆胶囊] (2026-11-21 20:54)
- 项目目标：已完成 **D 盘全迁与「一书多标签 + 单梗精准归位防串味」（v3）** 全链路升级、自动化验收、正式库升级、三端 MCP 路径切换至 D 盘、8765 热重启与 GitHub 同步。
- 权威路径与关键入口：
  - 唯一权威项目路径：`D:\Antigravity\zahuo-repo\screenwriter-kb`（Git 根：`D:\Antigravity\zahuo-repo`）
  - 可视化多标签星图工作台：`http://127.0.0.1:8765`（已由 D 盘 `.venv` 热重启，或双击 `D:\ScreenwriterKB\打开可视化知识库.cmd`）
  - 多标签核心接口：`GET /api/graph/universe`（含 `work_tag_links`）、`PUT/GET /api/works/{work_id}/tags`、`POST /api/upload-novel`（支持 `extra_genre_ids` & `auto_detect_tags`）、`POST /api/snippets/{id}/review` & `POST /api/dialogues/{id}/review`（支持 `genre_id` 防串味归位）
  - 测试：`uv run --python 3.12 pytest -v`（需配置 `$env:UV_CACHE_DIR="D:\ScreenwriterKB\.uv-cache"`、`$env:TEMP="D:\ScreenwriterKB\.tmp"`）
- 当前状态：
  - [✓] 后端多标签升级落地：`db.py` 幂等建表 `work_tags` 并自动回填旧库；`ingest.py` 内置 45 标签高频特征词自动识别 1~3 个副标签并按单段对白子标签分流；`repository.py` 实现 `set_work_tags`/`get_work_tags` 与试菜跨标签迁卡防串味；`web_api.py` 与 `mcp_server.py`（13 工具，新增 `update_work_tags`）全量对齐。
  - [✓] 前端多标签动态星图落地：`src/swkb/static/` 实现主标签发光实线弧线 + 副标签虚线跨列弧线、悬停/点击跨频联动聚光灯、喂书浮层多标签点选 + AI 自动识别开关、书籍抽屉内联增删标签、试菜卡片右上角 `⇄` 一键切归属标签。
  - [✓] E2E 测试 9/9 全绿通过（`9 passed`）：新增 `test_8`（多标签上传、PUT 增删签、双路由试菜跨签防串味、卡片隔离、`work_tag_links`、MCP `update_work_tags`）与 `test_9`（真实长篇《玄学小祖宗》自动识别多标签实测）。
  - [✓] 正式库与三端 MCP 全切 D 盘：运行 `scripts/connect_ai_clients.py` 升级 `D:\ScreenwriterKB`（`work_tags` 表已就绪，保持 `works=0` 干净空库，45 标签 / 26 精话锁 / 1 演示项目）；核实 Codex / Claude Desktop / Antigravity 三端配置与 `打开可视化知识库.cmd` 100% 指向 `D:/Antigravity/zahuo-repo/screenwriter-kb` 且注入 D 盘 `UV_CACHE_DIR`/`TEMP`，无旧 C 盘路径残留。
  - [✓] `127.0.0.1:8765` 已基于 D 盘虚拟环境重启，实测 `/api/graph/universe`、`/`、`/static/app.js` 均返回 200 OK 且包含 `work_tag_links` 与多标签前端代码。
  - [~] 真实桌面浏览器鼠标悬停多连线动效与三端客户端 GUI 重启拉起新 D 盘 stdio MCP 仍待用户本机实际打开体验。
- 关键决策与禁区：
  - [!] 严禁写回旧 C 盘路径（`C:\Users\visser\Downloads\ZaHuo\...`）；所有 `uv` 命令均应指向 D 盘缓存目录。
  - [!] 一书可跨多标签连线（`work_tags`），但单条对白严格归属单一抽屉（`dialogue_snippets.genre_id`），彻底杜绝多题材串味。
  - [!] 正式库在 `D:\ScreenwriterKB`，测试与开发必须用 `SWKB_DATA_DIR` 隔离；所有 `.cmd` 脚本保持 CRLF + UTF-8 无 BOM。
- 下一步待办：
  1. 浏览器刷新 `http://127.0.0.1:8765` 体验新版一书多连线星图，试传一本多题材小说验证自动多标签连线与试菜 `⇄` 分流。
  2. 重启 Codex / Claude Desktop / Antigravity 生效新的 D 盘 MCP 路径与 `update_work_tags` 工具。
- 核心上下文：swkb.db, swkb.ingest, swkb.repository, swkb.web_api, swkb.mcp_server, swkb.static/app.js, scripts/connect_ai_clients.py, tests/test_e2e.py
```

---

## 阶段演进归档（倒序精简）
- **2026-11-21 (v3)**：项目全面迁至 D 盘（`D:\Antigravity\zahuo-repo\screenwriter-kb`）并落地「一书多标签 + 单梗精准归位防串味」（`work_tags` 表、45 标签自动识别与单梗分流、`PUT /api/works/{id}/tags`、双路由试菜切签、`work_tag_links` 跨列弧线图谱、13 个 MCP 工具），E2E 9/9 全绿、三端 MCP 路径切换 D 盘、8765 重启验收并推送 GitHub。
- **2026-11-18 (v2)**：升级番茄/红果/QQ阅读三频 45 标签体系（`taxonomy.py`）、`ALTER TABLE` 平滑迁移、`GET /api/graph/universe` 与 `POST /api/upload-novel`、Antigravity 风格纯黑动态交互图谱前端、E2E 7/7 回归通过及本机正式库/8765 服务刷新。
- **2026-11-18 (v1)**：完成 `screenwriter-kb` 首版核心引擎、多题材知识库与互动影游画布、FastAPI 可视化工作台、12 个 stdio MCP 工具、`novel-craft-feeder` Skill、7 项 E2E 测试验收、三端连接脚本执行及跨 AI 记忆胶囊归档。
