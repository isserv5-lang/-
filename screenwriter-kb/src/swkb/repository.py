"""Knowledge-base repository for multi-genre drawers, works, dialogues, characters, and highlights."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import rjieba

from swkb.config import resolve_data_subdir
from swkb.db import get_conn, init_db
from swkb.taxonomy import CHANNEL_BY_ID, CHANNEL_BY_NAME, CHANNELS
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


def _normalize_channel(raw: str | None) -> str:
    """Normalize a channel identifier or Chinese label into 女频 / 男频 / 通用."""
    val = (raw or "").strip()
    if not val:
        return "通用"
    low = val.lower()
    if low in CHANNEL_BY_ID:
        return str(CHANNEL_BY_ID[low]["name"])
    if val in CHANNEL_BY_NAME:
        return val
    return "通用"


def _channel_id_for_name(channel_name: str) -> str:
    entry = CHANNEL_BY_NAME.get(channel_name)
    if entry is not None:
        return str(entry["id"])
    return "common"


def _query_genres(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            g.*,
            (
                SELECT COUNT(DISTINCT wt.work_id)
                FROM work_tags wt
                INNER JOIN works w ON w.id = wt.work_id
                WHERE wt.tag_id = g.id
            ) AS work_count,
            (SELECT COUNT(*) FROM dialogue_snippets d WHERE d.genre_id = g.id AND d.status = 'approved') AS approved_dialogue_count,
            (SELECT COUNT(*) FROM dialogue_snippets d WHERE d.genre_id = g.id AND d.status = 'pending') AS pending_dialogue_count,
            (SELECT COUNT(*) FROM characters c WHERE c.genre_id = g.id) AS character_count,
            (SELECT COUNT(*) FROM scene_highlights h WHERE h.genre_id = g.id) AS highlight_count
        FROM genres g
        ORDER BY
            CASE g.channel
                WHEN '女频' THEN 1
                WHEN '男频' THEN 2
                WHEN '通用' THEN 3
                ELSE 4
            END ASC,
            CASE WHEN COALESCE(g.sort_order, 0) > 0 THEN g.sort_order ELSE 999 END ASC,
            g.created_at ASC,
            g.id ASC
        """
    ).fetchall()
    items: list[dict[str, Any]] = []
    for r in rows:
        item = serialize_genre(r)
        if item is not None:
            item["channel"] = _normalize_channel(item.get("channel"))
            item["sub_group"] = str(item.get("sub_group") or "题材主干")
            item["platforms"] = str(item.get("platforms") or "")
            item["sort_order"] = int(item.get("sort_order") or 0)
            items.append(item)
    return items


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
    channel: str = "通用",
    sub_group: str = "题材主干",
    platforms: str = "",
    sort_order: int | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Create or update a genre drawer."""
    gid = (genre_id or "").strip()
    gname = (name or "").strip()
    if not gid:
        raise ValueError("genre_id 不能为空")
    if not gname:
        raise ValueError("类型抽屉名称不能为空")

    norm_channel = _normalize_channel(channel)
    norm_sub_group = (sub_group or "").strip() or "题材主干"
    norm_platforms = (platforms or "").strip()

    conn, _ = _open_conn(db_path)
    try:
        now = utc_now_iso()
        existing = conn.execute(
            "SELECT id, sort_order FROM genres WHERE id = ?",
            (gid,),
        ).fetchone()
        if existing is not None:
            resolved_sort = (
                int(sort_order)
                if sort_order is not None
                else int(existing["sort_order"] or 0)
            )
            conn.execute(
                """
                UPDATE genres
                SET name = ?, description = ?, channel_hint = ?,
                    channel = ?, sub_group = ?, platforms = ?, sort_order = ?
                WHERE id = ?
                """,
                (
                    gname,
                    description or "",
                    channel_hint or "",
                    norm_channel,
                    norm_sub_group,
                    norm_platforms,
                    resolved_sort,
                    gid,
                ),
            )
        else:
            resolved_sort = int(sort_order) if sort_order is not None else 100
            conn.execute(
                """
                INSERT INTO genres (
                    id, name, description, channel_hint,
                    channel, sub_group, platforms, sort_order, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gid,
                    gname,
                    description or "",
                    channel_hint or "",
                    norm_channel,
                    norm_sub_group,
                    norm_platforms,
                    resolved_sort,
                    now,
                ),
            )
        conn.commit()
        row = conn.execute(
            """
            SELECT
                g.*,
                (
                    SELECT COUNT(DISTINCT wt.work_id)
                    FROM work_tags wt
                    INNER JOIN works w ON w.id = wt.work_id
                    WHERE wt.tag_id = g.id
                ) AS work_count,
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
        result["channel"] = _normalize_channel(result.get("channel"))
        result["sub_group"] = str(result.get("sub_group") or "题材主干")
        result["platforms"] = str(result.get("platforms") or "")
        result["sort_order"] = int(result.get("sort_order") or 0)
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Works, Multi-Tag Associations & Chapters (书架与一书多标签管理)
# ---------------------------------------------------------------------------

MAX_TAGS_PER_WORK = 6


def _normalize_tag_ids(raw: list[str] | tuple[str, ...] | str | None) -> list[str]:
    """Normalize a list or comma-separated string of tag IDs into a deduplicated list."""
    if raw is None:
        return []
    parts: list[str]
    if isinstance(raw, str):
        parts = re.split(r"[,，;；\s]+", raw.strip())
    else:
        parts = [str(item) for item in raw]
    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tid = part.strip()
        if tid and tid not in seen:
            seen.add(tid)
            cleaned.append(tid)
    return cleaned


def _format_work_tag_row(row: sqlite3.Row) -> dict[str, Any]:
    ch_name = _normalize_channel(row["channel"])
    return {
        "id": str(row["tag_id"]),
        "name": str(row["name"] or row["tag_id"]),
        "channel": ch_name,
        "channel_id": _channel_id_for_name(ch_name),
        "sub_group": str(row["sub_group"] or "题材主干"),
        "is_primary": 1 if int(row["is_primary"] or 0) else 0,
    }


def _query_work_tags_conn(
    conn: sqlite3.Connection,
    work_id: str,
    fallback_genre_id: str | None = None,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            wt.tag_id,
            wt.is_primary,
            g.name,
            g.channel,
            g.sub_group
        FROM work_tags wt
        LEFT JOIN genres g ON g.id = wt.tag_id
        WHERE wt.work_id = ?
        ORDER BY wt.is_primary DESC, wt.rowid ASC
        """,
        (work_id,),
    ).fetchall()
    tags = [_format_work_tag_row(r) for r in rows]
    if not tags and fallback_genre_id and fallback_genre_id.strip():
        gid = fallback_genre_id.strip()
        g_row = conn.execute(
            "SELECT id, name, channel, sub_group FROM genres WHERE id = ?",
            (gid,),
        ).fetchone()
        ch_name = _normalize_channel(g_row["channel"] if g_row is not None else "通用")
        tags = [
            {
                "id": gid,
                "name": str(g_row["name"]) if g_row is not None else gid,
                "channel": ch_name,
                "channel_id": _channel_id_for_name(ch_name),
                "sub_group": str(g_row["sub_group"] or "题材主干") if g_row is not None else "题材主干",
                "is_primary": 1,
            }
        ]
    return tags


