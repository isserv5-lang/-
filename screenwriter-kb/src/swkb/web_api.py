from __future__ import annotations

import re
import threading
import uuid
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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
from swkb.db import get_conn, init_db, utc_now_iso
from swkb.ingest import import_novel_file, read_novel_span
from swkb.jinghua_lock import (
    add_jinghua_rule,
    check_text_with_jinghua_lock,
    delete_jinghua_rule,
    list_jinghua_rules,
    toggle_jinghua_rule,
)
from swkb.originality import check_script_originality
from swkb.repository import (
    get_dashboard_stats,
    get_work_detail,
    list_characters,
    list_dialogue_snippets,
    list_genres,
    list_scene_highlights,
    list_works,
    review_dialogue_snippet,
    save_dialogue_snippet,
    save_scene_highlight,
    update_work_summary,
    upsert_character,
    upsert_genre,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _ensure_db() -> Path:
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
    return db_path


@asynccontextmanager
async def _lifespan(_: FastAPI):
    _ensure_db()
    yield


app = FastAPI(
    title="编剧本地知识库 & 互动影游工作台",
    version="0.1.0",
    lifespan=_lifespan,
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class GenreCreateRequest(BaseModel):
    genre_id: str | None = None
    id: str | None = None
    name: str
    description: str = ""
    channel_hint: str = ""


class NovelImportRequest(BaseModel):
    file_path: str
    genre_id: str = "mengwa"
    title: str | None = None
    author: str = ""


class WorkSummaryUpdateRequest(BaseModel):
    summary_one_page: str
    style_notes: str = ""


class SnippetCreateRequest(BaseModel):
    genre_id: str
    raw_text: str
    work_id: str | None = None
    chapter_num: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    scene_tag: str = ""
    why_funny_or_effective: str = ""
    trigger_context: str = ""
    status: str = "approved"


class SnippetReviewRequest(BaseModel):
    status: str
    rating: int = 0
    scene_tag: str | None = None


class CharacterUpsertRequest(BaseModel):
    char_id: str | None = None
    work_id: str | None = None
    genre_id: str = "mengwa"
    name: str
    role_type: str = ""
    traits: str
    speech_style: str = ""
    catchphrases: str = ""
    forbidden_words: str = ""
    sample_lines: list[str] = Field(default_factory=list)


class HighlightCreateRequest(BaseModel):
    genre_id: str
    plain_summary: str
    work_id: str | None = None
    chapter_range: str = ""
    line_start: int | None = None
    line_end: int | None = None
    hook_type: str = ""
    why_it_hooks: str = ""
    raw_excerpt: str = ""


class JinghuaCheckRequest(BaseModel):
    text: str


class JinghuaRuleCreateRequest(BaseModel):
    bad_pattern: str
    category: str = "AI油腻旁白"
    reason: str = ""
    good_replacement_example: str = ""
    is_regex: bool = False


class JinghuaRuleToggleRequest(BaseModel):
    enabled: bool


class OriginalityCheckRequest(BaseModel):
    draft_text: str
    genre_id: str | None = None
    threshold: float = 0.35


class ProjectCreateRequest(BaseModel):
    title: str
    genre_id: str = "hudong"
    project_mode: str = "branching"
    synopsis: str = ""
    state_vars: dict[str, Any] = Field(default_factory=dict)
    target_style_notes: str = ""


class StoryNodeUpsertRequest(BaseModel):
    node_id: str | None = None
    project_id: str | None = None
    title: str
    node_code: str = "S1"
    episode_num: int = 1
    node_type: str = "scene"
    speaker_summary: str = ""
    script_text: str = ""
    camera_or_acting_cue: str = ""
    pos_x: float = 120.0
    pos_y: float = 120.0
    user_rating: int = 0
    revision_note: str = ""


class NodePositionRequest(BaseModel):
    pos_x: float
    pos_y: float


class StoryChoiceUpsertRequest(BaseModel):
    choice_id: str | None = None
    from_node_id: str
    to_node_id: str
    choice_label: str
    choice_tag: str = ""
    condition_expr: str = ""
    state_delta: dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify_genre_id(name: str) -> str:
    ascii_part = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip()).strip("_").lower()
    if ascii_part and len(ascii_part) >= 2:
        return ascii_part[:24]
    return f"drawer_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Static & Health
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def index_page():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="前端页面文件不存在")
    return FileResponse(
        path=str(index_file),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/health")
def api_health():
    db_path = _ensure_db()
    return {
        "status": "ok",
        "data_dir": str(get_data_dir()),
        "db_path": str(db_path),
    }


@app.get("/api/dashboard")
def api_dashboard():
    _ensure_db()
    return {
        "stats": get_dashboard_stats(),
        "genres": list_genres(),
        "data_dir": str(get_data_dir()),
    }


# ---------------------------------------------------------------------------
# Genres
# ---------------------------------------------------------------------------


@app.get("/api/genres")
def api_list_genres():
    _ensure_db()
    return {"items": list_genres()}


@app.post("/api/genres")
def api_create_genre(payload: GenreCreateRequest):
    _ensure_db()
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="抽屉名称不能为空")
    gid = (payload.genre_id or payload.id or "").strip()
    if not gid:
        gid = _slugify_genre_id(name)
    item = upsert_genre(
        genre_id=gid,
        name=name,
        description=payload.description.strip(),
        channel_hint=payload.channel_hint.strip(),
    )
    return {"item": item}


