"""Interactive branching drama & short-drama script graph engine for swkb."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import deque
from pathlib import Path
from typing import Any

from swkb.config import resolve_data_subdir
from swkb.db import get_conn, init_db
from swkb.jinghua_lock import check_text_with_jinghua_lock
from swkb.models import (
    ENDING_NODE_TYPES,
    NODE_TYPES,
    PROJECT_MODES,
    dump_json_field,
    new_id,
    serialize_script_project,
    serialize_script_revision,
    serialize_story_choice,
    serialize_story_node,
    utc_now_iso,
)

_NODE_TYPE_LABELS: dict[str, str] = {
    "start": "起始幕",
    "scene": "普通剧情",
    "climax": "高潮反转",
    "good_ending": "好结局",
    "bad_ending": "坏结局",
    "hidden_ending": "隐藏结局",
}


def _normalize_db_path(db_path: Path | str | None) -> Path | None:
    if db_path is None:
        return None
    return Path(db_path) if isinstance(db_path, str) else db_path


def _open_conn(db_path: Path | str | None = None) -> tuple[sqlite3.Connection, Path | None]:
    resolved = _normalize_db_path(db_path)
    conn = get_conn(resolved)
    init_db(conn)
    return conn, resolved


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


def create_script_project(
    title: str,
    genre_id: str = "hudong",
    project_mode: str = "branching",
    synopsis: str = "",
    state_vars: dict[str, Any] | str | None = None,
    target_style_notes: str = "",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Create a new short_drama or branching interactive script project."""
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("剧本标题 title 不能为空")
    mode = (project_mode or "branching").strip()
    if mode not in PROJECT_MODES:
        raise ValueError(f"无效的 project_mode：{mode}，必须是 {PROJECT_MODES}")

    conn, _ = _open_conn(db_path)
    try:
        pid = new_id("proj")
        now = utc_now_iso()
        state_json = dump_json_field(state_vars, default={})
        conn.execute(
            """
            INSERT INTO script_projects (
                id, title, genre_id, project_mode, synopsis,
                state_vars_json, target_style_notes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                clean_title,
                (genre_id or "hudong").strip() or "hudong",
                mode,
                (synopsis or "").strip(),
                state_json,
                (target_style_notes or "").strip(),
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM script_projects WHERE id = ?", (pid,)).fetchone()
        result = serialize_script_project(row)
        assert result is not None
        return result
    finally:
        conn.close()


def list_script_projects(db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """List all script projects with node, choice, and ending counts."""
    conn, _ = _open_conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT
                p.*,
                (SELECT COUNT(*) FROM story_nodes n WHERE n.project_id = p.id) AS node_count,
                (SELECT COUNT(*) FROM story_choices c WHERE c.project_id = p.id) AS choice_count,
                (SELECT COUNT(*) FROM story_nodes n WHERE n.project_id = p.id AND n.node_type IN ('good_ending', 'bad_ending', 'hidden_ending')) AS ending_count
            FROM script_projects p
            ORDER BY p.updated_at DESC, p.created_at DESC
            """
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = serialize_script_project(row)
            if item is not None:
                for key in ("node_count", "choice_count", "ending_count"):
                    if key in item and item[key] is not None:
                        item[key] = int(item[key])
                results.append(item)
        return results
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Nodes & Revisions
# ---------------------------------------------------------------------------


def list_node_revisions(
    node_id: str,
    limit: int = 20,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Return revision history records for a story node, newest first."""
    conn, _ = _open_conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM script_revisions
            WHERE node_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (node_id, max(1, int(limit))),
        ).fetchall()
        return [item for r in rows if (item := serialize_script_revision(r)) is not None]
    finally:
        conn.close()


def upsert_story_node(
    project_id: str,
    title: str,
    node_code: str = "S1",
    episode_num: int = 1,
    node_type: str = "scene",
    speaker_summary: str = "",
    script_text: str = "",
    camera_or_acting_cue: str = "",
    pos_x: float = 120,
    pos_y: float = 120,
    user_rating: int = 0,
    revision_note: str = "",
    node_id: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Create or update a story node, auto-scoring with Jinghua Lock and logging text revisions."""
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("场次标题 title 不能为空")
    clean_type = (node_type or "scene").strip()
    if clean_type not in NODE_TYPES:
        raise ValueError(f"无效的 node_type：{clean_type}，必须是 {NODE_TYPES}")

    conn, resolved = _open_conn(db_path)
    try:
        proj = conn.execute("SELECT id FROM script_projects WHERE id = ?", (project_id,)).fetchone()
        if proj is None:
            raise ValueError(f"找不到剧本工程：{project_id}")

        new_text = script_text or ""
        if new_text.strip():
            lock_result = check_text_with_jinghua_lock(new_text, db_path=resolved)
            jinghua_score = int(lock_result.get("score", 100))
        else:
            lock_result = {"passed": True, "score": 100, "hits": [], "rewrite_guide": ""}
            jinghua_score = 100

        now = utc_now_iso()
        nid = (node_id or "").strip() or new_id("node")
        existing = conn.execute("SELECT * FROM story_nodes WHERE id = ?", (nid,)).fetchone()

        if existing is not None:
            old_text = existing["script_text"] or ""
            if old_text != new_text:
                conn.execute(
                    """
                    INSERT INTO script_revisions (node_id, old_text, new_text, user_rating, note, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        nid,
                        old_text,
                        new_text,
                        int(user_rating),
                        (revision_note or "").strip(),
                        now,
                    ),
                )
            conn.execute(
                """
                UPDATE story_nodes
                SET project_id = ?, node_code = ?, title = ?, episode_num = ?,
                    node_type = ?, speaker_summary = ?, script_text = ?,
                    camera_or_acting_cue = ?, pos_x = ?, pos_y = ?,
                    jinghua_score = ?, user_rating = ?, revision_note = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    project_id,
                    (node_code or "S1").strip(),
                    clean_title,
                    int(episode_num),
                    clean_type,
                    (speaker_summary or "").strip(),
                    new_text,
                    (camera_or_acting_cue or "").strip(),
                    float(pos_x),
                    float(pos_y),
                    jinghua_score,
                    int(user_rating),
                    (revision_note or "").strip(),
                    now,
                    nid,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO story_nodes (
                    id, project_id, node_code, title, episode_num, node_type,
                    speaker_summary, script_text, camera_or_acting_cue,
                    pos_x, pos_y, jinghua_score, user_rating, revision_note,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    nid,
                    project_id,
                    (node_code or "S1").strip(),
                    clean_title,
                    int(episode_num),
                    clean_type,
                    (speaker_summary or "").strip(),
                    new_text,
                    (camera_or_acting_cue or "").strip(),
                    float(pos_x),
                    float(pos_y),
                    jinghua_score,
                    int(user_rating),
                    (revision_note or "").strip(),
                    now,
                    now,
                ),
            )

        conn.execute(
            "UPDATE script_projects SET updated_at = ? WHERE id = ?",
            (now, project_id),
        )
        conn.commit()

        row = conn.execute("SELECT * FROM story_nodes WHERE id = ?", (nid,)).fetchone()
        node = serialize_story_node(row)
        assert node is not None
        node["jinghua_check"] = lock_result
        return node
    finally:
        conn.close()


def delete_story_node(
    node_id: str,
    db_path: Path | str | None = None,
) -> bool:
    """Delete a story node along with its incident choices and revision history."""
    conn, _ = _open_conn(db_path)
    try:
        row = conn.execute("SELECT project_id FROM story_nodes WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            return False
        project_id = row["project_id"]
        conn.execute(
            "DELETE FROM story_choices WHERE from_node_id = ? OR to_node_id = ?",
            (node_id, node_id),
        )
        conn.execute("DELETE FROM script_revisions WHERE node_id = ?", (node_id,))
        cur = conn.execute("DELETE FROM story_nodes WHERE id = ?", (node_id,))
        conn.execute(
            "UPDATE script_projects SET updated_at = ? WHERE id = ?",
            (utc_now_iso(), project_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Choices (支线选项连线)
# ---------------------------------------------------------------------------


def upsert_story_choice(
    project_id: str,
    from_node_id: str,
    to_node_id: str,
    choice_label: str,
    choice_tag: str = "",
    condition_expr: str = "",
    state_delta: dict[str, Any] | str | None = None,
    sort_order: int = 0,
    choice_id: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Create or update a branching choice edge connecting two story nodes."""
    clean_label = (choice_label or "").strip()
    if not clean_label:
        raise ValueError("选项文案 choice_label 不能为空")

    conn, _ = _open_conn(db_path)
    try:
        proj = conn.execute("SELECT id FROM script_projects WHERE id = ?", (project_id,)).fetchone()
        if proj is None:
            raise ValueError(f"找不到剧本工程：{project_id}")

        from_node = conn.execute(
            "SELECT id FROM story_nodes WHERE id = ? AND project_id = ?",
            (from_node_id, project_id),
        ).fetchone()
        to_node = conn.execute(
            "SELECT id FROM story_nodes WHERE id = ? AND project_id = ?",
            (to_node_id, project_id),
        ).fetchone()
        if from_node is None:
            raise ValueError(f"起始节点不存在或不属于当前剧本：{from_node_id}")
        if to_node is None:
            raise ValueError(f"目标节点不存在或不属于当前剧本：{to_node_id}")

        cid = (choice_id or "").strip() or new_id("choice")
        delta_json = dump_json_field(state_delta, default={})
        existing = conn.execute("SELECT id FROM story_choices WHERE id = ?", (cid,)).fetchone()

        if existing is not None:
            conn.execute(
                """
                UPDATE story_choices
                SET project_id = ?, from_node_id = ?, to_node_id = ?,
                    choice_label = ?, choice_tag = ?, condition_expr = ?,
                    state_delta_json = ?, sort_order = ?
                WHERE id = ?
                """,
                (
                    project_id,
                    from_node_id,
                    to_node_id,
                    clean_label,
                    (choice_tag or "").strip(),
                    (condition_expr or "").strip(),
                    delta_json,
                    int(sort_order),
                    cid,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO story_choices (
                    id, project_id, from_node_id, to_node_id,
                    choice_label, choice_tag, condition_expr,
                    state_delta_json, sort_order
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cid,
                    project_id,
                    from_node_id,
                    to_node_id,
                    clean_label,
                    (choice_tag or "").strip(),
                    (condition_expr or "").strip(),
                    delta_json,
                    int(sort_order),
                ),
            )

        conn.execute(
            "UPDATE script_projects SET updated_at = ? WHERE id = ?",
            (utc_now_iso(), project_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM story_choices WHERE id = ?", (cid,)).fetchone()
        result = serialize_story_choice(row)
        assert result is not None
        return result
    finally:
        conn.close()


def delete_story_choice(
    choice_id: str,
    db_path: Path | str | None = None,
) -> bool:
    """Delete a branching choice edge."""
    conn, _ = _open_conn(db_path)
    try:
        row = conn.execute("SELECT project_id FROM story_choices WHERE id = ?", (choice_id,)).fetchone()
        if row is None:
            return False
        project_id = row["project_id"]
        cur = conn.execute("DELETE FROM story_choices WHERE id = ?", (choice_id,))
        conn.execute(
            "UPDATE script_projects SET updated_at = ? WHERE id = ?",
            (utc_now_iso(), project_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Graph & Diagnostics (图诊断与导出)
# ---------------------------------------------------------------------------


def _compute_diagnostics(
    nodes: list[dict[str, Any]],
    choices: list[dict[str, Any]],
) -> dict[str, Any]:
    """Analyze reachability, dead ends, ending distribution, and average Jinghua score."""
    node_map = {n["id"]: n for n in nodes}
    outgoing: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for ch in choices:
        src = ch.get("from_node_id")
        dst = ch.get("to_node_id")
        if src in node_map and dst in node_map:
            outgoing[src].append(dst)

    start_ids = [n["id"] for n in nodes if n.get("node_type") == "start"]
    has_start_node = len(start_ids) > 0

    # BFS from all 'start' nodes (fallback to first node if no start node exists)
    seeds = start_ids if start_ids else ([nodes[0]["id"]] if nodes else [])
    visited: set[str] = set(seeds)
    queue: deque[str] = deque(seeds)
    while queue:
        curr = queue.popleft()
        for nxt in outgoing.get(curr, []):
            if nxt not in visited:
                visited.add(nxt)
                queue.append(nxt)

    unreachable_nodes = [
        {"id": n["id"], "node_code": n["node_code"], "title": n["title"]}
        for n in nodes
        if n["id"] not in visited
    ]

    dead_end_nodes = [
        {"id": n["id"], "node_code": n["node_code"], "title": n["title"]}
        for n in nodes
        if n.get("node_type") not in ENDING_NODE_TYPES and len(outgoing.get(n["id"], [])) == 0
    ]

    good_count = sum(1 for n in nodes if n.get("node_type") == "good_ending")
    bad_count = sum(1 for n in nodes if n.get("node_type") == "bad_ending")
    hidden_count = sum(1 for n in nodes if n.get("node_type") == "hidden_ending")
    total_endings = good_count + bad_count + hidden_count

    avg_score = (
        round(sum(float(n.get("jinghua_score", 100)) for n in nodes) / len(nodes), 1)
        if nodes
        else 100.0
    )

    warnings: list[str] = []
    if nodes and not has_start_node:
        warnings.append("缺少起始幕节点（node_type='start'）")
    if unreachable_nodes:
        codes = "、".join(item["node_code"] for item in unreachable_nodes)
        warnings.append(f"发现 {len(unreachable_nodes)} 个孤立不可达节点：{codes}")
    if dead_end_nodes:
        codes = "、".join(item["node_code"] for item in dead_end_nodes)
        warnings.append(f"发现 {len(dead_end_nodes)} 个断头死胡同节点（非结局且无后续选项）：{codes}")
    if nodes and total_endings == 0:
        warnings.append("当前剧本尚未设置结局节点（good_ending / bad_ending / hidden_ending）")

    is_healthy = (
        len(unreachable_nodes) == 0
        and len(dead_end_nodes) == 0
        and (len(nodes) == 0 or (has_start_node and total_endings > 0))
    )

    return {
        "total_nodes": len(nodes),
        "total_choices": len(choices),
        "has_start_node": has_start_node,
        "unreachable_nodes": unreachable_nodes,
        "unreachable_node_ids": [x["id"] for x in unreachable_nodes],
        "dead_end_nodes": dead_end_nodes,
        "dead_end_node_ids": [x["id"] for x in dead_end_nodes],
        "endings": {
            "good_ending": good_count,
            "bad_ending": bad_count,
            "hidden_ending": hidden_count,
            "total": total_endings,
        },
        "avg_jinghua_score": avg_score,
        "is_healthy": is_healthy,
        "warnings": warnings,
    }


def get_project_graph(
    project_id: str,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Return full project metadata, ordered nodes, choices, and graph diagnostics."""
    conn, _ = _open_conn(db_path)
    try:
        proj_row = conn.execute(
            "SELECT * FROM script_projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if proj_row is None:
            raise ValueError(f"找不到剧本工程：{project_id}")
        project = serialize_script_project(proj_row)
        assert project is not None

        node_rows = conn.execute(
            """
            SELECT * FROM story_nodes
            WHERE project_id = ?
            ORDER BY episode_num ASC, node_code ASC, created_at ASC
            """,
            (project_id,),
        ).fetchall()
        nodes = [n for r in node_rows if (n := serialize_story_node(r)) is not None]

        # Attach recent revisions per node for quick diffing in the editor
        if nodes:
            node_ids = [n["id"] for n in nodes]
            placeholders = ",".join("?" for _ in node_ids)
            rev_rows = conn.execute(
                f"""
                SELECT * FROM script_revisions
                WHERE node_id IN ({placeholders})
                ORDER BY id DESC
                """,
                node_ids,
            ).fetchall()
            revs_by_node: dict[str, list[dict[str, Any]]] = {nid: [] for nid in node_ids}
            for r in rev_rows:
                rev = serialize_script_revision(r)
                if rev is not None and len(revs_by_node[rev["node_id"]]) < 10:
                    revs_by_node[rev["node_id"]].append(rev)
            for node in nodes:
                node["revisions"] = revs_by_node.get(node["id"], [])

        choice_rows = conn.execute(
            """
            SELECT * FROM story_choices
            WHERE project_id = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (project_id,),
        ).fetchall()
        choices = [c for r in choice_rows if (c := serialize_story_choice(r)) is not None]

        diagnostics = _compute_diagnostics(nodes, choices)
        return {
            "project": project,
            "nodes": nodes,
            "choices": choices,
            "diagnostics": diagnostics,
        }
    finally:
        conn.close()


def export_branching_script_markdown(
    project_id: str,
    db_path: Path | str | None = None,
) -> str:
    """Export an interactive branching or short-drama script as Markdown into exports/ and return text."""
    resolved = _normalize_db_path(db_path)
    graph = get_project_graph(project_id, db_path=resolved)
    project = graph["project"]
    nodes: list[dict[str, Any]] = graph["nodes"]
    choices: list[dict[str, Any]] = graph["choices"]
    diag = graph["diagnostics"]

    node_by_id = {n["id"]: n for n in nodes}
    choices_by_from: dict[str, list[dict[str, Any]]] = {}
    for ch in choices:
        choices_by_from.setdefault(ch["from_node_id"], []).append(ch)

    mode_label = "互动影游多支线剧本" if project.get("project_mode") == "branching" else "单线短剧剧本"
    lines: list[str] = [
        f"# 《{project['title']}》",
        "",
        f"- **工程模式**：{mode_label} (`{project.get('project_mode')}`)",
        f"- **类型抽屉**：`{project.get('genre_id')}`",
        f"- **初始状态变量**：`{json.dumps(project.get('state_vars', {}), ensure_ascii=False)}`",
        f"- **节点总数 / 分支选项数**：{diag['total_nodes']} / {diag['total_choices']}",
        f"- **结局分布**：好结局 {diag['endings']['good_ending']} | 坏结局 {diag['endings']['bad_ending']} | 隐藏结局 {diag['endings']['hidden_ending']}",
        f"- **精话锁平均分**：{diag['avg_jinghua_score']} 分",
        "",
    ]

    if project.get("synopsis"):
        lines.extend(["## 故事梗概", "", str(project["synopsis"]), ""])
    if project.get("target_style_notes"):
        lines.extend(["## 文风与台词要求", "", str(project["target_style_notes"]), ""])
    if diag.get("warnings"):
        lines.append("## 图结构诊断提醒")
        lines.append("")
        for warn in diag["warnings"]:
            lines.append(f"- ⚠️ {warn}")
        lines.append("")

    lines.extend(["---", "", "## 分镜与支线台本", ""])

    for node in nodes:
        type_cn = _NODE_TYPE_LABELS.get(node.get("node_type", "scene"), node.get("node_type", "scene"))
        lines.append(
            f"### 第 {node.get('episode_num', 1)} 集 · [{node.get('node_code', '')}] {node['title']}（{type_cn}）"
        )
        lines.append("")
        meta_parts = [f"精话锁评分：**{node.get('jinghua_score', 100)}**"]
        if node.get("speaker_summary"):
            meta_parts.append(f"出场角色：{node['speaker_summary']}")
        if node.get("camera_or_acting_cue"):
            meta_parts.append(f"镜头/表演提示：{node['camera_or_acting_cue']}")
        lines.append(f"> {' | '.join(meta_parts)}")
        lines.append("")

        if node.get("script_text"):
            lines.append(str(node["script_text"]).rstrip())
        else:
            lines.append("（暂无台词正文）")
        lines.append("")

        outgoing = choices_by_from.get(node["id"], [])
        if outgoing:
            lines.append("**玩家分支选项：**")
            for idx, ch in enumerate(outgoing, start=1):
                target = node_by_id.get(ch["to_node_id"])
                target_desc = (
                    f"[{target['node_code']}] {target['title']}" if target else ch["to_node_id"]
                )
                extras: list[str] = []
                if ch.get("choice_tag"):
                    extras.append(f"风格:{ch['choice_tag']}")
                if ch.get("condition_expr"):
                    extras.append(f"前置条件:{ch['condition_expr']}")
                if ch.get("state_delta"):
                    extras.append(
                        f"数值变化:{json.dumps(ch['state_delta'], ensure_ascii=False)}"
                    )
                extra_str = f" （{'；'.join(extras)}）" if extras else ""
                lines.append(f"{idx}. **{ch['choice_label']}** → {target_desc}{extra_str}")
            lines.append("")
        elif node.get("node_type") in ENDING_NODE_TYPES:
            lines.append(f"**[剧终 · {type_cn}]**")
            lines.append("")
        else:
            lines.append("**[⚠️ 警告：当前节点暂无后续分支且未标为结局]**")
            lines.append("")

    markdown = "\n".join(lines)

    exports_dir = resolve_data_subdir("exports", resolved)
    exports_dir.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r'[\\/:*?"<>|\s]+', "_", project["title"]).strip("_") or "script"
    out_file = exports_dir / f"{safe_title}_{project_id}.md"
    out_file.write_text(markdown, encoding="utf-8")

    return markdown
