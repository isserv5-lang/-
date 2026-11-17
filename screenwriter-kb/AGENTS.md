# AI 协作与项目交接规范 (AGENTS.md)

任何接手 `screenwriter-kb` 的 AI（Antigravity / Codex / Claude Code / Cursor / 子智能体）在修改代码前必须遵守以下规则：

1. **接手先读档**：
   - 先读 `START_HERE.md`（当前权威状态、运行/测试命令与禁区）和 `ARCHITECTURE.md`（目录结构、SQLite 表与模块接口契约）。
   - 再读 `HANDOFF.md` 顶部的**最新一条 `[记忆胶囊]`**，并用 `git status -sb` 与 `git log -n 3 --oneline` 核对工作区实况。
   - 历史记录仅供参考，切勿把已完成旧待办当作当前任务。

2. **数据隔离与测试纪律**：
   - 正式数据保险箱默认位于 `D:\ScreenwriterKB`（可通过环境变量 `SWKB_DATA_DIR` 覆盖）。
   - 运行自动化测试或开发调试时，**必须**通过 `SWKB_DATA_DIR` 或函数参数 `db_path` 指向临时隔离目录，严禁污染用户的 `D:\ScreenwriterKB`。
   - 严禁把版权小说原文件、`.sqlite` 数据库、`.bak` 备份或本机 AI 客户端配置提交到 Git。

3. **编码与风格红线（「精话锁」原则）**：
   - 本项目面向中文网文/短剧/互动影游编剧，任何对话提取或原文阅读均需核实真实行号与章节，**严禁在防盗坏章（`is_corrupted=1`）中抽取语料**。
   - 辅助用户写正文时，先抽对应类型抽屉（`draw_writing_fuel`），严格隔离跨类型语料；引号外旁白保持平实动作描写，出稿前务必过 `run_jinghua_lock` 与 `check_draft_originality`。

4. **并行开发与提交前同步胶囊**：
   - 多智能体并行时，严格在各自负责文件内修改，跨模块修改公共签名需同步更新 `ARCHITECTURE.md`。
   - 每完成一个阶段、推送 GitHub 前，或收到 `/save` / `生成交接包` 时，必须基于实测结果同步更新 `START_HERE.md`、`ARCHITECTURE.md`（若结构变动）和 `HANDOFF.md` 顶部的最新 `[记忆胶囊]`，诚实标注 `[✓] 已验证`、`[~] 待验证` 与 `[!] 踩坑/禁区`。