# ---------------------------------------------------------------------------
# Works & Ingest
# ---------------------------------------------------------------------------


@app.get("/api/works")
def api_list_works(genre_id: str | None = Query(default=None)):
    _ensure_db()
    gid = genre_id.strip() if genre_id else None
    return {"items": list_works(genre_id=gid or None)}


@app.post("/api/works/import")
def api_import_novel(payload: NovelImportRequest):
    _ensure_db()
    raw_path = payload.file_path.strip().strip('"').strip("'")
    if not raw_path:
        raise HTTPException(status_code=400, detail="请填写本地小说文件的完整路径")
    target = Path(raw_path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=400, detail=f"找不到该文件：{raw_path}")
    try:
        result = import_novel_file(
            file_path=target,
            genre_id=payload.genre_id.strip() or "mengwa",
            title=payload.title.strip() if payload.title else None,
            author=payload.author.strip(),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"导入失败：{exc}") from exc
    return {"result": result}


@app.get("/api/works/{work_id}")
def api_get_work_detail(work_id: str):
    _ensure_db()
    detail = get_work_detail(work_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="找不到这本书")
    return {"work": detail}


@app.put("/api/works/{work_id}/summary")
def api_update_work_summary(work_id: str, payload: WorkSummaryUpdateRequest):
    _ensure_db()
    try:
        updated = update_work_summary(
            work_id=work_id,
            summary_one_page=payload.summary_one_page,
            style_notes=payload.style_notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"work": updated}


