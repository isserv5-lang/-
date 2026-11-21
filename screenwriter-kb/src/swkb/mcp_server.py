from __future__ import annotations

import json
import re
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError:
    from mcp.server.mcpserver import MCPServer as FastMCP

from swkb.branching import (
    create_script_project,
    delete_story_choice,
    delete_story_node,
    export_branching_script_markdown,
    get_project_graph,
    list_script_projects,
    upsert_story_choice,
    upsert_story_node,
)
from swkb.config import get_data_dir, get_db_path
from swkb.db import get_conn, init_db
from swkb.ingest import import_novel_file, read_novel_span
from swkb.jinghua_lock import (
    add_jinghua_rule,
    check_text_with_jinghua_lock,
    list_jinghua_rules,
)
from swkb.originality import check_script_originality
from swkb.repository import (
    get_dashboard_stats,
    get_graph_universe,
    get_work_tags,
    list_characters,
    list_dialogue_snippets,
    list_genres,
    list_scene_highlights,
    list_works,
    review_dialogue_snippet,
    save_dialogue_snippet,
    save_scene_highlight,
    set_work_tags,
    upsert_character,
    upsert_genre,
)

mcp = FastMCP("screenwriter-kb")


def _ensure_db() -> None:
    """Ensure the active SQLite database exists and is initialized."""
    db_path = get_db_path()
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='genres'"
        ).fetchone()
        if row is None:
            init_db(conn)
    finally:
        conn.close()


def _parse_json_arg(raw: Any, expected_type: type, field_name: str) -> Any:
    """Parse a JSON string (or accept an already-decoded Python object)."""
    if raw is None:
        return expected_type()
    if isinstance(raw, expected_type):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return expected_type()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} 不是合法的 JSON：{exc}") from exc
        if not isinstance(parsed, expected_type):
            raise ValueError(
                f"{field_name} 必须是 {expected_type.__name__} 类型的 JSON"
            )
        return parsed
    raise ValueError(f"{field_name} 类型不支持：{type(raw).__name__}")


def _parse_tag_csv(raw: str | list[str] | None) -> list[str]:
    """Parse comma-separated tag IDs (or a JSON array string) into a clean deduplicated list."""
    if raw is None:
        return []
    parts: list[str]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                loaded = json.loads(text)
                parts = [str(x) for x in loaded] if isinstance(loaded, list) else [text]
            except (json.JSONDecodeError, ValueError):
                parts = re.split(r"[,，;；\s]+", text)
        else:
            parts = re.split(r"[,，;；\s]+", text)
    else:
        parts = [str(x) for x in raw]
    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tid = part.strip()
        if tid and tid not in seen:
            seen.add(tid)
            cleaned.append(tid)
    return cleaned


def _normalize_review_status(raw_status: str) -> str:
    """Map user-friendly review marks (✓/✗) to canonical status values."""
    val = (raw_status or "").strip().lower()
    if val in {"approved", "approve", "ok", "yes", "✓", "✔", "pass", "good", "1"}:
        return "approved"
    if val in {"rejected", "reject", "no", "✗", "✘", "x", "bad", "0"}:
        return "rejected"
    if val == "pending":
        return "pending"
    raise ValueError(
        f"无效的审核状态 '{raw_status}'，请使用 'approved'（✓）或 'rejected'（✗）"
    )


