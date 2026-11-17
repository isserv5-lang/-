"""Local zero-token originality checker using 8-gram overlap and rapidfuzz partial similarity."""

from __future__ import annotations

import re
from pathlib import Path

from rapidfuzz import fuzz

from swkb.db import get_conn

NORM_CHAR_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]+")
SPLIT_SENTENCE_RE = re.compile(r"[。！？!?\n]+")
NGRAM_SIZE = 8


def _norm_text(text: str) -> str:
    return "".join(NORM_CHAR_RE.findall(text)).lower()


def _extract_ngrams(norm_text: str, n: int = NGRAM_SIZE) -> set[str]:
    if len(norm_text) < n:
        return set()
    return {norm_text[i : i + n] for i in range(len(norm_text) - n + 1)}


def _draft_segments(draft_text: str) -> list[str]:
    segs: list[str] = []
    for raw in SPLIT_SENTENCE_RE.split(draft_text):
        n = _norm_text(raw)
        if len(n) >= 14:
            segs.append(n)
    if not segs:
        whole = _norm_text(draft_text)
        if len(whole) >= 14:
            segs.append(whole)
    return segs


def _compute_fuzzy_max(draft_segs: list[str], source_norm: str) -> float:
    if not draft_segs or len(source_norm) < 14:
        return 0.0
    best = 0.0
    for seg in draft_segs:
        # Compare reasonably sized windows; rapidfuzz partial_ratio returns 0..100
        score = fuzz.partial_ratio(seg[:200], source_norm) / 100.0
        if score > best:
            best = score
            if best >= 0.99:
                break
    return round(best, 4)


