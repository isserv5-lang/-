"""SQLite schema, connection management, and idempotent initial seed data."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from swkb.config import get_db_path
from swkb.taxonomy import LEGACY_GENRE_MIGRATION, OFFICIAL_TAGS


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format without microseconds."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS kb_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS genres (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT DEFAULT '',
    channel_hint TEXT DEFAULT '',
    channel TEXT DEFAULT '通用',
    sub_group TEXT DEFAULT '题材主干',
    platforms TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS works (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT DEFAULT '',
    genre_id TEXT,
    source_path TEXT,
    norm_path TEXT,
    file_sha256 TEXT,
    total_chars INTEGER DEFAULT 0,
    total_chapters INTEGER DEFAULT 0,
    clean_chapters INTEGER DEFAULT 0,
    corrupted_chapters INTEGER DEFAULT 0,
    completion_status TEXT DEFAULT 'unknown',
    summary_one_page TEXT DEFAULT '',
    style_notes TEXT DEFAULT '',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    chapter_num INTEGER NOT NULL,
    title TEXT,
    line_start INTEGER,
    line_end INTEGER,
    char_start INTEGER,
    char_end INTEGER,
    char_count INTEGER,
    is_corrupted INTEGER DEFAULT 0,
    corruption_reason TEXT DEFAULT '',
    UNIQUE(work_id, chapter_num)
);

CREATE TABLE IF NOT EXISTS characters (
    id TEXT PRIMARY KEY,
    work_id TEXT,
    genre_id TEXT,
    name TEXT NOT NULL,
    role_type TEXT DEFAULT '',
    traits TEXT NOT NULL,
    speech_style TEXT DEFAULT '',
    catchphrases TEXT DEFAULT '',
    forbidden_words TEXT DEFAULT '',
    sample_lines_json TEXT DEFAULT '[]',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS dialogue_snippets (
    id TEXT PRIMARY KEY,
    work_id TEXT,
    genre_id TEXT NOT NULL,
    chapter_num INTEGER,
    line_start INTEGER,
    line_end INTEGER,
    scene_tag TEXT DEFAULT '',
    raw_text TEXT NOT NULL,
    why_funny_or_effective TEXT DEFAULT '',
    trigger_context TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    rating INTEGER DEFAULT 0,
    seg_tokens TEXT DEFAULT '',
    created_at TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS dialogue_fts USING fts5(
    snippet_id UNINDEXED,
    scene_tag,
    raw_text,
    seg_tokens,
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS scene_highlights (
    id TEXT PRIMARY KEY,
    work_id TEXT,
    genre_id TEXT NOT NULL,
    chapter_range TEXT,
    line_start INTEGER,
    line_end INTEGER,
    hook_type TEXT DEFAULT '',
    plain_summary TEXT NOT NULL,
    why_it_hooks TEXT DEFAULT '',
    raw_excerpt TEXT DEFAULT '',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS jinghua_lock_rules (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    bad_pattern TEXT NOT NULL,
    is_regex INTEGER DEFAULT 0,
    reason TEXT DEFAULT '',
    good_replacement_example TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    hit_count INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS script_projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    genre_id TEXT DEFAULT 'hudong',
    project_mode TEXT DEFAULT 'branching',
    synopsis TEXT DEFAULT '',
    state_vars_json TEXT DEFAULT '{}',
    target_style_notes TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS story_nodes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    node_code TEXT NOT NULL,
    title TEXT NOT NULL,
    episode_num INTEGER DEFAULT 1,
    node_type TEXT DEFAULT 'scene',
    speaker_summary TEXT DEFAULT '',
    script_text TEXT DEFAULT '',
    camera_or_acting_cue TEXT DEFAULT '',
    pos_x REAL DEFAULT 120,
    pos_y REAL DEFAULT 120,
    jinghua_score INTEGER DEFAULT 100,
    user_rating INTEGER DEFAULT 0,
    revision_note TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS story_choices (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    from_node_id TEXT NOT NULL,
    to_node_id TEXT NOT NULL,
    choice_label TEXT NOT NULL,
    choice_tag TEXT DEFAULT '',
    condition_expr TEXT DEFAULT '',
    state_delta_json TEXT DEFAULT '{}',
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS script_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    old_text TEXT,
    new_text TEXT,
    user_rating INTEGER DEFAULT 0,
    note TEXT DEFAULT '',
    created_at TEXT
);
"""

