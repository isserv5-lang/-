"""Novel ingestion (.txt / .docx), chapter segmentation, anti-piracy detection, and span reader."""

from __future__ import annotations

import hashlib
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

import rjieba
from charset_normalizer import from_bytes

from swkb.config import resolve_data_subdir
from swkb.db import get_conn, utc_now_iso

CHAPTER_HEADING_RE = re.compile(
    r"^(?:"
    r"第[零一二三四五六七八九十百千万两0-9]+[章节回卷集部篇](?:[：:\s　].*)?"
    r"|(?:序章|楔子|引子|尾声|番外(?:\s*[0-9零一二三四五六七八九十]+)?)(?:[：:\s　].*)?"
    r"|Chapter\s+\d+(?:[：:\s.\-].*)?"
    r")$",
    re.IGNORECASE,
)

ANTI_PIRACY_RE = re.compile(
    r"正在手打中|手打中[，,]?请稍[等后]|请稍后刷新|最新章节请访问|章节内容正在更新|"
    r"内容正在获取中|防盗章节|防盗版|订阅率?不足|请支持正版|天才一秒记住|"
    r"请记住本站域名|无弹窗小说网|笔趣阁|更多免费小说|本书由.{0,12}整理",
    re.IGNORECASE,
)

TERMINAL_PUNCT = set("。！？….”\"』」）】~～")
DANGLING_TAIL = set("，、：；（【的了和把被将向与在而却")

SPEECH_NAME_RE = re.compile(
    r"(?:^|[，。！？\s])([一-龥]{2,3})(?:说道|问道|笑道|冷笑|哼道|低声道|开口|摇头|点头|挑眉|瞪眼|咬牙|嘟囔)?[：:][“\"「]"
)
NAME_STOPWORDS = {
    "什么", "怎么", "为什么", "不是", "没有", "自己", "我们", "你们", "他们", "她们", "它们",
    "这个", "那个", "这样", "那样", "这里", "那里", "时候", "突然", "但是", "可是", "因为",
    "所以", "如果", "虽然", "还是", "就是", "已经", "一直", "一下", "一点", "起来", "出来",
    "进来", "回去", "过去", "知道", "觉得", "看见", "听见", "说话", "声音", "东西", "地方",
    "看着", "笑着", "说着", "问道", "点头", "摇头", "转身", "低头", "抬头", "今天", "明天",
    "昨天", "现在", "刚才", "马上", "立刻", "忽然", "居然", "竟然", "其实", "本来", "根本",
    "只是", "不过", "而且", "于是", "然后", "接着", "最后", "众人", "大家", "旁边", "门口",
}

FOREIGN_WORLD_CLUSTERS = {
    "修仙玄幻": ("灵气", "筑基", "金丹", "元婴", "宗门", "丹药", "灵石", "渡劫", "魔尊", "剑气", "法宝", "长老", "飞升", "灵脉"),
    "星际机甲": ("机甲", "星际", "虫族", "精神力", "光脑", "星舰", "舰队", "星系", "元帅", "帝国军"),
    "末世丧尸": ("丧尸", "晶核", "异能者", "安全区", "变异兽", "进化者", "末世基地"),
    "军旅抗战": ("师长", "团长", "营长", "鬼子", "炮楼", "手榴弹", "阵地", "司令部"),
    "体育竞技": ("篮板", "三分球", "扣篮", "罚球", "总决赛", "主教练", "加时赛"),
}

QUOTE_CHARS = set("“”\"「」『』")


def _decode_plain_bytes(raw_bytes: bytes) -> str:
    """Decode raw text bytes (UTF-8 / UTF-16 / GB18030) cleanly to str."""
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        return raw_bytes.decode("utf-8-sig", errors="replace")
    if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw_bytes.decode("utf-16", errors="replace")

    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        pass

    best = from_bytes(raw_bytes).best()
    if best is not None:
        enc = (best.encoding or "").lower()
        if enc in ("gb2312", "gbk", "cp936"):
            enc = "gb18030"
        try:
            return raw_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass

    return raw_bytes.decode("gb18030", errors="replace")