@mcp.tool()
def kb_overview_and_drawers() -> dict[str, Any]:
    """查看编剧本地知识库总览：三大频（女频/男频/通用）与全部精准标签抽屉、已入库书籍、好梗数量、精话锁规则数与互动影游项目列表。

    建议每次开始新对话、不确定有哪些类型抽屉或想查看库存时最先调用。
    """
    _ensure_db()
    stats = get_dashboard_stats()
    universe = get_graph_universe()
    genres = list_genres()
    works = list_works()
    projects = list_script_projects()
    return {
        "data_dir": str(get_data_dir()),
        "stats": stats,
        "channels": [
            {
                "id": ch.get("id"),
                "name": ch.get("name"),
                "description": ch.get("description"),
                "tags_count": ch.get("tags_count"),
                "works_count": ch.get("works_count"),
                "approved_count": ch.get("approved_count"),
                "pending_count": ch.get("pending_count"),
            }
            for ch in universe.get("channels", [])
        ],
        "genres": genres,
        "works": [
            {
                "id": w.get("id"),
                "title": w.get("title"),
                "author": w.get("author"),
                "genre_id": w.get("genre_id"),
                "tag_ids": w.get("tag_ids", []),
                "tags": w.get("tags", []),
                "total_chapters": w.get("total_chapters"),
                "clean_chapters": w.get("clean_chapters"),
                "corrupted_chapters": w.get("corrupted_chapters"),
            }
            for w in works
        ],
        "interactive_projects": [
            {
                "id": p.get("id"),
                "title": p.get("title"),
                "genre_id": p.get("genre_id"),
                "project_mode": p.get("project_mode"),
                "updated_at": p.get("updated_at"),
            }
            for p in projects
        ],
    }


@mcp.tool()
def create_genre_drawer(
    genre_id: str,
    name: str,
    description: str = "",
    channel_hint: str = "",
    channel: str = "通用",
    sub_group: str = "题材主干",
    platforms: str = "",
) -> dict[str, Any]:
    """新建或更新一个类型抽屉（支持归入女频 / 男频 / 通用三大主频及子分组）。

    参数说明：
    - genre_id: 英文短标识，如 "nvewen"、"kehuan"（全小写无空格）
    - name: 中文抽屉名称，如 "现代虐恋"
    - description: 这个抽屉主要存什么风格的梗与对白
    - channel_hint: 对白风味与创作提示，如 "台词短脆、少废话直接反杀"
    - channel: 所属三大主频，取值 "女频" / "男频" / "通用"（默认 "通用"）
    - sub_group: 二级分组，如 "题材主干" / "爆款设定梗" / "叙事机制"（默认 "题材主干"）
    - platforms: 参考平台标签来源，如 "番茄/红果"（可选）
    """
    _ensure_db()
    gid = genre_id.strip().lower()
    if not gid or not name.strip():
        raise ValueError("genre_id 和 name 均不能为空")
    genre = upsert_genre(
        genre_id=gid,
        name=name.strip(),
        description=description.strip(),
        channel_hint=channel_hint.strip(),
        channel=channel.strip() or "通用",
        sub_group=sub_group.strip() or "题材主干",
        platforms=platforms.strip(),
    )
    return {"ok": True, "genre": genre}


@mcp.tool()
def import_novel(
    file_path: str,
    genre_id: str = "mengwa",
    title: str = "",
    author: str = "",
    extra_genre_ids: str = "",
    auto_detect_tags: bool = True,
) -> dict[str, Any]:
    """导入一本本地小说（支持 .txt 和 .docx，包括纯文本伪装的 .docx），支持一书多标签与单梗智能分流。

    系统会自动识别编码转 UTF-8、切分章节并精准标记防盗乱码/截断坏章（绝不从坏章抽梗），
    同时为书籍挂载主标签与副标签（可手动传入 extra_genre_ids，也可开启 auto_detect_tags 自动识别 1~3 个高置信副标签），
    并从干净章节中抽取 10~15 段待试菜对白按单段特征词分配至最贴合的标签抽屉。

    参数说明：
    - file_path: 小说文件在本地磁盘的绝对路径
    - genre_id: 主类型抽屉 ID（默认 "mengwa"，可用 kb_overview_and_drawers 查看全部抽屉）
    - title: 书名（留空则自动用文件名）
    - author: 作者名（可选）
    - extra_genre_ids: 额外副标签 ID，逗号分隔，如 "chuanshu_sidun,mengwa"（可选）
    - auto_detect_tags: 是否根据全书高频特征词自动识别 1~3 个高置信副标签（默认 True）
    """
    _ensure_db()
    parsed_extras = _parse_tag_csv(extra_genre_ids)
    result = import_novel_file(
        file_path=file_path,
        genre_id=genre_id.strip() or "mengwa",
        title=title.strip() or None,
        author=author.strip(),
        extra_genre_ids=parsed_extras or None,
        auto_detect_tags=bool(auto_detect_tags),
    )
    return {
        "ok": True,
        "import_result": result,
        "next_step": "请调用 get_taste_check_candidates 取出待试菜对白给用户打 ✓/✗（可同步指定 genre_id 防止多标签串味），校准口味后再扫描核心章节入库。",
    }