DEFAULT_GENRES = [
    (
        "mengwa",
        "萌娃团宠",
        "幼崽反差萌、奶凶护短、长辈/兄长打脸宠娃",
        "女频/番茄/短剧萌娃线：童言无忌戳痛点、大人装酷反被萌化",
    ),
    (
        "shoushi",
        "兽世修罗场",
        "异世求生、雄竞修罗场、护短反差与契约拉扯",
        "女频兽世：强弱反差、领地占有欲、日常互怼中见偏爱",
    ),
    (
        "dushi",
        "都市爽文",
        "职场打脸、身份反转、金钱与人脉降维打击",
        "男频/女频都市短剧：节奏快、台词短脆、少废话直接亮底牌",
    ),
    (
        "chongsheng",
        "重生复仇",
        "前世血仇今生拆招、假意周旋、精准反杀",
        "女频重生/逆袭：表面不动声色，对话句句埋刀子",
    ),
    (
        "xuanyi",
        "悬疑推理",
        "密室疑云、言多必失、微表情与供词矛盾点",
        "悬疑/刑侦/剧本杀：靠细节问答露马脚，严禁旁白剧透情绪",
    ),
    (
        "guyan",
        "古言权谋",
        "宅斗朝堂、话里有话、礼数外衣下的借刀杀人",
        "古言/宫斗/权谋：台词克制留白，讲究捧杀、激将与借势",
    ),
    (
        "hudong",
        "互动影游",
        "多支线抉择、好感/怀疑数值变化、反转结局闭环",
        "真人互动影游/分歧短剧：每幕末尾留明确冲突选项，台词口语好演",
    ),
]