def check_script_originality(
    draft_text: str,
    genre_id: str | None = None,
    threshold: float = 0.35,
    db_path: Path | None = None,
) -> dict:
    """Check draft text locally against clean imported works and stored dialogue snippets."""
    norm_draft = _norm_text(draft_text)
    if len(norm_draft) < NGRAM_SIZE:
        return {
            "passed": True,
            "threshold": threshold,
            "ngram_overlap_rate": 0.0,
            "max_similarity": 0.0,
            "matches": [],
            "summary": "文本过短（不足 8 个有效字），跳过本地查重。",
        }

    draft_ngrams = _extract_ngrams(norm_draft, NGRAM_SIZE)
    draft_segs = _draft_segments(draft_text)
    union_matched_ngrams: set[str] = set()
    matches: list[dict] = []
    max_fuzzy = 0.0

    conn = get_conn(db_path)
    try:
        # 1. Compare against clean chapters of stored works
        work_sql = "SELECT id, title, norm_path, genre_id FROM works"
        work_params: list = []
        if genre_id:
            work_sql += " WHERE genre_id = ?"
            work_params.append(genre_id)
        works = conn.execute(work_sql, work_params).fetchall()

        for work in works:
            norm_path_str = work["norm_path"]
            if not norm_path_str:
                continue
            p = Path(norm_path_str)
            if not p.exists():
                continue
            full_text = p.read_text(encoding="utf-8")
            chapters = conn.execute(
                """
                SELECT chapter_num, title, char_start, char_end
                FROM chapters
                WHERE work_id = ? AND is_corrupted = 0
                ORDER BY chapter_num ASC
                """,
                (work["id"],),
            ).fetchall()

            for ch in chapters:
                c_start = max(0, int(ch["char_start"] or 0))
                c_end = min(len(full_text), int(ch["char_end"] or len(full_text)))
                ch_raw = full_text[c_start:c_end]
                ch_norm = _norm_text(ch_raw)
                if len(ch_norm) < NGRAM_SIZE:
                    continue

                ch_ngrams = _extract_ngrams(ch_norm, NGRAM_SIZE)
                overlap_grams = draft_ngrams & ch_ngrams
                ngram_rate = round(len(overlap_grams) / len(draft_ngrams), 4)
                union_matched_ngrams.update(overlap_grams)

                fuzzy_sim = 0.0
                if overlap_grams or len(draft_segs) <= 25:
                    fuzzy_sim = _compute_fuzzy_max(draft_segs, ch_norm)
                max_fuzzy = max(max_fuzzy, fuzzy_sim)

                if ngram_rate >= 0.05 or fuzzy_sim >= max(0.55, threshold):
                    sample_grams = sorted(overlap_grams)[:5]
                    excerpt = ""
                    if sample_grams:
                        idx = ch_raw.find(sample_grams[0][:4])
                        if idx != -1:
                            excerpt = ch_raw[max(0, idx - 30) : idx + 110].strip()
                    if not excerpt:
                        excerpt = ch_raw[:140].strip()

                    matches.append(
                        {
                            "source_type": "chapter",
                            "work_id": work["id"],
                            "work_title": work["title"],
                            "chapter_num": ch["chapter_num"],
                            "chapter_title": ch["title"] or f"第{ch['chapter_num']}章",
                            "snippet_id": None,
                            "ngram_overlap": ngram_rate,
                            "fuzzy_similarity": fuzzy_sim,
                            "matched_ngrams_sample": sample_grams,
                            "source_excerpt": excerpt,
                        }
                    )

        # 2. Compare against stored dialogue snippets (pending + approved)
        sn_sql = """
            SELECT d.id, d.work_id, d.chapter_num, d.raw_text, w.title AS work_title
            FROM dialogue_snippets d
            LEFT JOIN works w ON d.work_id = w.id
            WHERE d.status != 'rejected'
        """
        sn_params: list = []
        if genre_id:
            sn_sql += " AND d.genre_id = ?"
            sn_params.append(genre_id)
        snippets = conn.execute(sn_sql, sn_params).fetchall()

        for sn in snippets:
            sn_raw = sn["raw_text"] or ""
            sn_norm = _norm_text(sn_raw)
            if len(sn_norm) < NGRAM_SIZE:
                continue
            sn_ngrams = _extract_ngrams(sn_norm, NGRAM_SIZE)
            overlap_grams = draft_ngrams & sn_ngrams
            ngram_rate = round(len(overlap_grams) / len(draft_ngrams), 4)
            union_matched_ngrams.update(overlap_grams)

            fuzzy_sim = _compute_fuzzy_max(draft_segs, sn_norm) if (overlap_grams or len(sn_norm) >= 14) else 0.0
            max_fuzzy = max(max_fuzzy, fuzzy_sim)

            if ngram_rate >= 0.08 or fuzzy_sim >= max(0.60, threshold):
                matches.append(
                    {
                        "source_type": "dialogue_snippet",
                        "work_id": sn["work_id"],
                        "work_title": sn["work_title"] or "知识库对白卡",
                        "chapter_num": sn["chapter_num"],
                        "chapter_title": f"第{sn['chapter_num']}章" if sn["chapter_num"] else "",
                        "snippet_id": sn["id"],
                        "ngram_overlap": ngram_rate,
                        "fuzzy_similarity": fuzzy_sim,
                        "matched_ngrams_sample": sorted(overlap_grams)[:5],
                        "source_excerpt": sn_raw[:160].strip(),
                    }
                )
    finally:
        conn.close()

    total_ngram_overlap = round(len(union_matched_ngrams) / len(draft_ngrams), 4)
    best_per_match = max(
        (max(m["ngram_overlap"], m["fuzzy_similarity"]) for m in matches),
        default=0.0,
    )
    max_similarity = round(max(total_ngram_overlap, best_per_match, max_fuzzy), 4)
    matches.sort(
        key=lambda m: (m["ngram_overlap"], m["fuzzy_similarity"]),
        reverse=True,
    )
    top_matches = matches[:10]
    passed = max_similarity < threshold

    if passed:
        summary = (
            f"通过本地原创度查重（最高相似度 {max_similarity:.1%}，8-gram 重字率 {total_ngram_overlap:.1%}，"
            f"阈值 {threshold:.0%}）：未发现照搬原著段落。"
        )
    else:
        first = top_matches[0]
        loc = f"《{first['work_title']}》"
        if first["chapter_num"] is not None:
            loc += f" 第{first['chapter_num']}章"
        summary = (
            f"查重预警：最高相似度 {max_similarity:.1%}（超过阈值 {threshold:.0%}），"
            f"8-gram 连续重字率 {total_ngram_overlap:.1%}，最接近出处：{loc}，请更换句式与具体桥段！"
        )

    return {
        "passed": passed,
        "threshold": threshold,
        "ngram_overlap_rate": total_ngram_overlap,
        "max_similarity": max_similarity,
        "matches": top_matches,
        "summary": summary,
    }