@mcp.tool()
def update_work_tags(
    work_id: str,
    primary_tag_id: str,
    extra_tag_ids: str = "",
) -> dict[str, Any]:
    """修改一本书的主标签与副标签（一书多标签管理，最多 6 个标签）。

    参数说明：
    - work_id: 书籍 ID
    - primary_tag_id: 主标签 ID（将同步更新 works.genre_id）
    - extra_tag_ids: 副标签 ID 列表，逗号分隔，如 "chuanshu_sidun,xiuluochang"（留空则仅保留主标签）
    """
    _ensure_db()
    wid = work_id.strip()
    primary = primary_tag_id.strip()
    if not wid or not primary:
        raise ValueError("work_id 和 primary_tag_id 均不能为空")
    extras = _parse_tag_csv(extra_tag_ids)
    tags = set_work_tags(
        work_id=wid,
        primary_tag_id=primary,
        extra_tag_ids=extras,
    )
    return {
        "ok": True,
        "work_id": wid,
        "primary_tag_id": tags[0]["id"] if tags else primary,
        "tag_ids": [t["id"] for t in tags],
        "tags": tags,
    }


@mcp.tool()
def get_taste_check_candidates(
    work_id: str = "",
    genre_id: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """获取待用户「试菜」打勾（✓喜欢 / ✗不喜欢）的候选原话对白片段（status='pending'）。

    喂入新书后，先用本工具拿 10 段真原话给用户挑选口味，不要自己替用户拍板。

    参数说明：
    - work_id: 可选，只看指定某本书抽出来的候选对白
    - genre_id: 可选，只看某个类型抽屉下的候选对白
    - limit: 返回条数，默认 10
    """
    _ensure_db()
    wid = work_id.strip() or None
    gid = genre_id.strip() or None
    candidates = list_dialogue_snippets(
        genre_id=gid,
        status="pending",
        limit=limit,
        work_id=wid,
    )
    return {
        "count": len(candidates),
        "candidates": candidates,
        "instruction": "请把这些带章号与原话的片段展示给用户，让用户直接回复每条是 ✓ 还是 ✗（并可指定归属哪个标签抽屉），再调用 submit_taste_reviews 提交。",
    }


@mcp.tool()
def submit_taste_reviews(reviews_json: str) -> dict[str, Any]:
    """批量提交用户对试菜对白的 ✓（approved）或 ✗（rejected）审核结果，支持按条指定归属标签 genre_id 防止串味。

    参数说明：
    - reviews_json: JSON 数组字符串，每项格式为：
      {"snippet_id": "片段ID", "status": "approved 或 rejected（也支持 ✓ / ✗）", "rating": 5, "scene_tag": "可选场景标签", "genre_id": "可选目标标签ID"}
    """
    _ensure_db()
    reviews = _parse_json_arg(reviews_json, list, "reviews_json")
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    approved_count = 0
    rejected_count = 0

    for idx, item in enumerate(reviews):
        if not isinstance(item, dict):
            errors.append({"index": idx, "error": "每条审核记录必须是对象"})
            continue
        snippet_id = str(item.get("snippet_id", "")).strip()
        if not snippet_id:
            errors.append({"index": idx, "error": "缺少 snippet_id"})
            continue
        try:
            norm_status = _normalize_review_status(str(item.get("status", "")))
            rating = int(item.get("rating", 5 if norm_status == "approved" else 1))
            scene_tag = item.get("scene_tag")
            target_genre = item.get("genre_id")
            updated = review_dialogue_snippet(
                snippet_id=snippet_id,
                status=norm_status,
                rating=rating,
                scene_tag=str(scene_tag).strip() if scene_tag is not None else None,
                genre_id=str(target_genre).strip() if target_genre is not None else None,
            )
            results.append(updated)
            if norm_status == "approved":
                approved_count += 1
            elif norm_status == "rejected":
                rejected_count += 1
        except Exception as exc:
            errors.append({"index": idx, "snippet_id": snippet_id, "error": str(exc)})

    return {
        "ok": len(errors) == 0,
        "reviewed_count": len(results),
        "approved_count": approved_count,
        "rejected_count": rejected_count,
        "results": results,
        "errors": errors,
    }


@mcp.tool()
def save_craft_items(
    genre_id: str,
    work_id: str = "",
    dialogues_json: str = "[]",
    characters_json: str = "[]",
    highlights_json: str = "[]",
) -> dict[str, Any]:
    """AI 精读完小说干净章节后，批量把精选「真原话对白梗」「人物声音卡」「精彩钩子名场面」写入本地数据库。

    严禁把防盗坏章内容写入！所有引用的原话、章号、行号必须先通过 read_novel_lines 核实准确。

    参数说明：
    - genre_id: 所属类型抽屉 ID（必填）
    - work_id: 所属书籍 ID（强烈建议传入）
    - dialogues_json: 对白梗 JSON 数组，元素字段：
      raw_text(必填原话), chapter_num, line_start, line_end, scene_tag, why_funny_or_effective, trigger_context, status(默认approved)
    - characters_json: 人物声音卡 JSON 数组，元素字段：
      name(必填), traits(必填性格特点), role_type, speech_style, catchphrases, forbidden_words, sample_lines(列表)
    - highlights_json: 钩子名场面 JSON 数组，元素字段：
      plain_summary(必填平实概括), chapter_range, line_start, line_end, hook_type, why_it_hooks, raw_excerpt
    """
    _ensure_db()
    gid = genre_id.strip() or "mengwa"
    wid = work_id.strip() or None

    dialogues = _parse_json_arg(dialogues_json, list, "dialogues_json")
    characters = _parse_json_arg(characters_json, list, "characters_json")
    highlights = _parse_json_arg(highlights_json, list, "highlights_json")

    saved_dialogues: list[dict[str, Any]] = []
    saved_characters: list[dict[str, Any]] = []
    saved_highlights: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for idx, d in enumerate(dialogues):
        try:
            raw_text = str(d.get("raw_text", "")).strip()
            if not raw_text:
                raise ValueError("raw_text 不能为空")
            saved_dialogues.append(
                save_dialogue_snippet(
                    genre_id=str(d.get("genre_id") or gid).strip() or gid,
                    raw_text=raw_text,
                    work_id=d.get("work_id") or wid,
                    chapter_num=d.get("chapter_num"),
                    line_start=d.get("line_start"),
                    line_end=d.get("line_end"),
                    scene_tag=str(d.get("scene_tag", "")).strip(),
                    why_funny_or_effective=str(d.get("why_funny_or_effective", "")).strip(),
                    trigger_context=str(d.get("trigger_context", "")).strip(),
                    status=str(d.get("status", "approved")).strip() or "approved",
                )
            )
        except Exception as exc:
            errors.append({"kind": "dialogue", "index": idx, "error": str(exc)})

    for idx, c in enumerate(characters):
        try:
            name = str(c.get("name", "")).strip()
            traits = str(c.get("traits", "")).strip()
            if not name or not traits:
                raise ValueError("人物卡片的 name 和 traits 均不能为空")
            saved_characters.append(
                upsert_character(
                    name=name,
                    traits=traits,
                    work_id=c.get("work_id") or wid,
                    genre_id=gid,
                    role_type=str(c.get("role_type", "")).strip(),
                    speech_style=str(c.get("speech_style", "")).strip(),
                    catchphrases=str(c.get("catchphrases", "")).strip(),
                    forbidden_words=str(c.get("forbidden_words", "")).strip(),
                    sample_lines=c.get("sample_lines") or [],
                    char_id=c.get("id") or c.get("char_id"),
                )
            )
        except Exception as exc:
            errors.append({"kind": "character", "index": idx, "error": str(exc)})

    for idx, h in enumerate(highlights):
        try:
            plain_summary = str(h.get("plain_summary", "")).strip()
            if not plain_summary:
                raise ValueError("名场面的 plain_summary 不能为空")
            saved_highlights.append(
                save_scene_highlight(
                    genre_id=gid,
                    plain_summary=plain_summary,
                    work_id=h.get("work_id") or wid,
                    chapter_range=str(h.get("chapter_range", "")).strip(),
                    line_start=h.get("line_start"),
                    line_end=h.get("line_end"),
                    hook_type=str(h.get("hook_type", "")).strip(),
                    why_it_hooks=str(h.get("why_it_hooks", "")).strip(),
                    raw_excerpt=str(h.get("raw_excerpt", "")).strip(),
                )
            )
        except Exception as exc:
            errors.append({"kind": "highlight", "index": idx, "error": str(exc)})

    return {
        "ok": len(errors) == 0,
        "saved_counts": {
            "dialogues": len(saved_dialogues),
            "characters": len(saved_characters),
            "highlights": len(saved_highlights),
        },
        "dialogue_ids": [x.get("id") for x in saved_dialogues],
        "character_ids": [x.get("id") for x in saved_characters],
        "highlight_ids": [x.get("id") for x in saved_highlights],
        "errors": errors,
    }


@mcp.tool()
def read_novel_lines(
    work_id: str,
    chapter_num: int,
    line_start: int = 1,
    line_end: int = 80,
) -> dict[str, Any]:
    """按书籍 ID、章节号与行号范围精准回读原书片段，用于核对原文或精读章节。

    若章节已被标记为防盗坏章（is_corrupted=True），会明确提示原因；严禁在未核实原文的情况下瞎编书里情节！

    参数说明：
    - work_id: 书籍 ID
    - chapter_num: 章节序号（从 1 开始）
    - line_start: 章节内起始行号（默认 1）
    - line_end: 章节内结束行号（默认 80）
    """
    _ensure_db()
    return read_novel_span(
        work_id=work_id.strip(),
        chapter_num=int(chapter_num),
        line_start=int(line_start),
        line_end=int(line_end),
    )


@mcp.tool()
def draw_writing_fuel(
    genre_id: str,
    scene_query: str = "",
    limit: int = 8,
) -> dict[str, Any]:
    """写新小说或新剧本前，从指定类型抽屉一次性取出创作弹药包（严格物理隔离整章原文防抄袭）。

    返回内容包括：
    1. 该抽屉下用户赞过的「真原话对白切片」（按 scene_query 检索）；
    2. 出场人物声音卡（说话习惯、口癖、禁区词）；
    3. 经典钩子结构摘要（不含整章原文）；
    4. 当前生效的「精话锁」禁用词黑名单，提醒写旁白时避开 AI 油腻腔。

    参数说明：
    - genre_id: 类型抽屉 ID（写什么题材就只开对应的抽屉，严禁串抽屉乱炖）
    - scene_query: 可选，当前想写的冲突或场景关键词（如 "反击"、"护短"、"装傻"、"审讯"）
    - limit: 最多返回几条对白样本（默认 8）
    """
    _ensure_db()
    gid = genre_id.strip() or "mengwa"
    query = scene_query.strip()

    matched_dialogues = list_dialogue_snippets(
        genre_id=gid,
        status="approved",
        query=query,
        limit=limit,
    )
    fallback_dialogues: list[dict[str, Any]] = []
    if query and not matched_dialogues:
        fallback_dialogues = list_dialogue_snippets(
            genre_id=gid,
            status="approved",
            query="",
            limit=min(limit, 5),
        )

    characters = list_characters(genre_id=gid)
    highlights = list_scene_highlights(genre_id=gid)
    active_lock_rules = list_jinghua_rules(include_disabled=False)

    return {
        "genre_id": gid,
        "scene_query": query,
        "approved_dialogues": matched_dialogues,
        "fallback_dialogues": fallback_dialogues,
        "character_voice_cards": [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "role_type": c.get("role_type"),
                "traits": c.get("traits"),
                "speech_style": c.get("speech_style"),
                "catchphrases": c.get("catchphrases"),
                "forbidden_words": c.get("forbidden_words"),
                "sample_lines": c.get("sample_lines", []),
            }
            for c in characters[:12]
        ],
        "scene_highlights": [
            {
                "id": h.get("id"),
                "chapter_range": h.get("chapter_range"),
                "hook_type": h.get("hook_type"),
                "plain_summary": h.get("plain_summary"),
                "why_it_hooks": h.get("why_it_hooks"),
            }
            for h in highlights[:10]
        ],
        "jinghua_lock_blacklist": [
            {
                "bad_pattern": r.get("bad_pattern"),
                "category": r.get("category"),
                "good_replacement_example": r.get("good_replacement_example"),
            }
            for r in active_lock_rules
        ],
        "writing_discipline": (
            "创作铁律：1) 只学习以上对白的节奏、卡点与反差，严禁照抄原句；"
            "2) 把机灵、狠话、笑点全留在引号内的台词里；"
            "3) 引号外旁白只写平实可见的普通动作与眼神（如“他停下脚步”“她放下筷子”），绝不用精话锁黑名单里的戏精腔！"
        ),
    }