# 26 curated Jinghua Lock blacklist rules covering AI greasy narration & stagey clichés
DEFAULT_JINGHUA_RULES = [
    (
        "jh_01",
        "生造网感词",
        "瞬间卡壳",
        0,
        "AI 最爱用的机械顿挫词，像录音机坏了，没有真实人的呼吸感",
        "改写：他刚张嘴，话到嘴边又咽了回去 / 他顿了一下，没接着往下说",
    ),
    (
        "jh_02",
        "小品式夸张",
        "夹细了八度",
        0,
        "小品夸张声线描写，真人演出来像闹剧，破坏情绪真实度",
        "改写：她声音压低了些，带着点讨好 / 他说话比刚才客气不少",
    ),
    (
        "jh_03",
        "小品式夸张",
        "高了八度",
        0,
        "声音突然拔高“八度”过于套路，属于无脑抖机灵旁白",
        "改写：他忍不住提高了声音：“你说什么？” / 她声调一下扬了起来",
    ),
    (
        "jh_04",
        "小品式夸张",
        "涨成猪肝色",
        0,
        "烂大街的面部充血比喻，廉价搞笑且画面油腻",
        "改写：他脸色沉了下来，脖子连着耳根都发红",
    ),
    (
        "jh_05",
        "AI油腻旁白",
        "空气突然安静",
        0,
        "AI 偷懒制造冷场的万能废话，直接写众人没接话更有张力",
        "改写：屋里没人接话，只听见墙上挂钟走动的声音",
    ),
    (
        "jh_06",
        "AI油腻旁白",
        "空气仿佛凝固",
        0,
        "高频 AI 腔套话，空洞形容气氛，不如写具体的停顿动作",
        "改写：几个人互相看了一眼，谁也没先开口",
    ),
    (
        "jh_07",
        "小品式夸张",
        "石化在原地",
        0,
        "漫画式僵硬形容，放在现实或严肃剧情里瞬间出戏",
        "改写：他站在门口没动，手里的水杯差点没端稳",
    ),
    (
        "jh_08",
        "AI油腻旁白",
        r"嘴角(狠狠)?抽(搐|了抽)",
        1,
        "AI 表达无语时的固定肌肉抽风模板，千篇一律",
        "改写：他叹了口气，把刚拿起来的文件又放回桌上",
    ),
    (
        "jh_09",
        "AI油腻旁白",
        "嘴角勾起一抹",
        0,
        "霸总网文重灾区油腻起手式，过度做作",
        "改写：他笑了笑，没把话挑明 / 她弯了下嘴角",
    ),
    (
        "jh_10",
        "AI油腻旁白",
        "三分薄凉",
        0,
        "经典扇形统计图式眼神描写，油腻感爆表",
        "改写：他看人的眼神冷淡，说完便移开了视线",
    ),
    (
        "jh_11",
        "AI油腻旁白",
        "眼底闪过一丝不易察觉",
        0,
        "既说“不易察觉”旁白又全交代了，典型上帝视角水字数",
        "改写：他眼神微动，很快又垂下眼皮",
    ),
    (
        "jh_12",
        "小品式夸张",
        "倒吸一口凉气",
        0,
        "全员集体吸氧式廉价震惊描写，缺少人物差异感",
        "改写：旁边几个人一下静下来，纷纷转头看她",
    ),
    (
        "jh_13",
        "生造网感词",
        "杀气碎了一地",
        0,
        "网络段子手式硬凑反差，让原本建立的压迫感直接垮掉",
        "改写：他刚放完狠话，就被对方一句反问顶得没了脾气",
    ),
    (
        "jh_14",
        "小品式夸张",
        r"眼珠子(差点|都要|快)?掉(下来|一地|到地上)",
        1,
        "过度卡通化的震惊描写，与真人影视剧表演严重违和",
        "改写：他瞪大眼睛盯着屏幕，半天没回过神",
    ),
    (
        "jh_15",
        "生造网感词",
        "脚趾抠出三室一厅",
        0,
        "把旁白写成评论区玩梗弹幕，极度破坏沉浸感",
        "改写：她恨不得立刻找个借口溜出房间，连耳朵尖都热了",
    ),
    (
        "jh_16",
        "生造网感词",
        r"(额头|脑门)?(滑下|挂满|冒出)?三道黑线",
        1,
        "二次元漫画符号硬塞进小说旁白，显得敷衍",
        "改写：她揉了揉眉心，实在不知道怎么接他这句话",
    ),
    (
        "jh_17",
        "AI油腻旁白",
        "瞳孔骤缩",
        0,
        "AI 表示吃惊的高频套词，普通对话根本看不见对方瞳孔变化",
        "改写：他捏着杯柄的手指紧了一下，脸色微变",
    ),
    (
        "jh_18",
        "小品式夸张",
        r"下巴(差点|都要|快)?掉(到地上|下来|一地)",
        1,
        "夸张卡通化表达，应该用具体反应替代",
        "改写：他张了张嘴，好几秒才找回自己的声音",
    ),
    (
        "jh_19",
        "AI油腻旁白",
        "鸡皮疙瘩掉了一地",
        0,
        "口水套话，缺乏真实身体感",
        "改写：她手臂上起了一层鸡皮疙瘩，下意识往后退了半步",
    ),
    (
        "jh_20",
        "生造网感词",
        "仿佛被按下了暂停键",
        0,
        "机械比喻替代真实场面调度，信息量为零",
        "改写：门口几个人同时停住脚步，走廊里一下子安静下来",
    ),
    (
        "jh_21",
        "AI油腻旁白",
        "喉结上下滚动",
        0,
        "言情 AI 高频油腻生理反应模板，滥用显廉价",
        "改写：他咽了口唾沫，话到嘴边却没顺溜说出来",
    ),
    (
        "jh_22",
        "AI油腻旁白",
        "眼底猩红",
        0,
        "中二病式过度渲染愤怒，普通人发火不需要眼睛充血发光",
        "改写：他盯着地上那份协议，眼神沉得吓人",
    ),
    (
        "jh_23",
        "AI油腻旁白",
        r"声音冷得像淬了冰|冷笑一声",
        1,
        "高频套路化修饰语，直接让人物说出冷硬的台词更有效",
        "改写：他语气平平地开口：“既然这样，按合同办。”",
    ),
    (
        "jh_24",
        "AI油腻旁白",
        "一字一顿地吐出",
        0,
        "舞台腔太重，正常人吵架或放狠话少有逐字吐词的",
        "改写：他盯着对方，把最后一句话说得很重",
    ),
    (
        "jh_25",
        "生造网感词",
        "气场两米八",
        0,
        "热搜体/营销号体旁白，严重拉低剧本质感",
        "改写：她往主位上一坐，原本议论的几个人主动收了声",
    ),
    (
        "jh_26",
        "生造网感词",
        r"嘴角(疯狂上扬|压都压不住)",
        1,
        "短视频旁白腔，改写为具体克制的小动作更真实",
        "改写：他低头喝了口茶，借着杯沿挡住嘴角的笑意",
    ),
]


_GENRE_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("channel", "TEXT DEFAULT '通用'"),
    ("sub_group", "TEXT DEFAULT '题材主干'"),
    ("platforms", "TEXT DEFAULT ''"),
    ("sort_order", "INTEGER DEFAULT 0"),
)


def _migrate_genres_table(conn: sqlite3.Connection) -> None:
    """Smoothly add 3-channel taxonomy columns to existing v1 SQLite genres tables."""
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(genres)").fetchall()
    }
    for col_name, col_def in _GENRE_MIGRATION_COLUMNS:
        if col_name not in cols:
            conn.execute(f"ALTER TABLE genres ADD COLUMN {col_name} {col_def}")


