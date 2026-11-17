"""Jinghua Lock (精话锁): detect and strip greasy AI narration while preserving dialogue flavor."""

from __future__ import annotations

import re
import sqlite3
import uuid
from pathlib import Path

from swkb.db import get_conn, utc_now_iso

QUOTE_PAIRS = {
    "“": "”",
    "「": "」",
    "『": "』",
    '"': '"',
}

SIMILE_RE = re.compile(r"仿佛|宛如|犹如|好似|活像")
MULTI_EXCLAM_RE = re.compile(r"[！!]{2,}")


def _split_line_segments(line: str) -> tuple[list[str], list[str]]:
    """Split a single line into (narration_segments, dialogue_segments)."""
    narration: list[str] = []
    dialogue: list[str] = []
    buf: list[str] = []
    in_quote_close: str | None = None

    for ch in line:
        if in_quote_close is None:
            if ch in QUOTE_PAIRS:
                if buf:
                    narration.append("".join(buf))
                    buf.clear()
                in_quote_close = QUOTE_PAIRS[ch]
            else:
                buf.append(ch)
        else:
            if ch == in_quote_close:
                if buf:
                    dialogue.append("".join(buf))
                    buf.clear()
                in_quote_close = None
            else:
                buf.append(ch)

    if buf:
        if in_quote_close is None:
            narration.append("".join(buf))
        else:
            dialogue.append("".join(buf))

    return narration, dialogue


def _row_to_rule(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "category": row["category"],
        "bad_pattern": row["bad_pattern"],
        "is_regex": bool(row["is_regex"]),
        "reason": row["reason"],
        "good_replacement_example": row["good_replacement_example"],
        "enabled": bool(row["enabled"]),
        "hit_count": row["hit_count"],
        "created_at": row["created_at"],
    }


def list_jinghua_rules(
    include_disabled: bool = True,
    db_path: Path | None = None,
) -> list[dict]:
    """Return Jinghua Lock rules ordered by enabled state, category, and id."""
    conn = get_conn(db_path)
    try:
        sql = "SELECT * FROM jinghua_lock_rules"
        if not include_disabled:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY enabled DESC, category ASC, id ASC"
        rows = conn.execute(sql).fetchall()
        return [_row_to_rule(r) for r in rows]
    finally:
        conn.close()


def add_jinghua_rule(
    bad_pattern: str,
    category: str = "AI油腻旁白",
    reason: str = "",
    good_replacement_example: str = "",
    is_regex: bool = False,
    db_path: Path | None = None,
) -> dict:
    """Add a new Jinghua Lock blacklist rule (or re-enable/update if pattern already exists)."""
    pattern = bad_pattern.strip()
    if not pattern:
        raise ValueError("bad_pattern cannot be empty")
    if is_regex:
        re.compile(pattern)

    conn = get_conn(db_path)
    try:
        existing = conn.execute(
            "SELECT * FROM jinghua_lock_rules WHERE bad_pattern = ? AND is_regex = ?",
            (pattern, 1 if is_regex else 0),
        ).fetchone()
        if existing is not None:
            conn.execute(
                """
                UPDATE jinghua_lock_rules
                SET enabled = 1,
                    category = ?,
                    reason = CASE WHEN ? != '' THEN ? ELSE reason END,
                    good_replacement_example = CASE WHEN ? != '' THEN ? ELSE good_replacement_example END
                WHERE id = ?
                """,
                (
                    category.strip() or existing["category"],
                    reason.strip(),
                    reason.strip(),
                    good_replacement_example.strip(),
                    good_replacement_example.strip(),
                    existing["id"],
                ),
            )
            conn.commit()
            updated = conn.execute(
                "SELECT * FROM jinghua_lock_rules WHERE id = ?", (existing["id"],)
            ).fetchone()
            return _row_to_rule(updated)

        rule_id = f"jh_{uuid.uuid4().hex[:8]}"
        now = utc_now_iso()
        conn.execute(
            """
            INSERT INTO jinghua_lock_rules
            (id, category, bad_pattern, is_regex, reason, good_replacement_example, enabled, hit_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?)
            """,
            (
                rule_id,
                category.strip() or "AI油腻旁白",
                pattern,
                1 if is_regex else 0,
                reason.strip() or "用户自定义拉黑的 AI 油腻/出戏表达",
                good_replacement_example.strip() or "改用平实具体的动作或神态描写替代",
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM jinghua_lock_rules WHERE id = ?", (rule_id,)
        ).fetchone()
        return _row_to_rule(row)
    finally:
        conn.close()


def toggle_jinghua_rule(
    rule_id: str,
    enabled: bool,
    db_path: Path | None = None,
) -> bool:
    """Enable or disable a Jinghua Lock rule by ID. Returns True if found."""
    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            "UPDATE jinghua_lock_rules SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, rule_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_jinghua_rule(
    rule_id: str,
    db_path: Path | None = None,
) -> bool:
    """Delete a Jinghua Lock rule by ID. Returns True if deleted."""
    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM jinghua_lock_rules WHERE id = ?", (rule_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _build_rewrite_guide(hits: list[dict], score: int) -> str:
    if not hits:
        return (
            f"通过精话锁检测（得分 {score}/100）：引号外旁白平实克制，未命中禁用油腻词，"
            "对话风格已隔离保护。"
        )

    lines = [
        f"未通过精话锁检测（得分 {score}/100，共命中 {len(hits)} 处旁白问题）。",
        "修改铁律：梗、反讽和狠话只允许留在引号内的台词里；引号外旁白老老实实写动作、神态与事实，严禁抖机灵、小品式夸张和 AI 腔比喻。",
        "请按以下逐条清单直接改写：",
    ]
    for idx, hit in enumerate(hits, start=1):
        lines.append(
            f"{idx}. 第 {hit['line_num']} 行命中【{hit['matched_text']}】（{hit['category']}）"
            f"—— 原因：{hit['reason']}；参考改法：{hit['good_replacement_example']}"
        )
    return "\n".join(lines)


