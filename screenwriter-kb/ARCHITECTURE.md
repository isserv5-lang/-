# 项目架构地图 (ARCHITECTURE)

## 1. 目录结构
```text
zahuo-repo/
├── .gitignore
├── AGENTS.md
├── README.md
└── screenwriter-kb/
    ├── AGENTS.md                        # 跨 AI 协作与红线规范
    ├── CLAUDE.md                        # Claude Code 自动引导入口
    ├── START_HERE.md                    # 当前权威运行指南与边界
    ├── ARCHITECTURE.md                  # 本文件：架构、SQLite 表与接口契约
    ├── HANDOFF.md                       # 最新 [记忆胶囊] 与阶段交接记录
    ├── README.md                        # 用户级上手说明书
    ├── pyproject.toml                   # 轻量预编译依赖与 `swkb` 脚本入口
    ├── uv.lock
    ├── 启动可视化面板.cmd               # 仓库内一键启动面板脚本（CRLF + UTF-8 无 BOM）
    ├── scripts/
    │   └── connect_ai_clients.py        # 初始化 D:\ScreenwriterKB 与三端 MCP/Skill 同步
    ├── skills/
    │   └── novel-craft-feeder/
    │       └── SKILL.md                 # 跨 AI 编剧喂书、精话锁去油与互动影游工作流指南
    ├── src/swkb/
    │   ├── __init__.py
    │   ├── config.py                    # SWKB_DATA_DIR 与子目录（sources/cards/exports/index）解析
    │   ├── taxonomy.py                  # 三大主频 (女频/男频/通用) 与 45 个红果/番茄/QQ阅读标签定义
    │   ├── db.py                        # SQLite 建表、v1->v2 平滑迁移、FTS5 索引与幂等初始种子
    │   ├── ingest.py                    # .txt/真伪.docx 解码、章节切分、防盗坏章检测、按行回读
    │   ├── jinghua_lock.py              # 引号内外分离的「精话锁」检测与黑名单管理
    │   ├── originality.py               # 8-gram 重字率 + rapidfuzz 本地零 Token 原创度查重
    │   ├── models.py                    # sqlite3.Row 转换、JSON 字段解析与实体序列化
    │   ├── repository.py                # 三频标签抽屉、图谱聚合、书架、试菜、FTS5 检索、人物/名场面 CRUD
    │   ├── branching.py                 # 互动影游节点图、死胡同诊断、修改历史与 Markdown 导出
    │   ├── web_api.py                   # FastAPI 本地可视化工作台后端接口（含图谱聚合与拖拽上传）
    │   ├── mcp_server.py                # 标准 stdio MCP 服务（12 个工具）
    │   ├── cli.py                       # `swkb` CLI 入口
    │   └── static/
    │       ├── index.html               # Antigravity 风格暗黑胶囊图谱与支线画布工作台
    │       ├── styles.css
    │       └── app.js
    └── tests/
        └── test_e2e.py                  # 全链路自动化端到端验收测试（7 项）
```