@app.get("/api/works/{work_id}/span")
def api_read_novel_span(
    work_id: str,
    chapter_num: int = Query(default=1, ge=0),
    line_start: int = Query(default=1, ge=1),
    line_end: int = Query(default=80, ge=1),
):
    _ensure_db()
    try:
        span = read_novel_span(
            work_id=work_id,
            chapter_num=chapter_num,
            line_start=line_start,
            line_end=line_end,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取原文失败：{exc}") from exc
    return {"span": span}


# ---------------------------------------------------------------------------
# Dialogue Snippets, Characters, Highlights
# ---------------------------------------------------------------------------


@app.get("/api/snippets")
def api_list_snippets(
    genre_id: str | None = Query(default=None),
    status: str = Query(default="approved"),
    query: str = Query(default=""),
    scene_tag: str = Query(default=""),
    work_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    _ensure_db()
    gid = genre_id.strip() if genre_id else None
    wid = work_id.strip() if work_id else None
    fetch_limit = max(limit, 120) if wid else limit
    items = list_dialogue_snippets(
        genre_id=gid or None,
        status=status,
        query=query.strip(),
        scene_tag=scene_tag.strip(),
        limit=fetch_limit,
    )
    if wid:
        items = [item for item in items if item.get("work_id") == wid][:limit]
    return {"items": items}


@app.post("/api/snippets")
def api_save_snippet(payload: SnippetCreateRequest):
    _ensure_db()
    if not payload.raw_text.strip():
        raise HTTPException(status_code=400, detail="对白原文不能为空")
    item = save_dialogue_snippet(
        genre_id=payload.genre_id.strip() or "mengwa",
        raw_text=payload.raw_text.strip(),
        work_id=payload.work_id or None,
        chapter_num=payload.chapter_num,
        line_start=payload.line_start,
        line_end=payload.line_end,
        scene_tag=payload.scene_tag.strip(),
        why_funny_or_effective=payload.why_funny_or_effective.strip(),
        trigger_context=payload.trigger_context.strip(),
        status=payload.status,
    )
    return {"item": item}


@app.post("/api/snippets/{snippet_id}/review")
def api_review_snippet(snippet_id: str, payload: SnippetReviewRequest):
    _ensure_db()
    if payload.status not in ("pending", "approved", "rejected"):
        raise HTTPException(status_code=400, detail="审核状态必须是 pending / approved / rejected")
    try:
        item = review_dialogue_snippet(
            snippet_id=snippet_id,
            status=payload.status,
            rating=payload.rating,
            scene_tag=payload.scene_tag,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"item": item}


@app.get("/api/characters")
def api_list_characters(
    work_id: str | None = Query(default=None),
    genre_id: str | None = Query(default=None),
):
    _ensure_db()
    return {
        "items": list_characters(
            work_id=(work_id.strip() if work_id else None) or None,
            genre_id=(genre_id.strip() if genre_id else None) or None,
        )
    }


@app.post("/api/characters")
def api_upsert_character(payload: CharacterUpsertRequest):
    _ensure_db()
    if not payload.name.strip() or not payload.traits.strip():
        raise HTTPException(status_code=400, detail="人物名字和性格特点不能为空")
    item = upsert_character(
        name=payload.name.strip(),
        traits=payload.traits.strip(),
        work_id=payload.work_id or None,
        genre_id=payload.genre_id.strip() or "mengwa",
        role_type=payload.role_type.strip(),
        speech_style=payload.speech_style.strip(),
        catchphrases=payload.catchphrases.strip(),
        forbidden_words=payload.forbidden_words.strip(),
        sample_lines=payload.sample_lines,
        char_id=payload.char_id,
    )
    return {"item": item}


@app.get("/api/highlights")
def api_list_highlights(
    work_id: str | None = Query(default=None),
    genre_id: str | None = Query(default=None),
):
    _ensure_db()
    return {
        "items": list_scene_highlights(
            work_id=(work_id.strip() if work_id else None) or None,
            genre_id=(genre_id.strip() if genre_id else None) or None,
        )
    }


@app.post("/api/highlights")
def api_save_highlight(payload: HighlightCreateRequest):
    _ensure_db()
    if not payload.plain_summary.strip():
        raise HTTPException(status_code=400, detail="场景大白话摘要不能为空")
    item = save_scene_highlight(
        genre_id=payload.genre_id.strip() or "mengwa",
        plain_summary=payload.plain_summary.strip(),
        work_id=payload.work_id or None,
        chapter_range=payload.chapter_range.strip(),
        line_start=payload.line_start,
        line_end=payload.line_end,
        hook_type=payload.hook_type.strip(),
        why_it_hooks=payload.why_it_hooks.strip(),
        raw_excerpt=payload.raw_excerpt.strip(),
    )
    return {"item": item}


# ---------------------------------------------------------------------------
# Jinghua Lock (精话锁)
# ---------------------------------------------------------------------------


@app.get("/api/jinghua/rules")
def api_list_jinghua_rules(include_disabled: bool = Query(default=True)):
    _ensure_db()
    return {"items": list_jinghua_rules(include_disabled=include_disabled)}


@app.post("/api/jinghua/rules")
def api_add_jinghua_rule(payload: JinghuaRuleCreateRequest):
    _ensure_db()
    if not payload.bad_pattern.strip():
        raise HTTPException(status_code=400, detail="禁用表达不能为空")
    item = add_jinghua_rule(
        bad_pattern=payload.bad_pattern.strip(),
        category=payload.category.strip() or "AI油腻旁白",
        reason=payload.reason.strip(),
        good_replacement_example=payload.good_replacement_example.strip(),
        is_regex=payload.is_regex,
    )
    return {"item": item}


@app.patch("/api/jinghua/rules/{rule_id}")
@app.post("/api/jinghua/rules/{rule_id}/toggle")
def api_toggle_jinghua_rule(rule_id: str, payload: JinghuaRuleToggleRequest):
    _ensure_db()
    ok = toggle_jinghua_rule(rule_id=rule_id, enabled=payload.enabled)
    if not ok:
        raise HTTPException(status_code=404, detail="找不到该规则")
    return {"ok": True, "enabled": payload.enabled}


@app.delete("/api/jinghua/rules/{rule_id}")
def api_delete_jinghua_rule(rule_id: str):
    _ensure_db()
    ok = delete_jinghua_rule(rule_id=rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="找不到该规则")
    return {"ok": True}


@app.post("/api/jinghua/check")
def api_check_jinghua(payload: JinghuaCheckRequest):
    _ensure_db()
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="请先粘贴需要检查的正文段落")
    result = check_text_with_jinghua_lock(payload.text)
    return {"result": result}


# ---------------------------------------------------------------------------
# Originality Check
# ---------------------------------------------------------------------------


@app.post("/api/originality/check")
def api_check_originality(payload: OriginalityCheckRequest):
    _ensure_db()
    if not payload.draft_text.strip():
        raise HTTPException(status_code=400, detail="请先输入需要查重的草稿正文")
    result = check_script_originality(
        draft_text=payload.draft_text,
        genre_id=(payload.genre_id.strip() if payload.genre_id else None) or None,
        threshold=payload.threshold,
    )
    return {"result": result}


# ---------------------------------------------------------------------------
# Interactive Branching Drama (互动影游多支线画布)
# ---------------------------------------------------------------------------


@app.get("/api/projects")
def api_list_projects():
    _ensure_db()
    return {"items": list_script_projects()}


@app.post("/api/projects")
def api_create_project(payload: ProjectCreateRequest):
    _ensure_db()
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="剧本名称不能为空")
    item = create_script_project(
        title=payload.title.strip(),
        genre_id=payload.genre_id.strip() or "hudong",
        project_mode=payload.project_mode.strip() or "branching",
        synopsis=payload.synopsis.strip(),
        state_vars=payload.state_vars,
        target_style_notes=payload.target_style_notes.strip(),
    )
    return {"item": item}