def _reassign_genre_references(conn: sqlite3.Connection, old_id: str, new_id: str) -> None:
    """Reassign any records from a legacy genre_id to its new official tag ID."""
    for table in (
        "works",
        "dialogue_snippets",
        "characters",
        "scene_highlights",
        "script_projects",
    ):
        conn.execute(
            f"UPDATE {table} SET genre_id = ? WHERE genre_id = ?",
            (new_id, old_id),
        )


def _seed_genres(conn: sqlite3.Connection, now: str) -> None:
    """Idempotently sync the official 3-channel platform taxonomy and migrate legacy drawers."""
    for tag in OFFICIAL_TAGS:
        gid = str(tag["id"])
        gname = str(tag["name"])
        by_name = conn.execute(
            "SELECT id FROM genres WHERE name = ?",
            (gname,),
        ).fetchone()
        if by_name is not None and by_name["id"] != gid:
            _reassign_genre_references(conn, str(by_name["id"]), gid)
            conn.execute("DELETE FROM genres WHERE id = ?", (by_name["id"],))

        conn.execute(
            """
            INSERT INTO genres (
                id, name, description, channel_hint,
                channel, sub_group, platforms, sort_order, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                channel_hint = excluded.channel_hint,
                channel = excluded.channel,
                sub_group = excluded.sub_group,
                platforms = excluded.platforms,
                sort_order = excluded.sort_order
            """,
            (
                gid,
                gname,
                str(tag.get("description", "")),
                str(tag.get("channel_hint", "")),
                str(tag.get("channel", "通用")),
                str(tag.get("sub_group", "题材主干")),
                str(tag.get("platforms", "")),
                int(tag.get("sort_order", 0)),
                now,
            ),
        )

    for old_id, new_id in LEGACY_GENRE_MIGRATION.items():
        legacy_row = conn.execute(
            "SELECT id FROM genres WHERE id = ?",
            (old_id,),
        ).fetchone()
        if legacy_row is not None:
            _reassign_genre_references(conn, old_id, new_id)
            conn.execute("DELETE FROM genres WHERE id = ?", (old_id,))


def _seed_jinghua_rules(conn: sqlite3.Connection, now: str) -> None:
    for rid, category, pattern, is_regex, reason, example in DEFAULT_JINGHUA_RULES:
        conn.execute(
            """
            INSERT OR IGNORE INTO jinghua_lock_rules
            (id, category, bad_pattern, is_regex, reason, good_replacement_example, enabled, hit_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?)
            """,
            (rid, category, pattern, is_regex, reason, example, now),
        )


