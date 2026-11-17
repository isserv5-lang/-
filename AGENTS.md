# 仓库级 AI 协作入口 (AGENTS.md)

本仓库包含子项目目录 [`screenwriter-kb/`](./screenwriter-kb/)。

任何接手开发的 AI（Antigravity / Codex / Claude Code / Cursor / 并行子智能体）请遵守：

1. **接手先读档**：进入 `screenwriter-kb/`，严格按顺序阅读：
   - `screenwriter-kb/START_HERE.md`（当前权威命令、环境与红线）
   - `screenwriter-kb/ARCHITECTURE.md`（模块目录、函数契约与 SQLite 表结构）
   - `screenwriter-kb/HANDOFF.md`（顶部最新 `[记忆胶囊]`）
2. **数据与版权红线**：
   - 本地个人书库与数据库默认存储在仓库外的 `D:\ScreenwriterKB`（或 `SWKB_DATA_DIR` 指定的目录），严禁将任何版权小说原文、`.sqlite` 数据库、`.venv` 或本机客户端配置（含密钥/Token）提交到 Git。
   - 严禁修改同级外部目录（例如 `C:\Users\visser\Downloads\ZaHuo\my-projects`）。
3. **提交前同步记忆胶囊**：完成阶段修改或执行 `git push` 前，务必同步更新 `screenwriter-kb/START_HERE.md`、`screenwriter-kb/ARCHITECTURE.md`（如有结构变更）和 `screenwriter-kb/HANDOFF.md` 顶部 `[记忆胶囊]`。
