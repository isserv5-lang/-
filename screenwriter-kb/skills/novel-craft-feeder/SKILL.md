---
name: novel-craft-feeder
description: >-
  跨 AI 通用编剧本地知识库喂书提炼、精话锁去油创作与互动影游多支线编剧技能。
  当用户要“喂新书进知识库”、“按某类型抽屉写小说/短剧台本”、“检查或去除 AI 油腻旁白（精话锁）”、
  “拉黑讨厌表达”，或“设计多分支互动影游/短剧剧本与结局连线图”时激活本技能。
---

# Novel Craft Feeder · 编剧喂书提炼、精话锁创作与互动影游编剧指南

本技能绑定本地 MCP 服务 `screenwriter-kb`（数据默认存于 `D:\ScreenwriterKB`，不上传云端）。
核心铁律：**宁缺毋滥、绝不瞎编原文、严禁跨类型乱炖、梗留引号内、旁白写人话**。

每次开始任务前，不确定抽屉或库存时，先调用 `kb_overview_and_drawers()` 查看现有类型抽屉、书籍、好梗数与互动剧项目。

---

## 流程一：喂新书入库（过滤坏章 → 10句试菜校准 → 精读入库）

当用户提供一本本地小说（`.txt` 或 `.docx`，含纯文本伪装的 `.docx`）要求学习/入库时，严格按以下 4 步走：

1. **先选抽屉再导入（禁止乱放）**
   - 若对应题材抽屉不存在（如新题材），先调用 `create_genre_drawer(genre_id, name, description, channel_hint)` 新建抽屉。
   - 调用 `import_novel(file_path, genre_id, title="", author="")` 导入小说。
   - 向用户**如实汇报**总章数、干净章数、被自动踢除的防盗乱码/截断坏章（`corrupted_chapters`）及原因。**绝不从坏章里抽任何内容！**

2. **抽 10 段真原话让用户打 ✓/✗ 试菜（严禁 AI 自己拍板）**
   - 调用 `get_taste_check_candidates(work_id=..., genre_id=..., limit=10)` 取出系统初步提取的候选对白。
   - 把这 10 段带章节号与行号的**原话**直接列给用户，请用户快速回复每条是 **✓（喜欢/要这种味）** 还是 **✗（不喜欢/别抓这种）**。
   - 收到用户反馈后，立刻调用 `submit_taste_reviews(reviews_json)` 批量提交 `approved` / `rejected`，把用户的真实口味锁进抽屉。

3. **按用户勾中的口味精读核心干净章节**
   - 使用 `read_novel_lines(work_id, chapter_num, line_start, line_end)` 逐段阅读干净章节（特别是前 3~15 章开篇抓人段落与高冲突章节）。
   - **真实性红线**：未调用 `read_novel_lines` 亲眼核对过的章节、人名、台词，**绝对禁止脑补或编造**；遇到读不准或遇到坏章截断，必须直说。

4. **提炼三件套批量入库**
   - 调用 `save_craft_items(genre_id, work_id, dialogues_json, characters_json, highlights_json)` 一次写入：
     - **真原话对白梗**（`dialogues_json`）：原汁原味摘录，标清 `chapter_num`、`line_start`、`line_end`、`scene_tag`、`why_funny_or_effective`（好笑/狠/爽在哪里）、`trigger_context`。
     - **人物声音卡**（`characters_json`）：写清 `name`、`role_type`、`traits`、`speech_style`（怎么说话）、`catchphrases`（口癖）、`forbidden_words`（这人绝对不会说的话）、`sample_lines`。
     - **5 个带章号名场面**（`highlights_json`）：提炼约 5 个最抓人的钩子/反转名场面，包含 `chapter_range`、`hook_type`、`plain_summary`（平实人话概括）、`why_it_hooks`。

---

## 流程二：按抽屉写小说 / 短剧 + 「精话锁」去油

当用户要求写新小说章节、短剧台词或改写润色时，严格执行：

1. **只开对应类型的抽屉拿弹药（物理隔离防抄袭）**
   - 调用 `draw_writing_fuel(genre_id, scene_query="", limit=8)`。
   - 只能开启与当前创作题材一致的 `genre_id`（例如写萌娃只开 `mengwa`，写兽世只开 `shoushi`），严禁把不同类型抽屉的梗混在一起乱炖。
   - 该工具只返回精选原话切片、人物声音卡、钩子结构与当前「精话锁」黑名单，不返回整章原文。**只学节奏、卡点与反差，严禁照抄原句！**