@app.get("/api/projects/{project_id}/graph")
def api_get_project_graph(project_id: str):
    _ensure_db()
    try:
        graph = get_project_graph(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return graph


@app.post("/api/projects/{project_id}/nodes")
def api_create_or_update_node(project_id: str, payload: StoryNodeUpsertRequest):
    _ensure_db()
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="场次标题不能为空")
    item = upsert_story_node(
        project_id=project_id,
        title=payload.title.strip(),
        node_code=payload.node_code.strip() or "S1",
        episode_num=payload.episode_num,
        node_type=payload.node_type.strip() or "scene",
        speaker_summary=payload.speaker_summary.strip(),
        script_text=payload.script_text,
        camera_or_acting_cue=payload.camera_or_acting_cue.strip(),
        pos_x=payload.pos_x,
        pos_y=payload.pos_y,
        user_rating=payload.user_rating,
        revision_note=payload.revision_note.strip(),
        node_id=payload.node_id,
    )
    return {"item": item}


@app.put("/api/nodes/{node_id}")
def api_update_node(node_id: str, payload: StoryNodeUpsertRequest):
    _ensure_db()
    project_id = payload.project_id
    if not project_id:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT project_id FROM story_nodes WHERE id = ?", (node_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="找不到该剧情节点")
            project_id = row["project_id"]
        finally:
            conn.close()
    item = upsert_story_node(
        project_id=project_id,
        title=payload.title.strip(),
        node_code=payload.node_code.strip() or "S1",
        episode_num=payload.episode_num,
        node_type=payload.node_type.strip() or "scene",
        speaker_summary=payload.speaker_summary.strip(),
        script_text=payload.script_text,
        camera_or_acting_cue=payload.camera_or_acting_cue.strip(),
        pos_x=payload.pos_x,
        pos_y=payload.pos_y,
        user_rating=payload.user_rating,
        revision_note=payload.revision_note.strip(),
        node_id=node_id,
    )
    return {"item": item}