@mcp.tool()
def run_jinghua_lock(draft_text: str) -> dict[str, Any]:
    """对刚写好的小说或剧本初稿运行「精话锁」去油体检。

    自动区分引号内台词与引号外旁白，重点揪出引号外的 AI 油腻腔、抖机灵夸张动作（如“瞬间卡壳”“夹细了八度”“涨成猪肝色”），
    给出 0~100 健康分、逐条命中位置与平实真人改写指令。交稿前必跑！
    """
    _ensure_db()
    return check_text_with_jinghua_lock(draft_text)


@mcp.tool()
def add_to_jinghua_lock(
    bad_pattern: str,
    category: str = "AI油腻旁白",
    reason: str = "",
    good_replacement_example: str = "",
) -> dict[str, Any]:
    """把用户刚抓到的讨厌油腻表达或生造词永久锁进「精话锁」黑名单，以后所有创作自动拦截。

    参数说明：
    - bad_pattern: 要拉黑的词句或表达，如 "嘴角扯出一抹弧度"
    - category: 分类，如 "AI油腻旁白"、"小品式夸张"、"套路表情"
    - reason: 为什么讨厌/哪里假
    - good_replacement_example: 真人作家会怎么平实改写（示范句）
    """
    _ensure_db()
    if not bad_pattern.strip():
        raise ValueError("bad_pattern 不能为空")
    rule = add_jinghua_rule(
        bad_pattern=bad_pattern.strip(),
        category=category.strip() or "AI油腻旁白",
        reason=reason.strip(),
        good_replacement_example=good_replacement_example.strip(),
    )
    return {"ok": True, "rule": rule}