def _query_all_work_tags_map(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT
            wt.work_id,
            wt.tag_id,
            wt.is_primary,
            g.name,
            g.channel,
            g.sub_group
        FROM work_tags wt
        LEFT JOIN genres g ON g.id = wt.tag_id
        ORDER BY wt.work_id ASC, wt.is_primary DESC, wt.rowid ASC
        """
    ).fetchall()
    mapping: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        wid = str(r["work_id"])
        mapping.setdefault(wid, []).append(_format_work_tag_row(r))
    return mapping


def _attach_tags_to_work(
    work: dict[str, Any],
    tags: list[dict[str, Any]],
) -> dict[str, Any]:
    work["tags"] = tags
    work["tag_ids"] = [t["id"] for t in tags]
    return work


def get_work_tags(
    work_id: str,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Return all tags associated with a work, ordered with the primary tag first."""
    conn, _ = _open_conn(db_path)
    try:
        work_row = conn.execute(
            "SELECT id, genre_id FROM works WHERE id = ?",
            (work_id,),
        ).fetchone()
        if work_row is None:
            return []
        return _query_work_tags_conn(conn, work_id, fallback_genre_id=work_row["genre_id"])
    finally:
        conn.close()


def set_work_tags(
    work_id: str,
    primary_tag_id: str,
    extra_tag_ids: list[str] | None = None,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Update a work's primary genre_id and synchronize all primary/secondary rows in work_tags."""
    wid = (work_id or "").strip()
    primary = (primary_tag_id or "").strip()
    if not wid:
        raise ValueError("work_id 不能为空")
    if not primary:
        raise ValueError("主标签 primary_tag_id 不能为空")

    extras = [
        tid
        for tid in _normalize_tag_ids(extra_tag_ids)
        if tid != primary
    ][: MAX_TAGS_PER_WORK - 1]

    conn, _ = _open_conn(db_path)
    try:
        work_row = conn.execute("SELECT id FROM works WHERE id = ?", (wid,)).fetchone()
        if work_row is None:
            raise ValueError(f"找不到作品：{wid}")

        now = utc_now_iso()
        conn.execute(
            "UPDATE works SET genre_id = ? WHERE id = ?",
            (primary, wid),
        )
        conn.execute("DELETE FROM work_tags WHERE work_id = ?", (wid,))
        conn.execute(
            """
            INSERT INTO work_tags (work_id, tag_id, is_primary, created_at)
            VALUES (?, ?, 1, ?)
            """,
            (wid, primary, now),
        )
        for tid in extras:
            conn.execute(
                """
                INSERT OR IGNORE INTO work_tags (work_id, tag_id, is_primary, created_at)
                VALUES (?, ?, 0, ?)
                """,
                (wid, tid, now),
            )
        conn.commit()
        return _query_work_tags_conn(conn, wid, fallback_genre_id=primary)
    finally:
        conn.close()


def list_works(
    genre_id: str | None = None,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """List imported works (including multi-tag details), optionally filtered by any linked tag."""
    conn, _ = _open_conn(db_path)
    try:
        if genre_id:
            rows = conn.execute(
                """
                SELECT DISTINCT w.*
                FROM works w
                LEFT JOIN work_tags wt ON wt.work_id = w.id
                WHERE w.genre_id = ? OR wt.tag_id = ?
                ORDER BY w.created_at DESC
                """,
                (genre_id, genre_id),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM works ORDER BY created_at DESC").fetchall()

        tags_map = _query_all_work_tags_map(conn)
        works: list[dict[str, Any]] = []
        for r in rows:
            item = serialize_work(r)
            if item is not None:
                wid = str(item["id"])
                w_tags = tags_map.get(wid) or _query_work_tags_conn(
                    conn, wid, fallback_genre_id=item.get("genre_id")
                )
                works.append(_attach_tags_to_work(item, w_tags))
        return works
    finally:
        conn.close()


def get_work_detail(
    work_id: str,
    db_path: Path | str | None = None,
) -> dict[str, Any] | None:
    """Return full work metadata, multi-tag info, chapter list, and clean/corrupted chapter statistics."""
    conn, _ = _open_conn(db_path)
    try:
        work_row = conn.execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone()
        if work_row is None:
            return None
        work = serialize_work(work_row)
        assert work is not None

        w_tags = _query_work_tags_conn(
            conn, work_id, fallback_genre_id=work.get("genre_id")
        )
        _attach_tags_to_work(work, w_tags)

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
        w_tags = _query_work_tags_conn(
            conn, work_id, fallback_genre_id=result.get("genre_id")
        )
        return _attach_tags_to_work(result, w_tags)
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
    work_id: str | None = None,
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
        if work_id:
            conditions.append("ds.work_id = ?")
            filter_params.append(work_id)
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
    genre_id: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Approve or reject a candidate dialogue snippet (optionally reassigning its genre_id) and sync card/FTS."""
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

        old_genre_id = str(existing["genre_id"] or "default")
        new_genre_id = (
            genre_id.strip()
            if (genre_id is not None and genre_id.strip())
            else old_genre_id
        )
        new_tag = existing["scene_tag"] if scene_tag is None else scene_tag.strip()
        new_seg = (
            _segment_text(f"{new_tag} {existing['raw_text']}")
            if (scene_tag is not None or not existing["seg_tokens"])
            else existing["seg_tokens"]
        )

        conn.execute(
            """
            UPDATE dialogue_snippets
            SET status = ?, rating = ?, scene_tag = ?, genre_id = ?, seg_tokens = ?
            WHERE id = ?
            """,
            (status, int(rating), new_tag, new_genre_id, new_seg, snippet_id),
        )
        if scene_tag is not None or new_genre_id != old_genre_id:
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
        if old_genre_id != new_genre_id:
            old_card = _card_file_path(old_genre_id, snippet_id, resolved)
            if old_card.exists():
                old_card.unlink()
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


def get_graph_universe(db_path: Path | str | None = None) -> dict[str, Any]:
    """Return aggregated 3-channel knowledge graph payload (root, channels, tags, works, work_tag_links)."""
    conn, _ = _open_conn(db_path)
    try:
        def _count(sql: str, params: tuple[Any, ...] = ()) -> int:
            row = conn.execute(sql, params).fetchone()
            return int(row[0]) if row is not None else 0

        raw_genres = _query_genres(conn)
        channel_buckets: dict[str, dict[str, Any]] = {}
        channel_work_ids: dict[str, set[str]] = {}
        channels_list: list[dict[str, Any]] = []
        for ch in CHANNELS:
            bucket = {
                "id": str(ch["id"]),
                "name": str(ch["name"]),
                "description": str(ch.get("description") or ""),
                "sort_order": int(ch.get("sort_order") or 0),
                "tags_count": 0,
                "works_count": 0,
                "approved_count": 0,
                "pending_count": 0,
                "characters_count": 0,
                "highlights_count": 0,
            }
            channel_buckets[bucket["name"]] = bucket
            channel_work_ids[bucket["name"]] = set()
            channels_list.append(bucket)

        tag_channel_map: dict[str, str] = {}
        tags: list[dict[str, Any]] = []
        for g in raw_genres:
            ch_name = _normalize_channel(g.get("channel"))
            ch_id = _channel_id_for_name(ch_name)
            works_count = int(g.get("work_count") or 0)
            approved_count = int(g.get("approved_dialogue_count") or 0)
            pending_count = int(g.get("pending_dialogue_count") or 0)
            characters_count = int(g.get("character_count") or 0)
            highlights_count = int(g.get("highlight_count") or 0)

            tag_id_str = str(g["id"])
            tag_channel_map[tag_id_str] = ch_name

            tag_item = {
                "id": tag_id_str,
                "name": str(g["name"]),
                "channel": ch_name,
                "channel_id": ch_id,
                "sub_group": str(g.get("sub_group") or "题材主干"),
                "platforms": str(g.get("platforms") or ""),
                "description": str(g.get("description") or ""),
                "channel_hint": str(g.get("channel_hint") or ""),
                "sort_order": int(g.get("sort_order") or 0),
                "works_count": works_count,
                "approved_count": approved_count,
                "pending_count": pending_count,
                "characters_count": characters_count,
                "highlights_count": highlights_count,
            }
            tags.append(tag_item)

            target_bucket = channel_buckets.get(ch_name) or channel_buckets["通用"]
            target_bucket["tags_count"] += 1
            target_bucket["approved_count"] += approved_count
            target_bucket["pending_count"] += pending_count
            target_bucket["characters_count"] += characters_count
            target_bucket["highlights_count"] += highlights_count

        tags_map = _query_all_work_tags_map(conn)
        work_rows = conn.execute(
            """
            SELECT
                w.id,
                w.title,
                w.author,
                w.genre_id,
                w.total_chapters,
                w.clean_chapters,
                w.corrupted_chapters,
                w.total_chars,
                w.created_at,
                (SELECT COUNT(*) FROM dialogue_snippets d WHERE d.work_id = w.id AND d.status = 'approved') AS approved_count,
                (SELECT COUNT(*) FROM dialogue_snippets d WHERE d.work_id = w.id AND d.status = 'pending') AS pending_count
            FROM works w
            ORDER BY w.created_at DESC
            """
        ).fetchall()

        works: list[dict[str, Any]] = []
        work_tag_links: list[dict[str, Any]] = []
        for r in work_rows:
            wid = str(r["id"])
            primary_gid = str(r["genre_id"] or "")
            w_tags = tags_map.get(wid) or _query_work_tags_conn(
                conn, wid, fallback_genre_id=primary_gid
            )
            w_tag_ids = [t["id"] for t in w_tags]
            for t in w_tags:
                tid = str(t["id"])
                work_tag_links.append(
                    {
                        "work_id": wid,
                        "tag_id": tid,
                        "is_primary": 1 if int(t.get("is_primary") or 0) else 0,
                    }
                )
                ch_for_tag = tag_channel_map.get(tid) or _normalize_channel(t.get("channel"))
                if ch_for_tag in channel_work_ids:
                    channel_work_ids[ch_for_tag].add(wid)

            works.append(
                {
                    "id": wid,
                    "title": str(r["title"]),
                    "author": str(r["author"] or ""),
                    "genre_id": primary_gid,
                    "tag_ids": w_tag_ids,
                    "tags": w_tags,
                    "total_chapters": int(r["total_chapters"] or 0),
                    "clean_chapters": int(r["clean_chapters"] or 0),
                    "corrupted_chapters": int(r["corrupted_chapters"] or 0),
                    "total_chars": int(r["total_chars"] or 0),
                    "approved_count": int(r["approved_count"] or 0),
                    "pending_count": int(r["pending_count"] or 0),
                }
            )

        for ch_name, w_set in channel_work_ids.items():
            channel_buckets[ch_name]["works_count"] = len(w_set)

        root = {
            "id": "root",
            "name": "编剧总库",
            "total_tags": len(tags),
            "total_works": _count("SELECT COUNT(*) FROM works"),
            "approved_count": _count(
                "SELECT COUNT(*) FROM dialogue_snippets WHERE status = 'approved'"
            ),
            "pending_count": _count(
                "SELECT COUNT(*) FROM dialogue_snippets WHERE status = 'pending'"
            ),
            "characters_count": _count("SELECT COUNT(*) FROM characters"),
            "highlights_count": _count("SELECT COUNT(*) FROM scene_highlights"),
            "total_jinghua_rules": _count("SELECT COUNT(*) FROM jinghua_lock_rules"),
            "enabled_jinghua_rules": _count(
                "SELECT COUNT(*) FROM jinghua_lock_rules WHERE enabled = 1"
            ),
            "total_script_projects": _count("SELECT COUNT(*) FROM script_projects"),
        }

        return {
            "root": root,
            "channels": channels_list,
            "tags": tags,
            "works": works,
            "work_tag_links": work_tag_links,
        }
    finally:
        conn.close()