@app.patch("/api/nodes/{node_id}/position")
def api_update_node_position(node_id: str, payload: NodePositionRequest):
    db_path = _ensure_db()
    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            "UPDATE story_nodes SET pos_x = ?, pos_y = ?, updated_at = ? WHERE id = ?",
            (float(payload.pos_x), float(payload.pos_y), utc_now_iso(), node_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="找不到该剧情节点")
    finally:
        conn.close()
    return {"ok": True, "node_id": node_id, "pos_x": payload.pos_x, "pos_y": payload.pos_y}


@app.delete("/api/nodes/{node_id}")
def api_delete_node(node_id: str):
    _ensure_db()
    ok = delete_story_node(node_id)
    if not ok:
        raise HTTPException(status_code=404, detail="找不到该剧情节点")
    return {"ok": True}


@app.get("/api/nodes/{node_id}/revisions")
def api_get_node_revisions(node_id: str):
    db_path = _ensure_db()
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, node_id, old_text, new_text, user_rating, note, created_at
            FROM script_revisions
            WHERE node_id = ?
            ORDER BY id DESC
            LIMIT 20
            """,
            (node_id,),
        ).fetchall()
        items = [dict(r) for r in rows]
    finally:
        conn.close()
    return {"items": items}


@app.post("/api/projects/{project_id}/choices")
def api_upsert_choice(project_id: str, payload: StoryChoiceUpsertRequest):
    _ensure_db()
    if not payload.choice_label.strip():
        raise HTTPException(status_code=400, detail="选项按钮文字不能为空")
    item = upsert_story_choice(
        project_id=project_id,
        from_node_id=payload.from_node_id,
        to_node_id=payload.to_node_id,
        choice_label=payload.choice_label.strip(),
        choice_tag=payload.choice_tag.strip(),
        condition_expr=payload.condition_expr.strip(),
        state_delta=payload.state_delta,
        sort_order=payload.sort_order,
        choice_id=payload.choice_id,
    )
    return {"item": item}


@app.delete("/api/choices/{choice_id}")
def api_delete_choice(choice_id: str):
    _ensure_db()
    ok = delete_story_choice(choice_id)
    if not ok:
        raise HTTPException(status_code=404, detail="找不到该分支选项")
    return {"ok": True}


@app.get("/api/projects/{project_id}/export")
@app.post("/api/projects/{project_id}/export")
def api_export_project(project_id: str):
    _ensure_db()
    try:
        markdown = export_branching_script_markdown(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"markdown": markdown}


def run_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Start the local FastAPI visualization server and optionally open a browser."""
    _ensure_db()
    if open_browser:
        url = f"http://{host}:{port}"
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_server()