def _extract_docx_xml_text(raw_bytes: bytes, path: Path) -> str | None:
    """Extract paragraph text if path is a real ZIP-based .docx; return None otherwise."""
    if not zipfile.is_zipfile(path):
        return None
    try:
        with zipfile.ZipFile(path) as zf:
            if "word/document.xml" not in zf.namelist():
                return None
            xml_bytes = zf.read("word/document.xml")
    except zipfile.BadZipFile:
        return None

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    paragraphs: list[str] = []
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag == "p":
            parts: list[str] = []
            for child in elem.iter():
                ctag = child.tag.rsplit("}", 1)[-1]
                if ctag == "t" and child.text:
                    parts.append(child.text)
                elif ctag == "tab":
                    parts.append("\t")
                elif ctag in ("br", "cr"):
                    parts.append("\n")
            paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def load_novel_text(file_path: Path) -> tuple[str, str]:
    """Read a .txt or .docx (including plain-text disguised .docx) file to normalized UTF-8 text."""
    raw_bytes = file_path.read_bytes()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    text: str | None = None
    if file_path.suffix.lower() == ".docx":
        text = _extract_docx_xml_text(raw_bytes, file_path)

    if text is None:
        text = _decode_plain_bytes(raw_bytes)

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    return "\n".join(lines), sha256


def _is_chapter_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 80:
        return False
    if stripped[0] in QUOTE_CHARS:
        return False
    return bool(CHAPTER_HEADING_RE.match(stripped))


def _split_chapters(full_text: str) -> list[dict]:
    """Split normalized full text into chapter dicts with exact line and char spans."""
    lines = full_text.split("\n")
    line_char_starts: list[int] = []
    offset = 0
    for line in lines:
        line_char_starts.append(offset)
        offset += len(line) + 1  # +1 for '\n'

    heading_indices = [idx for idx, ln in enumerate(lines) if _is_chapter_heading(ln)]

    if not heading_indices:
        # Fallback for short-story format using standalone sequential numbers: 1, 2, 3...
        num_candidates = [
            (idx, int(ln.strip()))
            for idx, ln in enumerate(lines)
            if re.fullmatch(r"\d{1,4}", ln.strip())
        ]
        seq_indices: list[int] = []
        expected = 1
        for idx, val in num_candidates:
            if val == expected:
                seq_indices.append(idx)
                expected += 1
        if len(seq_indices) >= 3:
            heading_indices = seq_indices

    if not heading_indices:
        return [
            {
                "chapter_num": 1,
                "title": "第1章 正文",
                "line_start": 1,
                "line_end": len(lines),
                "char_start": 0,
                "char_end": len(full_text),
                "text": full_text,
            }
        ]

    chapters: list[dict] = []
    first_h = heading_indices[0]
    if first_h > 0:
        pre_lines = lines[:first_h]
        pre_text = "\n".join(pre_lines)
        if len(re.sub(r"\s+", "", pre_text)) >= 80:
            chapters.append(
                {
                    "chapter_num": 0,
                    "title": "前言/简介",
                    "line_start": 1,
                    "line_end": first_h,
                    "char_start": 0,
                    "char_end": line_char_starts[first_h] - 1,
                    "text": pre_text,
                }
            )

    for i, start_idx in enumerate(heading_indices, start=1):
        end_idx = heading_indices[i] - 1 if i < len(heading_indices) else len(lines) - 1
        ch_lines = lines[start_idx : end_idx + 1]
        ch_text = "\n".join(ch_lines)
        c_start = line_char_starts[start_idx]
        c_end = line_char_starts[end_idx] + len(lines[end_idx])
        chapters.append(
            {
                "chapter_num": i,
                "title": lines[start_idx].strip(),
                "line_start": start_idx + 1,
                "line_end": end_idx + 1,
                "char_start": c_start,
                "char_end": c_end,
                "text": ch_text,
            }
        )
    return chapters


def _extract_candidate_names(text: str, min_count: int = 2) -> set[str]:
    """Extract likely character/proper names from Chinese novel text."""
    counts: Counter[str] = Counter()
    for m in SPEECH_NAME_RE.finditer(text):
        name = m.group(1)
        if name not in NAME_STOPWORDS:
            counts[name] += 2

    for tok in rjieba.cut(text):
        tok = tok.strip()
        if 2 <= len(tok) <= 3 and all("\u4e00" <= ch <= "\u9fff" for ch in tok):
            if tok not in NAME_STOPWORDS:
                counts[tok] += 1

    return {name for name, cnt in counts.items() if cnt >= min_count}


