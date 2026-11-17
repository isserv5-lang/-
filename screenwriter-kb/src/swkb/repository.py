"""Knowledge-base repository for multi-genre drawers, works, dialogues, characters, and highlights."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import rjieba

from swkb.config import resolve_data_subdir
from swkb.db import get_conn, init_db
from swkb.models import (
    SNIPPET_STATUSES,
    dump_json_field,
    new_id,
    serialize_chapter,
    serialize_character,
    serialize_dialogue_snippet,
    serialize_genre,
    serialize_scene_highlight,
    serialize_work,
    utc_now_iso,
)

_TOKEN_CLEAN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _normalize_db_path(db_path: Path | str | None) -> Path | None:
    if db_path is None:
        return None
    return Path(db_path) if isinstance(db_path, str) else db_path


def _open_conn(db_path: Path | str | None = None) -> tuple[sqlite3.Connection, Path | None]:
    resolved = _normalize_db_path(db_path)
    conn = get_conn(resolved)
    init_db(conn)
    return conn, resolved


def _segment_text(text: str) -> str:
    """Segment Chinese/mixed text into space-separated tokens for FTS5 indexing."""
    if not text:
        return ""
    tokens: list[str] = []
    for tok in rjieba.cut_for_search(text):
        cleaned = tok.strip()
        if cleaned and _TOKEN_CLEAN_RE.search(cleaned):
            tokens.append(cleaned)
    return " ".join(tokens)


def _build_fts_query(query: str) -> str:
    """Build a safe SQLite FTS5 MATCH expression from a user search query."""
    if not query or not query.strip():
        return ""
    terms: list[str] = []
    seen: set[str] = set()
    for tok in rjieba.cut(query.strip()):
        for part in _TOKEN_CLEAN_RE.findall(tok):
            if part and part not in seen:
                seen.add(part)
                escaped = part.replace('"', '""')
                terms.append(f'"{escaped}"')
    return " OR ".join(terms)


def _card_file_path(genre_id: str, snippet_id: str, db_path: Path | None) -> Path:
    safe_genre = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", genre_id.strip()) or "default"
    cards_dir = resolve_data_subdir("cards", db_path) / safe_genre
    cards_dir.mkdir(parents=True, exist_ok=True)
    return cards_dir / f"dialogue_{snippet_id}.md"


def _sync_dialogue_card(snippet: dict[str, Any], db_path: Path | None) -> None:
    """Write a human-readable Markdown backup card when approved, or remove if rejected."""
    genre_id = str(snippet.get("genre_id") or "default")
    snippet_id = str(snippet["id"])
    card_path = _card_file_path(genre_id, snippet_id, db_path)
    if snippet.get("status") != "approved":
        if card_path.exists():
            card_path.unlink()
        return

    lines = [
        f"# 对白梗卡：{snippet_id}",
        "",
        f"- **类型抽屉**：`{genre_id}`",
        f"- **场景标签**：{snippet.get('scene_tag') or '未标注'}",
        f"- **状态 / 评分**：{snippet.get('status')} / {snippet.get('rating', 0)} 星",
        f"- **来源作品 ID**：{snippet.get('work_id') or '手动录入'}",
        f"- **章号 / 行号**：第 {snippet.get('chapter_num') or '-'} 章 (L{snippet.get('line_start') or '-'}-L{snippet.get('line_end') or '-'})",
        f"- **入库时间**：{snippet.get('created_at') or ''}",
        "",
        "## 原话摘录",
        "",
        str(snippet.get("raw_text") or ""),
        "",
    ]
    if snippet.get("trigger_context"):
        lines.extend(["## 触发语境", "", str(snippet["trigger_context"]), ""])
    if snippet.get("why_funny_or_effective"):
        lines.extend(["## 好笑 / 有效原因", "", str(snippet["why_funny_or_effective"]), ""])

    card_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Genres (全类型抽屉)
# ---------------------------------------------------------------------------


def _query_genres(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            g.*,
            (SELECT COUNT(*) FROM works w WHERE w.genre_id = g.id) AS work_count,
            (SELECT COUNT(*) FROM dialogue_snippets d WHERE d.genre_id = g.id AND d.status = 'approved') AS approved_dialogue_count,
            (SELECT COUNT(*) FROM dialogue_snippets d WHERE d.genre_id = g.id AND d.status = 'pending') AS pending_dialogue_count,
            (SELECT COUNT(*) FROM characters c WHERE c.genre_id = g.id) AS character_count,
            (SELECT COUNT(*) FROM scene_highlights h WHERE h.genre_id = g.id) AS highlight_count
        FROM genres g
        ORDER BY g.created_at ASC, g.id ASC
        """
    ).fetchall()
    return [item for r in rows if (item := serialize_genre(r)) is not None]