@mcp.tool()
def check_draft_originality(draft_text: str, genre_id: str = "") -> dict[str, Any]:
    """本地零 Token 原创度查重：把新写初稿与抽屉内已入库对白和原著干净章节做 8-gram 及局部相似度比对，防止无意照抄原句。

    参数说明：
    - draft_text: 新创作的稿件正文
    - genre_id: 可选，只对比指定类型抽屉（留空则全库比对）
    """
    _ensure_db()
    return check_script_originality(
        draft_text=draft_text,
        genre_id=genre_id.strip() or None,
    )


@mcp.tool()
def manage_interactive_drama(action: str, payload_json: str = "{}") -> dict[str, Any]:
    """创建、查看、修改、诊断或导出「互动影游多支线剧本」项目、剧情节点与玩家分支选项。

    支持的 action 及对应 payload_json 参数：
    1. "list_projects"：列出所有互动影游项目（payload 可为 "{}"）
    2. "create_project"：新建项目。payload: {"title": "必填剧名", "genre_id": "hudong", "project_mode": "branching", "synopsis": "简介", "state_vars": {"好感度": 0}, "target_style_notes": "风格要求"}
    3. "get_graph"：读取完整节点连线图与死胡同诊断。payload: {"project_id": "必填项目ID"}
    4. "upsert_node"：新增或更新剧情节点。payload: {"project_id": "必填", "node_id": "更新时传", "node_code": "S1", "title": "节点标题", "episode_num": 1, "node_type": "start|scene|climax|good_ending|bad_ending|hidden_ending", "speaker_summary": "出场人物", "script_text": "正文台本", "camera_or_acting_cue": "表演/镜头提示", "pos_x": 120, "pos_y": 120, "user_rating": 0, "revision_note": ""}
    5. "delete_node"：删除节点。payload: {"node_id": "必填"}
    6. "upsert_choice"：新增或更新玩家选项连线。payload: {"project_id": "必填", "choice_id": "更新时传", "from_node_id": "起点ID", "to_node_id": "终点ID", "choice_label": "选项按钮文案", "choice_tag": "爽点/虐点/隐藏", "condition_expr": "好感度>=2", "state_delta": {"好感度": 1}, "sort_order": 0}
    7. "delete_choice"：删除选项连线。payload: {"choice_id": "必填"}
    8. "export_markdown"：导出带分支跳转的可读台本 Markdown。payload: {"project_id": "必填"}
    9. "batch_save"：一次性批量写入多节点与多选项（支持用 node_code 如 "S1"、"E1" 直接互连！）。payload: {"project_id": "可选(无则用title新建)", "title": "新建时必填", "synopsis": "", "state_vars": {}, "nodes": [...], "choices": [{"from_node_code": "S1", "to_node_code": "E1", "choice_label": "...", ...}]}
    """
    _ensure_db()
    act = (action or "").strip().lower()
    payload = _parse_json_arg(payload_json, dict, "payload_json")

    if act == "list_projects":
        return {"ok": True, "projects": list_script_projects()}

    if act == "create_project":
        title = str(payload.get("title", "")).strip()
        if not title:
            raise ValueError("create_project 需要提供 title")
        project = create_script_project(
            title=title,
            genre_id=str(payload.get("genre_id", "hudong")).strip() or "hudong",
            project_mode=str(payload.get("project_mode", "branching")).strip() or "branching",
            synopsis=str(payload.get("synopsis", "")).strip(),
            state_vars=payload.get("state_vars"),
            target_style_notes=str(payload.get("target_style_notes", "")).strip(),
        )
        return {"ok": True, "project": project}

    if act in {"get_graph", "get_project"}:
        project_id = str(payload.get("project_id", "")).strip()
        if not project_id:
            raise ValueError("get_graph 需要提供 project_id")
        graph = get_project_graph(project_id)
        return {"ok": True, **graph}

    if act == "upsert_node":
        project_id = str(payload.get("project_id", "")).strip()
        title = str(payload.get("title", "")).strip()
        if not project_id or not title:
            raise ValueError("upsert_node 需要提供 project_id 和 title")
        node = upsert_story_node(
            project_id=project_id,
            title=title,
            node_code=str(payload.get("node_code", "S1")).strip() or "S1",
            episode_num=int(payload.get("episode_num", 1)),
            node_type=str(payload.get("node_type", "scene")).strip() or "scene",
            speaker_summary=str(payload.get("speaker_summary", "")).strip(),
            script_text=str(payload.get("script_text", "")),
            camera_or_acting_cue=str(payload.get("camera_or_acting_cue", "")).strip(),
            pos_x=float(payload.get("pos_x", 120)),
            pos_y=float(payload.get("pos_y", 120)),
            user_rating=int(payload.get("user_rating", 0)),
            revision_note=str(payload.get("revision_note", "")).strip(),
            node_id=payload.get("node_id") or None,
        )
        return {"ok": True, "node": node}

    if act == "delete_node":
        node_id = str(payload.get("node_id", "")).strip()
        if not node_id:
            raise ValueError("delete_node 需要提供 node_id")
        return {"ok": delete_story_node(node_id)}

    if act == "upsert_choice":
        project_id = str(payload.get("project_id", "")).strip()
        from_node_id = str(payload.get("from_node_id", "")).strip()
        to_node_id = str(payload.get("to_node_id", "")).strip()
        choice_label = str(payload.get("choice_label", "")).strip()
        if not (project_id and from_node_id and to_node_id and choice_label):
            raise ValueError(
                "upsert_choice 需要提供 project_id, from_node_id, to_node_id, choice_label"
            )
        choice = upsert_story_choice(
            project_id=project_id,
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            choice_label=choice_label,
            choice_tag=str(payload.get("choice_tag", "")).strip(),
            condition_expr=str(payload.get("condition_expr", "")).strip(),
            state_delta=payload.get("state_delta"),
            sort_order=int(payload.get("sort_order", 0)),
            choice_id=payload.get("choice_id") or None,
        )
        return {"ok": True, "choice": choice}

    if act == "delete_choice":
        choice_id = str(payload.get("choice_id", "")).strip()
        if not choice_id:
            raise ValueError("delete_choice 需要提供 choice_id")
        return {"ok": delete_story_choice(choice_id)}

    if act in {"export_markdown", "export"}:
        project_id = str(payload.get("project_id", "")).strip()
        if not project_id:
            raise ValueError("export_markdown 需要提供 project_id")
        markdown = export_branching_script_markdown(project_id)
        return {"ok": True, "markdown": markdown}

    if act == "batch_save":
        project_id = str(payload.get("project_id", "")).strip()
        if not project_id:
            title = str(payload.get("title", "")).strip()
            if not title:
                raise ValueError("batch_save 未提供 project_id 时必须提供 title 以新建项目")
            proj = create_script_project(
                title=title,
                genre_id=str(payload.get("genre_id", "hudong")).strip() or "hudong",
                project_mode=str(payload.get("project_mode", "branching")).strip() or "branching",
                synopsis=str(payload.get("synopsis", "")).strip(),
                state_vars=payload.get("state_vars"),
                target_style_notes=str(payload.get("target_style_notes", "")).strip(),
            )
            project_id = proj["id"]

        code_to_id: dict[str, str] = {}
        try:
            existing_graph = get_project_graph(project_id)
            for existing_node in existing_graph.get("nodes", []):
                if existing_node.get("node_code"):
                    code_to_id[str(existing_node["node_code"])] = existing_node["id"]
        except Exception:
            pass

        for idx, nd in enumerate(payload.get("nodes", [])):
            code = str(nd.get("node_code", f"N{idx + 1}")).strip() or f"N{idx + 1}"
            saved_node = upsert_story_node(
                project_id=project_id,
                title=str(nd.get("title", code)).strip() or code,
                node_code=code,
                episode_num=int(nd.get("episode_num", 1)),
                node_type=str(nd.get("node_type", "scene")).strip() or "scene",
                speaker_summary=str(nd.get("speaker_summary", "")).strip(),
                script_text=str(nd.get("script_text", "")),
                camera_or_acting_cue=str(nd.get("camera_or_acting_cue", "")).strip(),
                pos_x=float(nd.get("pos_x", 120 + (idx % 4) * 280)),
                pos_y=float(nd.get("pos_y", 100 + (idx // 4) * 200)),
                user_rating=int(nd.get("user_rating", 0)),
                revision_note=str(nd.get("revision_note", "")).strip(),
                node_id=nd.get("node_id") or code_to_id.get(code),
            )
            code_to_id[code] = saved_node["id"]

        for idx, ch in enumerate(payload.get("choices", [])):
            from_id = str(
                ch.get("from_node_id")
                or code_to_id.get(str(ch.get("from_node_code", "")).strip(), "")
            ).strip()
            to_id = str(
                ch.get("to_node_id")
                or code_to_id.get(str(ch.get("to_node_code", "")).strip(), "")
            ).strip()
            if not from_id or not to_id:
                raise ValueError(f"第 {idx + 1} 条分支选项找不到对应的起点或终点节点")
            upsert_story_choice(
                project_id=project_id,
                from_node_id=from_id,
                to_node_id=to_id,
                choice_label=str(ch.get("choice_label", "")).strip() or f"选项{idx + 1}",
                choice_tag=str(ch.get("choice_tag", "")).strip(),
                condition_expr=str(ch.get("condition_expr", "")).strip(),
                state_delta=ch.get("state_delta"),
                sort_order=int(ch.get("sort_order", idx)),
                choice_id=ch.get("choice_id") or None,
            )

        graph = get_project_graph(project_id)
        return {"ok": True, **graph}

    return {
        "ok": False,
        "error": f"不支持的 action: {action}",
        "supported_actions": [
            "list_projects",
            "create_project",
            "get_graph",
            "upsert_node",
            "delete_node",
            "upsert_choice",
            "delete_choice",
            "export_markdown",
            "batch_save",
        ],
    }


def main() -> None:
    """Start the screenwriter-kb stdio MCP server."""
    _ensure_db()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