## 2. 核心模块与职责边界
| 模块路径 | 核心职责 | 对外主要公开函数 / 入口 | 依赖关系 |
| :--- | :--- | :--- | :--- |
| `src/swkb/config.py` | 本地保险箱路径解析与子目录自动创建 | `get_data_dir()`, `get_db_path()`, `resolve_data_subdir()` | 无内部依赖 |
| `src/swkb/taxonomy.py` | 三大主频（`女频`/`男频`/`通用`）与 45 个平台标签体系及旧 ID 映射 | `CHANNELS`, `OFFICIAL_TAGS`, `LEGACY_GENRE_MIGRATION`, `CHANNEL_BY_ID`, `TAG_BY_ID` | 标准库 |
| `src/swkb/db.py` | SQLite Schema、v1→v2 平滑字段迁移、FTS5 虚表与幂等种子初始化 | `get_conn()`, `init_db()`, `utc_now_iso()` | `config`, `taxonomy` |
| `src/swkb/ingest.py` | 小说解码（含伪 `.docx`）、章节切分、防盗坏章识别、试菜抽取、按行回读 | `import_novel_file()`, `read_novel_span()`, `load_novel_text()` | `config`, `db`, `rjieba`, `charset_normalizer` |
| `src/swkb/jinghua_lock.py` | 引号内外隔离检测、黑名单增删启停与真人改写建议生成 | `check_text_with_jinghua_lock()`, `list_jinghua_rules()`, `add_jinghua_rule()`, `toggle_jinghua_rule()`, `delete_jinghua_rule()` | `db` |
| `src/swkb/originality.py` | 8-gram 连续重字率 + `rapidfuzz` 局部相似度查重 | `check_script_originality()` | `db`, `rapidfuzz` |
| `src/swkb/models.py` | 行对象序列化、JSON 字段安全解析与领域常量 | `serialize_*()`, `parse_json_field()`, `dump_json_field()` | 标准库 |
| `src/swkb/repository.py` | 45+ 类型标签抽屉、知识星图聚合、书架详情、试菜打勾、FTS5 检索、人物卡、钩子卡、总览统计 | `list_genres()`, `upsert_genre()`, `get_graph_universe()`, `list_works()`, `get_work_detail()`, `update_work_summary()`, `list_dialogue_snippets()`, `save_dialogue_snippet()`, `review_dialogue_snippet()`, `upsert_character()`, `list_characters()`, `save_scene_highlight()`, `list_scene_highlights()`, `get_dashboard_stats()` | `config`, `db`, `models`, `taxonomy`, `rjieba` |
| `src/swkb/branching.py` | 互动影游项目、节点坐标与版本对比、选项数值、死胡同诊断与台本导出 | `create_script_project()`, `list_script_projects()`, `get_project_graph()`, `upsert_story_node()`, `delete_story_node()`, `upsert_story_choice()`, `delete_story_choice()`, `export_branching_script_markdown()`, `list_node_revisions()` | `config`, `db`, `models`, `jinghua_lock` |
| `src/swkb/web_api.py` | FastAPI REST 接口（含 `/api/graph/universe` 与 `/api/upload-novel`）与静态页面托管（`127.0.0.1:8765`） | `app`, `run_server()` | `config`, `db`, `ingest`, `jinghua_lock`, `originality`, `repository`, `branching` |
| `src/swkb/mcp_server.py` | stdio MCP 服务，暴露 12 个编剧/建图工具（含三大频总览与标签建抽屉） | `mcp`, `main()` | `config`, `db`, `ingest`, `jinghua_lock`, `originality`, `repository`, `branching` |
| `src/swkb/cli.py` | `swkb` 命令行工具（`mcp`/`web`/`import`/`lock-check`/`stats`/`init`） | `main()`, `build_parser()` | `config`, `db`, `ingest`, `jinghua_lock`, `repository`, `web_api`, `mcp_server` |

## 3. SQLite 数据表结构（`D:\ScreenwriterKB\index\kb.sqlite`）
- **`kb_meta`**：初始化与迁移标记（`initial_seed_v1`、`taxonomy_v2_synced`），保证即使删除示例也不重复灌回，且支持 v1 库自动平滑升级。
- **`genres`**：三频标签抽屉（含 `channel`、`sub_group`、`platforms`、`sort_order`；预置 `女频` 16 + `男频` 15 + `通用` 14 共 45 个标签，保留 `mengwa`、`shoushi`、`chongsheng`、`xuanyi`、`hudong` 原 ID 并将旧空抽屉 `dushi`/`guyan` 平滑迁移至 `dushi_naodong`/`gongdou`）。
- **`works`** & **`chapters`**：导入书架、UTF-8 规范文本路径、SHA-256 幂等键、章节起止行列与 `is_corrupted` 防盗标记。
- **`dialogue_snippets`** + **`dialogue_fts`**（FTS5 `unicode61`）：待试菜（`pending`）/ 已入库（`approved`）/ 已淘汰（`rejected`）对白，`approved` 时同步导出 `cards/{genre_id}/dialogue_*.md`。
- **`characters`** & **`scene_highlights`**：人物声音卡（含口癖/禁区词/例句）与钩子名场面卡。
- **`jinghua_lock_rules`**：精话锁规则表（预置 26 条典型 AI 油腻旁白/小品夸张词规则，支持精确子串与正则）。
- **`script_projects`**, **`story_nodes`**, **`story_choices`**, **`script_revisions`**：互动影游多支线工程、画布坐标、分支好感/条件数值与正文修改记录。

## 4. 核心数据流与关键设计决策
- **本地保险箱物理隔离**：所有用户数据存放在 `SWKB_DATA_DIR`（默认 `D:\ScreenwriterKB`）下的 `sources/`、`cards/`、`exports/`、`index/kb.sqlite`，与代码仓库完全分离。
- **抽屉物理隔离**：写作前通过 `draw_writing_fuel(genre_id)` 只打开当前题材抽屉，不返回整章原文，防止 AI 串题材乱炖或照搬原著。
- **伪 `.docx` 兜底与数字分节**：`load_novel_text` 先尝试 ZIP 解析 `word/document.xml`，失败则自动回退纯文本编码探测（UTF-8/UTF-16/GB18030）；`_split_chapters` 在无标准“第X章”标题时自动识别连续数字小节标题（`1, 2, 3...`）。
- **防盗坏章双重识别**：`ingest.py` 结合防盗手打特征词、异常断句截断、乱码符检测及“后半章人名断裂 + 异世界词簇突变”过滤坏章，确保干净章才参与抽梗与查重。
- **精话锁引号隔离**：`jinghua_lock.py` 逐行拆出引号内台词与引号外旁白，只用黑名单严查引号外旁白，保护台词本身的个性与笑点。