def list_genres(db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """List all genre drawers with work, approved dialogue, and character counts."""
    conn, _ = _open_conn(db_path)
    try:
        return _query_genres(conn)
    finally:
        conn.close()


def upsert_genre(
    genre_id: str,
    name: str,
    description: str = "",
    channel_hint: str = "",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Create or update a genre drawer."""
    gid = (genre_id or "").strip()
    gname = (name or "").strip()
    if not gid:
        raise ValueError("genre_id 不能为空")
    if not gname:
        raise ValueError("类型抽屉名称不能为空")

    conn, _ = _open_conn(db_path)
    try:
        now = utc_now_iso()
        existing = conn.execute("SELECT id FROM genres WHERE id = ?", (gid,)).fetchone()
        if existing is not None:
            conn.execute(
                """
                UPDATE genres
                SET name = ?, description = ?, channel_hint = ?
                WHERE id = ?
                """,
                (gname, description or "", channel_hint or "", gid),
            )
        else:
            conn.execute(
                """
                INSERT INTO genres (id, name, description, channel_hint, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (gid, gname, description or "", channel_hint or "", now),
            )
        conn.commit()
        row = conn.execute(
            """
            SELECT
                g.*,
                (SELECT COUNT(*) FROM works w WHERE w.genre_id = g.id) AS work_count,
                (SELECT COUNT(*) FROM dialogue_snippets d WHERE d.genre_id = g.id AND d.status = 'approved') AS approved_dialogue_count,
                (SELECT COUNT(*) FROM dialogue_snippets d WHERE d.genre_id = g.id AND d.status = 'pending') AS pending_dialogue_count,
                (SELECT COUNT(*) FROM characters c WHERE c.genre_id = g.id) AS character_count,
                (SELECT COUNT(*) FROM scene_highlights h WHERE h.genre_id = g.id) AS highlight_count
            FROM genres g
            WHERE g.id = ?
            """,
            (gid,),
        ).fetchone()
        result = serialize_genre(row)
        assert result is not None
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Works & Chapters (书架管理)
# ---------------------------------------------------------------------------


def list_works(
    genre_id: str | None = None,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """List imported works, optionally filtered by genre_id."""
    conn, _ = _open_conn(db_path)
    try:
        if genre_id:
            rows = conn.execute(
                "SELECT * FROM works WHERE genre_id = ? ORDER BY created_at DESC",
                (genre_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM works ORDER BY created_at DESC").fetchall()
        return [item for r in rows if (item := serialize_work(r)) is not None]
    finally:
        conn.close()


def get_work_detail(
    work_id: str,
    db_path: Path | str | None = None,
) -> dict[str, Any] | None:
    """Return full work metadata, chapter list, and clean/corrupted chapter statistics."""
    conn, _ = _open_conn(db_path)
    try:
        work_row = conn.execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone()
        if work_row is None:
            return None
        work = serialize_work(work_row)
        assert work is not None

        chapter_rows = conn.execute(
            "SELECT * FROM chapters WHERE work_id = ? ORDER BY chapter_num ASC",
            (work_id,),
        ).fetchall()
        chapters = [ch for r in chapter_rows if (ch := serialize_chapter(r)) is not None]

        clean_list = [ch["chapter_num"] for ch in chapters if not ch.get("is_corrupted")]
        corrupted_list = [
            {
                "chapter_num": ch["chapter_num"],
                "title": ch.get("title") or "",
                "corruption_reason": ch.get("corruption_reason") or "",
            }
            for ch in chapters
            if ch.get("is_corrupted")
        ]

        work["chapters"] = chapters
        work["chapter_stats"] = {
            "total": len(chapters) if chapters else int(work.get("total_chapters") or 0),
            "clean": len(clean_list) if chapters else int(work.get("clean_chapters") or 0),
            "corrupted": len(corrupted_list) if chapters else int(work.get("corrupted_chapters") or 0),
            "clean_chapter_nums": clean_list,
            "corrupted_chapters": corrupted_list,
        }
        return work
    finally:
        conn.close()


def update_work_summary(
    work_id: str,
    summary_one_page: str,
    style_notes: str = "",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Update a work's one-page summary and style notes."""
    conn, _ = _open_conn(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE works
            SET summary_one_page = ?, style_notes = ?
            WHERE id = ?
            """,
            (summary_one_page or "", style_notes or "", work_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"找不到作品：{work_id}")
        row = conn.execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone()
        result = serialize_work(row)
        assert result is not None
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dialogue Snippets & Taste Check (对白与梗真题本)
# ---------------------------------------------------------------------------


def list_dialogue_snippets(
    genre_id: str | None = None,
    status: str = "approved",
    query: str = "",
    scene_tag: str = "",
    limit: int = 30,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Query dialogue snippets with genre isolation, status filter, scene tag, and FTS5 search."""
    conn, _ = _open_conn(db_path)
    try:
        safe_limit = max(1, min(int(limit), 500))
        conditions: list[str] = []
        filter_params: list[Any] = []

        if genre_id:
            conditions.append("ds.genre_id = ?")
            filter_params.append(genre_id)
        if status and status != "all":
            conditions.append("ds.status = ?")
            filter_params.append(status)
        if scene_tag and scene_tag.strip():
            conditions.append("ds.scene_tag LIKE ?")
            filter_params.append(f"%{scene_tag.strip()}%")

        q_clean = (query or "").strip()
        fts_expr = _build_fts_query(q_clean)

        if q_clean:
            like_pat = f"%{q_clean}%"
            if fts_expr:
                conditions.append(
                    "(fts_hits.snippet_id IS NOT NULL OR ds.raw_text LIKE ? OR ds.scene_tag LIKE ? OR ds.why_funny_or_effective LIKE ?)"
                )
                where_sql = " AND ".join(conditions)
                sql = f"""
                    WITH fts_hits AS (
                        SELECT snippet_id, bm25(dialogue_fts) AS rank
                        FROM dialogue_fts
                        WHERE dialogue_fts MATCH ?
                    )
                    SELECT ds.*, w.title AS work_title
                    FROM dialogue_snippets ds
                    LEFT JOIN works w ON ds.work_id = w.id
                    LEFT JOIN fts_hits ON fts_hits.snippet_id = ds.id
                    WHERE {where_sql}
                    ORDER BY (fts_hits.snippet_id IS NULL) ASC, COALESCE(fts_hits.rank, 999.0) ASC, ds.rating DESC, ds.created_at DESC
                    LIMIT ?
                """
                params = [fts_expr, *filter_params, like_pat, like_pat, like_pat, safe_limit]
            else:
                conditions.append(
                    "(ds.raw_text LIKE ? OR ds.scene_tag LIKE ? OR ds.why_funny_or_effective LIKE ?)"
                )
                where_sql = " AND ".join(conditions)
                sql = f"""
                    SELECT ds.*, w.title AS work_title
                    FROM dialogue_snippets ds
                    LEFT JOIN works w ON ds.work_id = w.id
                    WHERE {where_sql}
                    ORDER BY ds.rating DESC, ds.created_at DESC
                    LIMIT ?
                """
                params = [*filter_params, like_pat, like_pat, like_pat, safe_limit]
        else:
            where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            sql = f"""
                SELECT ds.*, w.title AS work_title
                FROM dialogue_snippets ds
                LEFT JOIN works w ON ds.work_id = w.id
                {where_sql}
                ORDER BY ds.rating DESC, ds.created_at DESC
                LIMIT ?
            """
            params = [*filter_params, safe_limit]

        rows = conn.execute(sql, params).fetchall()
        return [item for r in rows if (item := serialize_dialogue_snippet(r)) is not None]
    finally:
        conn.close()


def save_dialogue_snippet(
    genre_id: str,
    raw_text: str,
    work_id: str | None = None,
    chapter_num: int | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    scene_tag: str = "",
    why_funny_or_effective: str = "",
    trigger_context: str = "",
    status: str = "approved",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Save a dialogue snippet, index via rjieba into FTS5, and write card backup if approved."""
    if not raw_text or not raw_text.strip():
        raise ValueError("对白原文 raw_text 不能为空")
    if status not in SNIPPET_STATUSES:
        raise ValueError(f"无效的 status：{status}，必须是 {SNIPPET_STATUSES}")

    conn, resolved = _open_conn(db_path)
    try:
        snippet_id = new_id("dlg")
        now = utc_now_iso()
        clean_raw = raw_text.strip()
        clean_tag = (scene_tag or "").strip()
        seg_tokens = _segment_text(f"{clean_tag} {clean_raw}")

        conn.execute(
            """
            INSERT INTO dialogue_snippets (
                id, work_id, genre_id, chapter_num, line_start, line_end,
                scene_tag, raw_text, why_funny_or_effective, trigger_context,
                status, rating, seg_tokens, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snippet_id,
                work_id or None,
                genre_id or "mengwa",
                chapter_num,
                line_start,
                line_end,
                clean_tag,
                clean_raw,
                (why_funny_or_effective or "").strip(),
                (trigger_context or "").strip(),
                status,
                0,
                seg_tokens,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO dialogue_fts (snippet_id, scene_tag, raw_text, seg_tokens)
            VALUES (?, ?, ?, ?)
            """,
            (snippet_id, clean_tag, clean_raw, seg_tokens),
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM dialogue_snippets WHERE id = ?",
            (snippet_id,),
        ).fetchone()
        snippet = serialize_dialogue_snippet(row)
        assert snippet is not None
        _sync_dialogue_card(snippet, resolved)
        return snippet
    finally:
        conn.close()


def review_dialogue_snippet(
    snippet_id: str,
    status: str,
    rating: int = 0,
    scene_tag: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Approve or reject a candidate dialogue snippet (taste check) and sync card/FTS."""
    if status not in SNIPPET_STATUSES:
        raise ValueError(f"无效的 status：{status}，必须是 {SNIPPET_STATUSES}")

    conn, resolved = _open_conn(db_path)
    try:
        existing = conn.execute(
            "SELECT * FROM dialogue_snippets WHERE id = ?",
            (snippet_id,),
        ).fetchone()
        if existing is None:
            raise ValueError(f"找不到对白片段：{snippet_id}")

        new_tag = existing["scene_tag"] if scene_tag is None else scene_tag.strip()
        new_seg = _segment_text(f"{new_tag} {existing['raw_text']}") if scene_tag is not None else existing["seg_tokens"]

        conn.execute(
            """
            UPDATE dialogue_snippets
            SET status = ?, rating = ?, scene_tag = ?, seg_tokens = ?
            WHERE id = ?
            """,
            (status, int(rating), new_tag, new_seg, snippet_id),
        )
        if scene_tag is not None:
            conn.execute("DELETE FROM dialogue_fts WHERE snippet_id = ?", (snippet_id,))
            conn.execute(
                """
                INSERT INTO dialogue_fts (snippet_id, scene_tag, raw_text, seg_tokens)
                VALUES (?, ?, ?, ?)
                """,
                (snippet_id, new_tag, existing["raw_text"], new_seg),
            )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM dialogue_snippets WHERE id = ?",
            (snippet_id,),
        ).fetchone()
        snippet = serialize_dialogue_snippet(row)
        assert snippet is not None
        _sync_dialogue_card(snippet, resolved)
        return snippet
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Characters (人物专属声音卡)
# ---------------------------------------------------------------------------


def upsert_character(
    name: str,
    traits: str,
    work_id: str | None = None,
    genre_id: str = "mengwa",
    role_type: str = "",
    speech_style: str = "",
    catchphrases: str = "",
    forbidden_words: str = "",
    sample_lines: list[str] | str | None = None,
    char_id: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Create or update a character voice card."""
    if not name or not name.strip():
        raise ValueError("人物姓名 name 不能为空")
    if not traits or not traits.strip():
        raise ValueError("人物特征 traits 不能为空")

    conn, _ = _open_conn(db_path)
    try:
        cid = (char_id or "").strip() or new_id("char")
        sample_json = dump_json_field(sample_lines, default=[])
        now = utc_now_iso()

        existing = conn.execute("SELECT id FROM characters WHERE id = ?", (cid,)).fetchone()
        if existing is not None:
            conn.execute(
                """
                UPDATE characters
                SET work_id = ?, genre_id = ?, name = ?, role_type = ?, traits = ?,
                    speech_style = ?, catchphrases = ?, forbidden_words = ?, sample_lines_json = ?
                WHERE id = ?
                """,
                (
                    work_id or None,
                    genre_id or "mengwa",
                    name.strip(),
                    (role_type or "").strip(),
                    traits.strip(),
                    (speech_style or "").strip(),
                    (catchphrases or "").strip(),
                    (forbidden_words or "").strip(),
                    sample_json,
                    cid,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO characters (
                    id, work_id, genre_id, name, role_type, traits,
                    speech_style, catchphrases, forbidden_words, sample_lines_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cid,
                    work_id or None,
                    genre_id or "mengwa",
                    name.strip(),
                    (role_type or "").strip(),
                    traits.strip(),
                    (speech_style or "").strip(),
                    (catchphrases or "").strip(),
                    (forbidden_words or "").strip(),
                    sample_json,
                    now,
                ),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM characters WHERE id = ?", (cid,)).fetchone()
        result = serialize_character(row)
        assert result is not None
        return result
    finally:
        conn.close()


def list_characters(
    work_id: str | None = None,
    genre_id: str | None = None,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """List character voice cards filtered by work_id and/or genre_id."""
    conn, _ = _open_conn(db_path)
    try:
        conditions: list[str] = []
        params: list[Any] = []
        if work_id:
            conditions.append("work_id = ?")
            params.append(work_id)
        if genre_id:
            conditions.append("genre_id = ?")
            params.append(genre_id)
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"SELECT * FROM characters {where_sql} ORDER BY created_at DESC",
            params,
        ).fetchall()
        return [item for r in rows if (item := serialize_character(r)) is not None]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Scene Highlights (精彩场景钩子卡)
# ---------------------------------------------------------------------------


def save_scene_highlight(
    genre_id: str,
    plain_summary: str,
    work_id: str | None = None,
    chapter_range: str = "",
    line_start: int | None = None,
    line_end: int | None = None,
    hook_type: str = "",
    why_it_hooks: str = "",
    raw_excerpt: str = "",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Save a scene highlight hook card."""
    if not plain_summary or not plain_summary.strip():
        raise ValueError("场景大白话摘要 plain_summary 不能为空")

    conn, _ = _open_conn(db_path)
    try:
        hid = new_id("hl")
        now = utc_now_iso()
        conn.execute(
            """
            INSERT INTO scene_highlights (
                id, work_id, genre_id, chapter_range, line_start, line_end,
                hook_type, plain_summary, why_it_hooks, raw_excerpt, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hid,
                work_id or None,
                genre_id or "mengwa",
                (chapter_range or "").strip(),
                line_start,
                line_end,
                (hook_type or "").strip(),
                plain_summary.strip(),
                (why_it_hooks or "").strip(),
                (raw_excerpt or "").strip(),
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM scene_highlights WHERE id = ?", (hid,)).fetchone()
        result = serialize_scene_highlight(row)
        assert result is not None
        return result
    finally:
        conn.close()


def list_scene_highlights(
    work_id: str | None = None,
    genre_id: str | None = None,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """List scene highlight hook cards filtered by work_id and/or genre_id."""
    conn, _ = _open_conn(db_path)
    try:
        conditions: list[str] = []
        params: list[Any] = []
        if work_id:
            conditions.append("work_id = ?")
            params.append(work_id)
        if genre_id:
            conditions.append("genre_id = ?")
            params.append(genre_id)
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"SELECT * FROM scene_highlights {where_sql} ORDER BY created_at DESC",
            params,
        ).fetchall()
        return [item for r in rows if (item := serialize_scene_highlight(r)) is not None]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dashboard Stats (全局统计)
# ---------------------------------------------------------------------------


def get_dashboard_stats(db_path: Path | str | None = None) -> dict[str, Any]:
    """Return global dashboard statistics across genres, works, dialogues, rules, and projects."""
    conn, _ = _open_conn(db_path)
    try:
        def _count(sql: str, params: tuple[Any, ...] = ()) -> int:
            row = conn.execute(sql, params).fetchone()
            return int(row[0]) if row is not None else 0

        genres = _query_genres(conn)
        return {
            "total_genres": len(genres),
            "total_works": _count("SELECT COUNT(*) FROM works"),
            "total_chapters": _count("SELECT COUNT(*) FROM chapters"),
            "clean_chapters": _count("SELECT COUNT(*) FROM chapters WHERE is_corrupted = 0"),
            "corrupted_chapters": _count("SELECT COUNT(*) FROM chapters WHERE is_corrupted = 1"),
            "total_approved_dialogues": _count(
                "SELECT COUNT(*) FROM dialogue_snippets WHERE status = 'approved'"
            ),
            "pending_taste_count": _count(
                "SELECT COUNT(*) FROM dialogue_snippets WHERE status = 'pending'"
            ),
            "rejected_dialogue_count": _count(
                "SELECT COUNT(*) FROM dialogue_snippets WHERE status = 'rejected'"
            ),
            "total_characters": _count("SELECT COUNT(*) FROM characters"),
            "total_highlights": _count("SELECT COUNT(*) FROM scene_highlights"),
            "total_jinghua_rules": _count("SELECT COUNT(*) FROM jinghua_lock_rules"),
            "enabled_jinghua_rules": _count(
                "SELECT COUNT(*) FROM jinghua_lock_rules WHERE enabled = 1"
            ),
            "total_script_projects": _count("SELECT COUNT(*) FROM script_projects"),
            "genre_breakdown": genres,
        }
    finally:
        conn.close()