def _seed_demo_interactive_project(conn: sqlite3.Connection, now: str) -> None:
    project_id = "demo_hudong_01"
    conn.execute(
        """
        INSERT OR IGNORE INTO script_projects
        (id, title, genre_id, project_mode, synopsis, state_vars_json, target_style_notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            "示例：茶水间读心术（互动影游 4 节点演示）",
            "hudong",
            "branching",
            "极简演示模板：开场在公司茶水间拿错总监的冰美式，3 个选择分别改变【好感度】与【怀疑度】，通向不同结局。",
            json.dumps({"好感度": 50, "怀疑度": 10}, ensure_ascii=False),
            "台词短平快，旁白只写平实动作，拒绝 AI 油腻腔",
            now,
            now,
        ),
    )

    nodes = [
        (
            "demo_node_s1",
            project_id,
            "S1",
            "开场：端错的那杯冰美式",
            1,
            "start",
            "主角林夏 vs 总监周沉",
            "茶水间门口，周沉低头看了一眼林夏手里的无糖冰美式，又看了一眼自己桌上那杯半糖加奶。\n周沉：“你手里的杯子，名字好像是我。”\n林夏看清杯贴上的字母，直接把杯子放回他桌面。",
            "特写杯贴名字，切周沉抬眼看人",
            80.0,
            180.0,
            100,
            5,
            "起始节点：15秒内抛冲突",
        ),
        (
            "demo_node_s2",
            project_id,
            "S2",
            "对峙：他反问你是不是故意的",
            1,
            "climax",
            "林夏 vs 周沉",
            "周沉没急着拿杯子，指尖敲了下桌面：“今天早上部门周报，你也写错了我的名字。”\n林夏：“周总记性这么好，那上个月我垫付的打车费，您签字了吗？”",
            "双人近景正反打，节奏干脆",
            400.0,
            110.0,
            100,
            5,
            "反客为主的拉扯点",
        ),
        (
            "demo_node_e1",
            project_id,
            "E1",
            "好结局：拿到核心项目主笔位",
            1,
            "good_ending",
            "林夏、周沉",
            "周沉停顿两秒，签字笔在报销单上落下一笔，顺手把新项目的立项表推到她面前。\n周沉：“晚上七点，会议室对第一版分镜。”",
            "阳光切入办公桌，落版出主线任务",
            740.0,
            70.0,
            100,
            5,
            "爽点落袋",
        ),
        (
            "demo_node_e2",
            project_id,
            "E2",
            "坏结局：甩锅翻车被调离核心组",
            1,
            "bad_ending",
            "林夏、实习生小李",
            "周沉看了眼监控方向，没有拆穿，只叫来行政把新项目分组表换了人。\n周沉：“下次分不清自己的东西，就去资料室先理清台账。”",
            "冷调全景，人走茶凉",
            740.0,
            270.0,
            100,
            4,
            "惩罚性分支结局",
        ),
    ]

    for row in nodes:
        conn.execute(
            """
            INSERT OR IGNORE INTO story_nodes
            (id, project_id, node_code, title, episode_num, node_type, speaker_summary,
             script_text, camera_or_acting_cue, pos_x, pos_y, jinghua_score,
             user_rating, revision_note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*row, now, now),
        )

    choices = [
        (
            "demo_ch_01",
            project_id,
            "demo_node_s1",
            "demo_node_s2",
            "A. 坦白拿错了，顺手递上自己的乌龙茶",
            "坦诚拉好感",
            "",
            json.dumps({"好感度": 15, "怀疑度": -5}, ensure_ascii=False),
            1,
        ),
        (
            "demo_ch_02",
            project_id,
            "demo_node_s1",
            "demo_node_e2",
            "B. 嘴硬假装没看见，把锅甩给实习生",
            "甩锅翻车",
            "",
            json.dumps({"好感度": -25, "怀疑度": 30}, ensure_ascii=False),
            2,
        ),
        (
            "demo_ch_03",
            project_id,
            "demo_node_s2",
            "demo_node_e1",
            "C. 借报销反将一军，顺势接下新项目",
            "反客为主",
            "好感度>=60",
            json.dumps({"好感度": 20, "怀疑度": -10}, ensure_ascii=False),
            1,
        ),
    ]

    for c_row in choices:
        conn.execute(
            """
            INSERT OR IGNORE INTO story_choices
            (id, project_id, from_node_id, to_node_id, choice_label, choice_tag,
             condition_expr, state_delta_json, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            c_row,
        )


def init_db(conn: sqlite3.Connection) -> None:
    """Initialize all SQLite tables, FTS5 index, schema migrations, and idempotent starter data."""
    conn.executescript(SCHEMA_SQL)
    _migrate_genres_table(conn)
    now = utc_now_iso()

    # Sync the 3-channel official taxonomy once per v2 schema upgrade (or if genres is empty).
    taxonomy_meta = conn.execute(
        "SELECT value FROM kb_meta WHERE key = 'taxonomy_v2_synced'"
    ).fetchone()
    genre_count_row = conn.execute("SELECT COUNT(*) FROM genres").fetchone()
    genre_count = int(genre_count_row[0]) if genre_count_row is not None else 0
    if taxonomy_meta is None or genre_count == 0:
        _seed_genres(conn, now)
        conn.execute(
            "INSERT OR REPLACE INTO kb_meta (key, value) VALUES ('taxonomy_v2_synced', ?)",
            (now,),
        )

    # Seed initial Jinghua Lock rules and 4-node demo project once (idempotent even if user later deletes items).
    meta_row = conn.execute(
        "SELECT value FROM kb_meta WHERE key = 'initial_seed_v1'"
    ).fetchone()
    if meta_row is None:
        _seed_jinghua_rules(conn, now)
        _seed_demo_interactive_project(conn, now)
        conn.execute(
            "INSERT OR IGNORE INTO kb_meta (key, value) VALUES ('initial_seed_v1', ?)",
            (now,),
        )
    conn.commit()


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a configured SQLite connection, initializing or migrating schema if needed."""
    target = Path(db_path) if db_path is not None else get_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")

    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='genres'"
    ).fetchone()
    if exists is None:
        init_db(conn)
    else:
        synced = None
        meta_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='kb_meta'"
        ).fetchone()
        if meta_exists is not None:
            synced = conn.execute(
                "SELECT 1 FROM kb_meta WHERE key = 'taxonomy_v2_synced'"
            ).fetchone()
        if synced is None:
            init_db(conn)
    return conn