def check_text_with_jinghua_lock(
    text: str,
    db_path: Path | None = None,
) -> dict:
    """Inspect narration outside quotes against enabled Jinghua Lock rules and style checks."""
    conn = get_conn(db_path)
    try:
        rule_rows = conn.execute(
            "SELECT * FROM jinghua_lock_rules WHERE enabled = 1 ORDER BY id ASC"
        ).fetchall()
        rules = [_row_to_rule(r) for r in rule_rows]

        compiled_rules: list[tuple[dict, re.Pattern[str] | None]] = []
        for rule in rules:
            if rule["is_regex"]:
                try:
                    compiled_rules.append((rule, re.compile(rule["bad_pattern"])))
                except re.error:
                    compiled_rules.append((rule, None))
            else:
                compiled_rules.append((rule, None))

        hits: list[dict] = []
        triggered_rule_ids: list[str] = []
        narration_chars = 0
        dialogue_chars = 0
        style_warning_count = 0

        for line_num, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            narr_segs, dial_segs = _split_line_segments(line)
            narration_chars += sum(len(s.strip()) for s in narr_segs)
            dialogue_chars += sum(len(s.strip()) for s in dial_segs)

            for seg in narr_segs:
                if not seg.strip():
                    continue
                for rule, regex in compiled_rules:
                    if rule["is_regex"]:
                        if regex is None:
                            continue
                        for m in regex.finditer(seg):
                            hits.append(
                                {
                                    "rule_id": rule["id"],
                                    "category": rule["category"],
                                    "bad_pattern": rule["bad_pattern"],
                                    "matched_text": m.group(0),
                                    "line_num": line_num,
                                    "line_text": line.strip(),
                                    "reason": rule["reason"],
                                    "good_replacement_example": rule["good_replacement_example"],
                                    "in_dialogue": False,
                                }
                            )
                            triggered_rule_ids.append(rule["id"])
                    else:
                        pat = rule["bad_pattern"]
                        start = 0
                        while True:
                            pos = seg.find(pat, start)
                            if pos == -1:
                                break
                            hits.append(
                                {
                                    "rule_id": rule["id"],
                                    "category": rule["category"],
                                    "bad_pattern": pat,
                                    "matched_text": pat,
                                    "line_num": line_num,
                                    "line_text": line.strip(),
                                    "reason": rule["reason"],
                                    "good_replacement_example": rule["good_replacement_example"],
                                    "in_dialogue": False,
                                }
                            )
                            triggered_rule_ids.append(rule["id"])
                            start = pos + len(pat)

                # Narration rhetorical/exclamation bloat check (outside quotes only)
                exclam_total = seg.count("！") + seg.count("!")
                if MULTI_EXCLAM_RE.search(seg) or exclam_total >= 2:
                    style_warning_count += 1
                    hits.append(
                        {
                            "rule_id": "style_exclam",
                            "category": "夸张感叹/修辞堆砌",
                            "bad_pattern": "旁白连续感叹号",
                            "matched_text": seg.strip()[:40],
                            "line_num": line_num,
                            "line_text": line.strip(),
                            "reason": "引号外旁白出现多个感叹号，像说书腔或情绪硬塞",
                            "good_replacement_example": "改用句号平静交代动作，把情绪留给人物台词",
                            "in_dialogue": False,
                        }
                    )

                similes = SIMILE_RE.findall(seg)
                if len(similes) >= 2:
                    style_warning_count += 1
                    hits.append(
                        {
                            "rule_id": "style_simile",
                            "category": "夸张感叹/修辞堆砌",
                            "bad_pattern": "旁白比喻词堆砌",
                            "matched_text": " / ".join(similes),
                            "line_num": line_num,
                            "line_text": line.strip(),
                            "reason": "同一句旁白堆砌多个“仿佛/宛如/犹如”，画面虚浮油腻",
                            "good_replacement_example": "删掉比喻，直接写具体发生了什么动作或声音",
                            "in_dialogue": False,
                        }
                    )

        if triggered_rule_ids:
            for rid in triggered_rule_ids:
                conn.execute(
                    "UPDATE jinghua_lock_rules SET hit_count = hit_count + 1 WHERE id = ?",
                    (rid,),
                )
            conn.commit()

        rule_hit_count = len(triggered_rule_ids)
        penalty = rule_hit_count * 15 + style_warning_count * 8
        score = max(0, min(100, 100 - penalty))
        passed = rule_hit_count == 0 and score >= 80
        rewrite_guide = _build_rewrite_guide(hits, score)

        return {
            "passed": passed,
            "score": score,
            "hits": hits,
            "rewrite_guide": rewrite_guide,
            "checked_rules_count": len(rules),
            "narration_chars": narration_chars,
            "dialogue_chars": dialogue_chars,
        }
    finally:
        conn.close()