def _collect_book_core_names(chapters: list[dict]) -> set[str]:
    """Find recurring protagonist/core names from clean-looking early chapters."""
    doc_freq: Counter[str] = Counter()
    sample = [c for c in chapters if c["chapter_num"] >= 1][:8]
    for ch in sample:
        if ANTI_PIRACY_RE.search(ch["text"]):
            continue
        names = _extract_candidate_names(ch["text"], min_count=2)
        for n in names:
            doc_freq[n] += 1
    if len(sample) >= 2:
        return {n for n, df in doc_freq.items() if df >= 2}
    return {n for n, _ in doc_freq.most_common(6)}


def _detect_corrupted_chapter(
    ch_text: str,
    core_names: set[str],
    book_baseline_clusters: set[str],
) -> tuple[bool, str]:
    """Detect anti-piracy hand-typed notices, truncation, garbling, or swapped second halves."""
    lines = ch_text.split("\n")
    body = "\n".join(lines[1:]) if len(lines) > 1 else ch_text
    compact = re.sub(r"\s+", "", body)

    piracy_match = ANTI_PIRACY_RE.search(body)
    if piracy_match:
        return True, f"含防盗/手打提示词：{piracy_match.group(0)}"

    if len(compact) == 0:
        return True, "章节正文为空"

    bad_chars = body.count("\ufffd")
    if bad_chars >= 2 or (bad_chars / max(1, len(body))) > 0.005:
        return True, "含大面积乱码替换符"

    # Abrupt half-sentence truncation check
    non_empty_lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
    last_line = non_empty_lines[-1] if non_empty_lines else ""
    if len(compact) < 120:
        if last_line and last_line[-1] not in TERMINAL_PUNCT:
            return True, "超短残章且结尾半句异常截断"
        if re.search(r"待更新|待补|占位|请假条", body):
            return True, "章节疑似占位/未完整更新"

    if last_line and len(compact) < 600 and last_line[-1] in DANGLING_TAIL:
        return True, f"结尾半句异常截断（停在“{last_line[-1]}”）"

    # Character discontinuity + foreign world cluster in second half / tail
    if len(compact) >= 180:
        # Check both midpoint split and 45%/60% head-vs-tail split to handle uneven paste points
        split_pairs = [
            (body[: len(body) // 2], body[len(body) // 2 :]),
            (body[: int(len(body) * 0.45)], body[int(len(body) * 0.60) :]),
        ]
        for first_half, second_half in split_pairs:
            if len(re.sub(r"\s+", "", second_half)) < 80:
                continue
            first_names = _extract_candidate_names(first_half, min_count=2)
            anchor_names = first_names | {n for n in core_names if n in first_half}
            second_all_names = _extract_candidate_names(second_half, min_count=1)
            second_recurring = _extract_candidate_names(second_half, min_count=2)

            overlap = anchor_names & second_all_names

            if anchor_names and not overlap:
                # Check foreign genre/other-world cluster injection in second half
                for cluster_name, words in FOREIGN_WORLD_CLUSTERS.items():
                    if cluster_name in book_baseline_clusters:
                        continue
                    first_distinct = sum(1 for w in words if w in first_half)
                    second_distinct = sum(1 for w in words if w in second_half)
                    second_hits = sum(second_half.count(w) for w in words)
                    if first_distinct == 0 and (second_distinct >= 3 or second_hits >= 5):
                        return (
                            True,
                            f"后半章主角/人名与前文完全断裂，且混入高频【{cluster_name}】异世界词",
                        )

                # Pure character replacement even without known cluster keywords
                new_names = second_recurring - anchor_names
                if len(anchor_names) >= 2 and len(new_names) >= 2 and len(re.sub(r"\s+", "", second_half)) >= 250:
                    return True, "后半章人物与前文主角完全断裂并替换为全新陌生人名"

    return False, ""


def _tokenize_chinese(text: str) -> str:
    tokens = [tok.strip() for tok in rjieba.cut(text) if tok.strip()]
    return " ".join(tokens)


def _extract_candidate_snippets(chapters: list[dict], target_count: int = 12) -> list[dict]:
    """Extract 10~15 high-contrast dialogue snippets strictly from clean chapters."""
    candidates: list[dict] = []

    for ch in chapters:
        if ch["is_corrupted"] or ch["chapter_num"] < 1:
            continue
        ch_lines = ch["text"].split("\n")
        n = len(ch_lines)
        i = 1  # skip heading line 0 (1-based line 1)
        while i < n:
            line = ch_lines[i].strip()
            if not any(q in line for q in ("“", "”", "「", "」", '"')):
                i += 1
                continue

            # Expand a compact 1..4 line exchange block containing dialogue
            end_i = i
            while end_i + 1 < n and (end_i - i + 1) < 4:
                nxt = ch_lines[end_i + 1].strip()
                if not nxt:
                    break
                if any(q in nxt for q in ("“", "”", "「", "」", '"')) or len(nxt) <= 36:
                    end_i += 1
                else:
                    break

            block_lines = [ch_lines[k].strip() for k in range(i, end_i + 1) if ch_lines[k].strip()]
            raw_text = "\n".join(block_lines)
            char_len = len(raw_text)

            if 22 <= char_len <= 320:
                q_marks = raw_text.count("？") + raw_text.count("?")
                e_marks = raw_text.count("！") + raw_text.count("!")
                ellipses = raw_text.count("……") + raw_text.count("...")
                quote_turns = max(
                    raw_text.count("“"),
                    raw_text.count("「"),
                    raw_text.count('"') // 2,
                )
                quoted_chars = sum(
                    len(m.group(0))
                    for m in re.finditer(r"“[^”]*”|「[^」]*」|\"[^\"]*\"", raw_text)
                )
                quote_ratio = quoted_chars / max(1, char_len)

                score = 0.0
                score += min(3, q_marks) * 3.0
                score += min(3, e_marks) * 2.5
                score += min(2, ellipses) * 3.0
                if quote_turns >= 2:
                    score += 4.5
                if len(block_lines) >= 2 and all(len(bl) <= 60 for bl in block_lines):
                    score += 2.5
                if quote_ratio < 0.25:
                    score -= 4.0

                if score >= 4.0:
                    tags: list[str] = []
                    if q_marks > 0 and e_marks > 0:
                        tags.append("问叹反差")
                    elif quote_turns >= 2:
                        tags.append("短句交锋")
                    elif ellipses > 0:
                        tags.append("停顿反转")
                    else:
                        tags.append("互动对白")

                    prev_ctx = ""
                    if i - 1 >= 1 and ch_lines[i - 1].strip():
                        prev_ctx = ch_lines[i - 1].strip()[:100]

                    candidates.append(
                        {
                            "chapter_num": ch["chapter_num"],
                            "line_start": i + 1,  # 1-based within chapter
                            "line_end": end_i + 1,
                            "raw_text": raw_text,
                            "scene_tag": "/".join(tags),
                            "trigger_context": prev_ctx,
                            "why_funny_or_effective": "待试菜：自动抽取的高反差/互动交锋原话",
                            "score": score,
                        }
                    )
            i = end_i + 1

    if not candidates:
        return []

    # Sort descending by score while preserving chapter diversity
    candidates.sort(key=lambda c: c["score"], reverse=True)
    selected: list[dict] = []
    per_ch: Counter[int] = Counter()
    max_per_ch = 3 if len({c["chapter_num"] for c in candidates}) >= 4 else 6

    for cand in candidates:
        if len(selected) >= min(15, max(target_count, 10)):
            break
        if per_ch[cand["chapter_num"]] >= max_per_ch:
            continue
        selected.append(cand)
        per_ch[cand["chapter_num"]] += 1

    # Top up to at least 10 if relaxed per-chapter cap allows
    if len(selected) < 10:
        chosen_keys = {(s["chapter_num"], s["line_start"]) for s in selected}
        for cand in candidates:
            if len(selected) >= 12:
                break
            key = (cand["chapter_num"], cand["line_start"])
            if key not in chosen_keys:
                selected.append(cand)
                chosen_keys.add(key)

    return selected


def import_novel_file(
    file_path: str | Path,
    genre_id: str = "mengwa",
    title: str | None = None,
    author: str = "",
    db_path: Path | None = None,
) -> dict:
    """Import a .txt or .docx novel, detect corrupted anti-piracy chapters, and extract pending dialogues."""
    src_path = Path(file_path).expanduser()
    if not src_path.exists() or not src_path.is_file():
        raise FileNotFoundError(f"Novel file not found: {src_path}")

    full_text, sha256 = load_novel_text(src_path)
    book_title = (title or "").strip() or src_path.stem

    conn = get_conn(db_path)
    try:
        # Idempotent check on exact file hash + genre
        existing = conn.execute(
            "SELECT * FROM works WHERE file_sha256 = ? AND genre_id = ?",
            (sha256, genre_id),
        ).fetchone()
        if existing is not None and existing["norm_path"] and Path(existing["norm_path"]).exists():
            corrupted_rows = conn.execute(
                "SELECT chapter_num, title, corruption_reason FROM chapters WHERE work_id = ? AND is_corrupted = 1 ORDER BY chapter_num",
                (existing["id"],),
            ).fetchall()
            pending_count = conn.execute(
                "SELECT COUNT(*) FROM dialogue_snippets WHERE work_id = ? AND status = 'pending'",
                (existing["id"],),
            ).fetchone()[0]
            return {
                "work_id": existing["id"],
                "title": existing["title"],
                "author": existing["author"],
                "genre_id": existing["genre_id"],
                "norm_path": existing["norm_path"],
                "file_sha256": existing["file_sha256"],
                "total_chars": existing["total_chars"],
                "total_chapters": existing["total_chapters"],
                "clean_chapters": existing["clean_chapters"],
                "corrupted_chapters": existing["corrupted_chapters"],
                "corrupted_chapter_list": [dict(r) for r in corrupted_rows],
                "pending_snippets_count": pending_count,
                "already_exists": True,
            }

        work_id = f"wk_{uuid.uuid4().hex[:10]}"
        sources_dir = resolve_data_subdir("sources", db_path)
        norm_path = sources_dir / f"{work_id}.txt"
        norm_path.write_text(full_text, encoding="utf-8")

        raw_chapters = _split_chapters(full_text)
        core_names = _collect_book_core_names(raw_chapters)

        # Determine if the book itself legitimately belongs to a cluster domain from Ch1 / first halves
        early_first_halves = "\n".join(
            c["text"][: max(1, len(c["text"]) // 2)] for c in raw_chapters[:3]
        )
        baseline_clusters: set[str] = set()
        for cname, words in FOREIGN_WORLD_CLUSTERS.items():
            if sum(1 for w in words if w in early_first_halves) >= 2:
                baseline_clusters.add(cname)

        clean_count = 0
        corrupted_count = 0
        corrupted_list: list[dict] = []

        for ch in raw_chapters:
            is_bad, reason = _detect_corrupted_chapter(
                ch["text"], core_names, baseline_clusters
            )
            ch["is_corrupted"] = 1 if is_bad else 0
            ch["corruption_reason"] = reason
            ch["char_count"] = len(re.sub(r"\s+", "", ch["text"]))
            if is_bad:
                corrupted_count += 1
                corrupted_list.append(
                    {
                        "chapter_num": ch["chapter_num"],
                        "title": ch["title"],
                        "corruption_reason": reason,
                    }
                )
            else:
                clean_count += 1

        now = utc_now_iso()
        total_chars = len(re.sub(r"\s+", "", full_text))
        conn.execute(
            """
            INSERT INTO works
            (id, title, author, genre_id, source_path, norm_path, file_sha256,
             total_chars, total_chapters, clean_chapters, corrupted_chapters,
             completion_status, summary_one_page, style_notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'imported', '', '', ?)
            """,
            (
                work_id,
                book_title,
                author.strip(),
                genre_id,
                str(src_path),
                str(norm_path),
                sha256,
                total_chars,
                len(raw_chapters),
                clean_count,
                corrupted_count,
                now,
            ),
        )

        for ch in raw_chapters:
            conn.execute(
                """
                INSERT INTO chapters
                (work_id, chapter_num, title, line_start, line_end, char_start, char_end,
                 char_count, is_corrupted, corruption_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    work_id,
                    ch["chapter_num"],
                    ch["title"],
                    ch["line_start"],
                    ch["line_end"],
                    ch["char_start"],
                    ch["char_end"],
                    ch["char_count"],
                    ch["is_corrupted"],
                    ch["corruption_reason"],
                ),
            )

        snippets = _extract_candidate_snippets(raw_chapters, target_count=12)
        saved_snippets: list[dict] = []
        for sn in snippets:
            sid = f"sn_{uuid.uuid4().hex[:10]}"
            seg_tokens = _tokenize_chinese(sn["raw_text"])
            conn.execute(
                """
                INSERT INTO dialogue_snippets
                (id, work_id, genre_id, chapter_num, line_start, line_end, scene_tag,
                 raw_text, why_funny_or_effective, trigger_context, status, rating, seg_tokens, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)
                """,
                (
                    sid,
                    work_id,
                    genre_id,
                    sn["chapter_num"],
                    sn["line_start"],
                    sn["line_end"],
                    sn["scene_tag"],
                    sn["raw_text"],
                    sn["why_funny_or_effective"],
                    sn["trigger_context"],
                    seg_tokens,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO dialogue_fts (snippet_id, scene_tag, raw_text, seg_tokens)
                VALUES (?, ?, ?, ?)
                """,
                (sid, sn["scene_tag"], sn["raw_text"], seg_tokens),
            )
            saved_snippets.append(
                {
                    "id": sid,
                    "chapter_num": sn["chapter_num"],
                    "line_start": sn["line_start"],
                    "line_end": sn["line_end"],
                    "scene_tag": sn["scene_tag"],
                    "raw_text": sn["raw_text"],
                }
            )

        conn.commit()
        return {
            "work_id": work_id,
            "title": book_title,
            "author": author.strip(),
            "genre_id": genre_id,
            "norm_path": str(norm_path),
            "file_sha256": sha256,
            "total_chars": total_chars,
            "total_chapters": len(raw_chapters),
            "clean_chapters": clean_count,
            "corrupted_chapters": corrupted_count,
            "corrupted_chapter_list": corrupted_list,
            "pending_snippets_count": len(saved_snippets),
            "sample_pending_snippets": saved_snippets,
            "already_exists": False,
        }
    finally:
        conn.close()


def read_novel_span(
    work_id: str,
    chapter_num: int,
    line_start: int = 1,
    line_end: int = 80,
    db_path: Path | None = None,
) -> dict:
    """Read exact chapter lines (1-based within the chapter) from a normalized work file."""
    conn = get_conn(db_path)
    try:
        work = conn.execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone()
        if work is None:
            raise ValueError(f"Work not found: {work_id}")

        chapter = conn.execute(
            "SELECT * FROM chapters WHERE work_id = ? AND chapter_num = ?",
            (work_id, chapter_num),
        ).fetchone()
        if chapter is None:
            raise ValueError(f"Chapter {chapter_num} not found in work {work_id}")

        norm_path = Path(work["norm_path"])
        if not norm_path.exists():
            raise FileNotFoundError(f"Normalized source missing: {norm_path}")

        all_lines = norm_path.read_text(encoding="utf-8").split("\n")
        g_start = max(1, int(chapter["line_start"]))
        g_end = min(len(all_lines), int(chapter["line_end"]))
        ch_lines = all_lines[g_start - 1 : g_end]
        total_ch_lines = len(ch_lines)

        req_start = max(1, int(line_start))
        req_end = min(total_ch_lines, max(req_start, int(line_end)))
        sliced = ch_lines[req_start - 1 : req_end]

        structured_lines = [
            {"line_num": req_start + idx, "text": txt}
            for idx, txt in enumerate(sliced)
        ]

        return {
            "work_id": work_id,
            "work_title": work["title"],
            "chapter_num": chapter_num,
            "chapter_title": chapter["title"],
            "is_corrupted": bool(chapter["is_corrupted"]),
            "corruption_reason": chapter["corruption_reason"],
            "line_start": req_start,
            "line_end": req_end,
            "total_chapter_lines": total_ch_lines,
            "lines": structured_lines,
            "text": "\n".join(sliced),
        }
    finally:
        conn.close()