2. **初稿写作铁律：「梗留引号内，旁白老老实实写普通动作」**
   - 把机灵、毒舌、奶凶、反转、笑点全部放进角色**引号内的台词**里，严格贴合人物声音卡。
   - **引号外旁白必须写真人作家的平实动作**：写看得到的动作、位置、物件与对话停顿（如“他把茶杯放回桌上”“她回头看了一眼”“屋里没人说话”）。
   - **严禁使用 AI 油腻腔 / 小品式夸张旁白**，例如：`瞬间卡壳`、`夹细了八度`、`涨成猪肝色`、`空气突然安静`、`石化在原地`、`嘴角抽搐`、`嘴角勾起一抹三分薄凉`、`眼底闪过一丝不易察觉`、`倒吸一口凉气`、`杀气碎了一地` 等。

3. **出稿前双体检（缺一不可）**
   - 第一检：调用 `run_jinghua_lock(draft_text)` 刮油体检。若未通过或有任何命中，**必须按返回的 `hits` 与 `rewrite_guide` 逐处改写成平实人话**，直到 `passed=True` 再给用户看（或附上体检分）。
   - 第二检：调用 `check_draft_originality(draft_text, genre_id=...)` 进行本地零 Token 原创度查重，确保没有无意照搬原书句子。

4. **用户抓到新油腻词 → 立刻永久拉黑**
   - 只要用户指出某句话“太 AI / 太油腻 / 像小品 / 不想再看到这种写法”，立刻调用：
     `add_to_jinghua_lock(bad_pattern, category="AI油腻旁白", reason=..., good_replacement_example=...)`
   - 把它永久锁进「精话锁」黑名单，以后所有客户端（Codex / Claude / Antigravity）都会自动拦截。

---

## 流程三：多支线互动影游 / 短剧编剧

当用户要创作真人互动影游、多结局短剧或互动分支剧情时：

1. **先定结构与数值变量**
   - 调用 `manage_interactive_drama(action="create_project", payload_json=...)` 或使用 `batch_save`，明确 `state_vars`（例如 `{"好感度": 0, "怀疑度": 0, "线索数": 0}`）与目标风格。
   - 需要特定题材语感时，先配合对应抽屉调用 `draw_writing_fuel` 垫底，写出的台词同样必须过 `run_jinghua_lock`。

2. **设计高张力节点与差异化选项**
   - 使用 `manage_interactive_drama` 的 `upsert_node` / `upsert_choice` 或一次性 `batch_save` 构建剧情树：
     - **节点类型（`node_type`）**：`start`（开局引入）、`scene`（过渡/推进）、`climax`（关键卡点高潮）、`good_ending`（好结局）、`bad_ending`（坏结局）、`hidden_ending`（隐藏结局）。
     - **节点内容**：每个节点都要有清晰的 `speaker_summary`（出场人物）、`script_text`（精炼可演的对白台本）、`camera_or_acting_cue`（微表情/景别/走位提示，拒绝油腻词汇）。
     - **玩家分支选项（`story_choices`）**：选项不能是“去左边/去右边”的废话，必须带有明显情绪或利益冲突（用 `choice_tag` 标出 `爽点` / `虐点` / `隐藏` / `作死`），并配置 `condition_expr`（触发门槛，如 `好感度>=2`）与 `state_delta`（属性加减，如 `{"好感度": 2, "怀疑度": -1}`）。

3. **死胡同与多结局闭环自检**
   - 写完或改完后，务必调用 `manage_interactive_drama(action="get_graph", payload_json='{"project_id": "..."}')` 查看 `diagnostics` 诊断结果，确保没有无后续选项的非结局“死胡同节点”或孤立节点，确保所有结局都能闭环到达。
   - 需要交付完整文字台本时，调用 `manage_interactive_drama(action="export_markdown", ...)` 导出带跳转编号的剧本 Markdown。
   - **主动提示用户**：可以双击 `D:\ScreenwriterKB\打开可视化知识库.cmd`（或在终端运行 `swkb web`），在本地可视化网页的「互动影游多支线画布」中直观查看并拖拽调整所有剧情节点、分支连线与数值变化。
