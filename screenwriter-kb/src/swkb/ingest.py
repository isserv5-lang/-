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
from swkb.taxonomy import TAG_BY_ID

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

MAX_WORK_TAGS = 6

# High-precision keyword lexicon mapped strictly to swkb.taxonomy.OFFICIAL_TAGS IDs.
# Each entry has ("strong" unmistakable markers, "normal" supporting markers).
TAG_FEATURE_RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # --- 女频 ---
    "gongdou": (
        ("宫斗", "宅斗", "庶女", "嫡女", "姨娘", "贵妃", "后宅", "庶妹", "嫡姐", "掌家", "侯府", "世子"),
        ("太后", "皇后", "主母", "休书", "宫宴", "赐婚", "嫔妃", "宗室", "王府", "管家权", "侧妃"),
    ),
    "xianyan_naodong": (
        ("现言脑洞", "吃瓜系统", "恋综", "娱乐圈", "热搜第一", "顶流"),
        ("热搜", "经纪人", "综艺", "粉丝", "影帝", "弹幕", "微博", "直播"),
    ),
    "guyan_naodong": (
        ("流放", "逃荒", "灵泉空间", "随身空间", "抄家流放", "古言脑洞"),
        ("抄家", "灵泉", "空间", "囤物资", "荒年", "古代", "县令", "朝堂"),
    ),
    "haomen": (
        ("豪门", "财阀", "总裁", "联姻", "黑卡", "董事会"),
        ("集团", "收购", "股份", "别墅", "管家", "助理", "千金", "少爷", "宴会"),
    ),
    "niandai_nv": (
        ("七零", "八零", "知青", "供销社", "粮票", "工分", "生产队", "下乡"),
        ("大队长", "极品亲戚", "分家", "彩礼", "摆摊", "国营饭店", "布票", "年代"),
    ),
    "shoushi": (
        ("兽世", "兽夫", "兽人", "雄性", "雌性", "精神海", "兽纹", "兽形", "狂化"),
        ("伴侣", "部落", "结侣", "兽族", "亚兽", "蛇兽", "狼兽", "狐兽", "虎兽", "雌尊"),
    ),
    "xuanhuan_yq": (
        ("师尊", "仙尊", "魔尊", "灵根", "契约兽", "渡劫", "剑宗"),
        ("宗门", "灵脉", "丹药", "秘境", "仙界", "魔界", "灵力", "飞升", "长老"),
    ),
    "mengwa": (
        ("萌宝", "团宠", "奶团", "小奶团", "小团子", "幼崽", "奶声奶气", "小奶音", "崽崽", "乖宝"),
        ("小家伙", "小丫头", "软糯", "糖葫芦", "太爷爷", "爹地", "妈咪", "舅舅", "抱抱", "奶凶", "小朋友"),
    ),
    "zhenjia_qianjin": (
        ("真千金", "假千金", "抱错", "断亲", "亲子鉴定", "养女"),
        ("亲生女儿", "认亲", "偏心", "逐出家门", "亲生父母", "亲哥"),
    ),
    "zhuiqi": (
        ("追妻", "火葬场", "离婚协议", "白月光", "替身", "复婚"),
        ("离婚", "后悔", "挽回", "冷落", "原谅", "跪求", "心死", "放手"),
    ),
    "chuanshu_sidun": (
        ("穿书", "死遁", "假死", "恶毒女配", "万人嫌", "原著", "炮灰"),
        ("剧本", "剧情", "情节", "男主", "女主", "书里", "下线", "白月光", "攻略", "结局"),
    ),
    "danvzhu": (
        ("虐渣", "渣男", "绿茶", "白莲花", "和离", "休夫", "独美"),
        ("打脸", "实锤", "反杀", "退婚", "搞事业", "清醒"),
    ),
    "duxinshu": (
        ("读心", "读心术", "偷听心声", "心声", "吃瓜", "内心吐槽"),
        ("腹诽", "心里暗骂", "心底", "瓜田", "听见", "心里话"),
    ),
    "xianhun_houai": (
        ("先婚后爱", "协议结婚", "契约婚姻", "隐婚", "结婚证"),
        ("领证", "民政局", "婚后", "同居", "夫妻", "联姻"),
    ),
    "shanhun_tijia": (
        ("闪婚", "替嫁", "冲喜", "代嫁"),
        ("领证", "民政局", "婚约", "嫁给", "残疾大佬"),
    ),
    "bingjiao_jiushu": (
        ("病娇", "偏执", "疯批", "阴鸷", "囚禁", "黑化", "救赎"),
        ("占有欲", "锁链", "猩红", "别想逃", "乖乖", "发疯"),
    ),
    # --- 男频 ---
    "dushi_naodong": (
        ("都市脑洞", "金手指", "觉醒异能", "外挂"),
        ("觉醒", "异能", "都市", "直播间", "脑洞", "奇葩"),
    ),
    "dushi_gaowu": (
        ("高武", "灵气复苏", "气血", "武考", "异兽", "凶兽", "武馆"),
        ("武道", "宗师", "战神", "境界", "突破", "秘境", "淬体"),
    ),
    "lishi_naodong": (
        ("科举", "屯田", "朝堂", "造纸", "火药", "大明", "大唐", "大秦"),
        ("皇帝", "皇子", "丞相", "武将", "边关", "精盐", "玻璃", "朝廷"),
    ),
    "chuantong_xh": (
        ("丹田", "经脉", "宗门大比", "斗气", "炼丹", "剑意", "废柴"),
        ("灵力", "功法", "圣地", "退婚", "秘境", "境界", "长老"),
    ),
    "xuanhuan_nd": (
        ("长生", "苟道", "人生模拟", "模拟器", "气运之子", "熟练度"),
        ("签到", "面板", "加点", "老六", "反派", "词条"),
    ),
    "xuanyi": (
        ("厉鬼", "鬼魂", "阴气", "阴差", "煞气", "捉鬼", "驱邪", "诡异", "诡事"),
        ("棺材", "墓地", "怨气", "凶案", "尸检", "符箓", "道士", "阵法", "玄学", "民俗"),
    ),
    "moshi_tunhuo": (
        ("末世", "丧尸", "囤货", "安全屋", "避难所", "晶核", "天灾"),
        ("极寒", "极热", "物资", "异能", "空间", "变异", "基地"),
    ),
    "zhutian": (
        ("诸天", "无限流", "主神", "轮回空间", "位面", "剧情世界"),
        ("副本", "穿梭", "积分", "兑换", "任务世界", "穿越世界"),
    ),
    "zhanshen_zx": (
        ("赘婿", "上门女婿", "战神令", "龙王", "殿主"),
        ("战神", "丈母娘", "退婚", "护国", "跪下"),
    ),
    "shenhao": (
        ("神豪", "消费返利", "舔狗金", "百亿", "亿万"),
        ("返利", "刷卡", "黑卡", "豪车", "包场", "买下", "资产"),
    ),
    "guize_guaitan": (
        ("规则怪谈", "诡异副本", "违反规则", "守则", "理智值"),
        ("规则", "怪谈", "纸条", "污染", "必须遵守", "不要相信"),
    ),
    "xiangcun": (
        ("村霸", "种田", "鱼塘", "山货", "承包", "乡亲"),
        ("村长", "养殖", "药材", "果园", "村里", "灵泉"),
    ),
    "jianbao_shenyi": (
        ("鉴宝", "黄金瞳", "捡漏", "神医", "银针", "针灸"),
        ("古玩", "青花瓷", "赝品", "把脉", "药方", "悬壶"),
    ),
    "gaozhishang": (
        ("刑侦", "嫌疑人", "老千", "案发现场", "审讯"),
        ("推理", "布局", "赌场", "暗语", "破案", "凶手", "逻辑"),
    ),
    "niandai_nan": (
        ("倒货", "倒爷", "国营厂", "录像厅", "万元户", "下海"),
        ("厂长", "批发", "磁带", "供销社", "改革开放"),
    ),
    # --- 通用 ---
    "xitong": (
        ("系统", "宿主", "任务完成", "系统提示", "绑定系统", "好感度", "面板"),
        ("叮", "积分", "奖励", "签到", "商城", "提示音", "绑定", "进度条", "任务"),
    ),
    "chongsheng": (
        ("重生", "上一世", "上辈子", "前世", "这一世", "重活一世"),
        ("血仇", "复仇", "重来", "改变命运", "前生", "今生"),
    ),
    "jixian_fanzhuan": (
        ("反转", "揭穿", "亲子鉴定", "亮出证据", "录音笔"),
        ("监控", "录音", "证据", "真相", "打脸", "傻眼", "实锤"),
    ),
    "siwang_huidang": (
        ("回档", "存档", "读档", "重置时间", "时间循环"),
        ("循环", "重置", "时间线", "重开", "第几次"),
    ),
    "qunxiang_zuipao": (
        ("互怼", "群像", "拌嘴", "贫嘴", "损友"),
        ("吐槽", "憋笑", "哄堂大笑", "起哄", "接话"),
    ),
    "majia": (
        ("马甲", "掉马", "隐藏身份", "幕后老板", "身份曝光"),
        ("大佬", "传人", "竟然是", "居然是", "黑客", "神医"),
    ),
    "banzhu": (
        ("扮猪吃虎", "深藏不露", "隐藏实力", "土包子", "乡巴佬"),
        ("废物", "嘲讽", "轻视", "碾压", "狗眼看人低"),
    ),
    "zhibo_suanming": (
        ("算命", "卜卦", "卦象", "玄学", "连麦", "天师", "血光之灾", "掐指一算", "批命", "符纸", "道观"),
        ("风水", "符箓", "面相", "观相", "弹幕", "直播间", "大师", "祖师", "煞气", "铜钱", "罗盘", "算一卦"),
    ),
    "fafeng": (
        ("发疯文学", "掀桌", "创死", "道德绑架", "精神状态"),
        ("发疯", "素质", "忍无可忍", "奇葩"),
    ),
    "xiuluochang": (
        ("修罗场", "雄竞", "情敌", "争风吃醋", "醋意"),
        ("吃醋", "争夺", "偏爱", "占有欲", "咬牙切齿", "争宠", "冷哼", "盯着她"),
    ),
    "wucp": (
        ("无CP", "搞事业", "事业脑", "不谈恋爱"),
        ("搞钱", "专心修炼", "升级"),
    ),
    "shuangqiang": (
        ("双强", "势均力敌", "棋逢对手"),
        ("联手", "合作", "试探", "默契", "暗中配合"),
    ),
}


def _parse_extra_tag_ids(raw: list[str] | tuple[str, ...] | str | None) -> list[str]:
    """Normalize optional extra_genre_ids from list/tuple/comma-separated string."""
    if raw is None:
        return []
    parts: list[str]
    if isinstance(raw, str):
        parts = re.split(r"[,，;；\s]+", raw.strip())
    else:
        parts = [str(item) for item in raw]
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tid = part.strip()
        if tid and tid not in seen:
            seen.add(tid)
            result.append(tid)
    return result


def detect_secondary_tags(
    text: str,
    title: str,
    primary_genre_id: str,
    existing_extra_ids: list[str] | None = None,
    max_auto: int = 3,
) -> list[str]:
    """Detect 1~3 high-confidence secondary official tags from book title and clean body text."""
    if max_auto <= 0:
        return []
    excluded = {primary_genre_id.strip()} | set(existing_extra_ids or [])
    sample_text = (text or "")[:120_000]
    clean_title = (title or "").strip()

    scored: list[tuple[float, str]] = []
    for tag_id, (strong_words, normal_words) in TAG_FEATURE_RULES.items():
        if tag_id in excluded or tag_id not in TAG_BY_ID:
            continue

        title_strong = sum(1 for w in strong_words if w in clean_title)
        title_normal = sum(1 for w in normal_words if w in clean_title)

        body_strong_distinct = 0
        body_strong_hits = 0
        body_normal_distinct = 0
        body_normal_hits = 0

        for w in strong_words:
            c = sample_text.count(w)
            if c > 0:
                body_strong_distinct += 1
                body_strong_hits += min(c, 12)

        for w in normal_words:
            c = sample_text.count(w)
            if c > 0:
                body_normal_distinct += 1
                body_normal_hits += min(c, 8)

        distinct_total = body_strong_distinct + body_normal_distinct
        total_hits = body_strong_hits + body_normal_hits

        is_confident = (
            title_strong >= 1
            or (title_normal >= 2)
            or (body_strong_distinct >= 2 and body_strong_hits >= 3)
            or (body_strong_distinct >= 1 and distinct_total >= 2 and total_hits >= 5)
            or (distinct_total >= 3 and total_hits >= 8)
        )
        if not is_confident:
            continue

        score = (
            title_strong * 12.0
            + title_normal * 4.0
            + body_strong_distinct * 3.5
            + body_strong_hits * 1.5
            + body_normal_distinct * 1.2
            + body_normal_hits * 0.5
        )
        scored.append((score, tag_id))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [tag_id for _, tag_id in scored[:max_auto]]


def _score_text_for_tag(text: str, tag_id: str) -> float:
    rules = TAG_FEATURE_RULES.get(tag_id)
    if rules is None:
        return 0.0
    strong_words, normal_words = rules
    score = 0.0
    for w in strong_words:
        c = text.count(w)
        if c > 0:
            score += min(c, 3) * 3.0
    for w in normal_words:
        c = text.count(w)
        if c > 0:
            score += min(c, 3) * 1.0
    return score


def _assign_snippet_genre(
    raw_text: str,
    trigger_context: str,
    primary_genre_id: str,
    work_tag_ids: list[str],
) -> str:
    """Route a candidate dialogue snippet to the best-fitting tag among the work's own tags."""
    if len(work_tag_ids) <= 1:
        return primary_genre_id

    combined = f"{raw_text or ''}\n{trigger_context or ''}"
    primary_score = _score_text_for_tag(combined, primary_genre_id)

    best_tag = primary_genre_id
    best_score = primary_score
    for tid in work_tag_ids:
        if tid == primary_genre_id:
            continue
        sec_score = _score_text_for_tag(combined, tid)
        if sec_score >= 2.0 and sec_score > best_score:
            best_score = sec_score
            best_tag = tid
    return best_tag


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


def _fetch_work_tags_for_ingest(conn, work_id: str, fallback_primary: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT wt.tag_id, wt.is_primary, g.name, g.channel, g.sub_group
        FROM work_tags wt
        LEFT JOIN genres g ON g.id = wt.tag_id
        WHERE wt.work_id = ?
        ORDER BY wt.is_primary DESC, wt.rowid ASC
        """,
        (work_id,),
    ).fetchall()
    tags = [
        {
            "id": str(r["tag_id"]),
            "name": str(r["name"] or r["tag_id"]),
            "channel": str(r["channel"] or "通用"),
            "sub_group": str(r["sub_group"] or "题材主干"),
            "is_primary": 1 if int(r["is_primary"] or 0) else 0,
        }
        for r in rows
    ]
    if not tags and fallback_primary:
        g_row = conn.execute(
            "SELECT name, channel, sub_group FROM genres WHERE id = ?",
            (fallback_primary,),
        ).fetchone()
        tags = [
            {
                "id": fallback_primary,
                "name": str(g_row["name"]) if g_row is not None else fallback_primary,
                "channel": str(g_row["channel"] or "通用") if g_row is not None else "通用",
                "sub_group": str(g_row["sub_group"] or "题材主干") if g_row is not None else "题材主干",
                "is_primary": 1,
            }
        ]
    return tags


def import_novel_file(
    file_path: str | Path,
    genre_id: str = "mengwa",
    title: str | None = None,
    author: str = "",
    extra_genre_ids: list[str] | None = None,
    auto_detect_tags: bool = True,
    db_path: Path | None = None,
) -> dict:
    """Import a .txt or .docx novel, detect corrupted anti-piracy chapters, auto-tag, and extract pending dialogues."""
    src_path = Path(file_path).expanduser()
    if not src_path.exists() or not src_path.is_file():
        raise FileNotFoundError(f"Novel file not found: {src_path}")

    primary_genre = (genre_id or "mengwa").strip() or "mengwa"
    manual_extras = [
        tid for tid in _parse_extra_tag_ids(extra_genre_ids) if tid != primary_genre
    ][: MAX_WORK_TAGS - 1]

    full_text, sha256 = load_novel_text(src_path)
    book_title = (title or "").strip() or src_path.stem

    conn = get_conn(db_path)
    try:
        # Idempotent check on exact file hash + primary genre
        existing = conn.execute(
            "SELECT * FROM works WHERE file_sha256 = ? AND genre_id = ?",
            (sha256, primary_genre),
        ).fetchone()
        if existing is not None and existing["norm_path"] and Path(existing["norm_path"]).exists():
            now = utc_now_iso()
            existing_id = str(existing["id"])
            conn.execute(
                """
                INSERT INTO work_tags (work_id, tag_id, is_primary, created_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(work_id, tag_id) DO UPDATE SET is_primary = 1
                """,
                (existing_id, primary_genre, now),
            )
            if manual_extras:
                cur_count = conn.execute(
                    "SELECT COUNT(*) FROM work_tags WHERE work_id = ?",
                    (existing_id,),
                ).fetchone()[0]
                for tid in manual_extras:
                    if cur_count >= MAX_WORK_TAGS:
                        break
                    cur = conn.execute(
                        """
                        INSERT OR IGNORE INTO work_tags (work_id, tag_id, is_primary, created_at)
                        VALUES (?, ?, 0, ?)
                        """,
                        (existing_id, tid, now),
                    )
                    if cur.rowcount > 0:
                        cur_count += 1
            conn.commit()
            existing_tags = _fetch_work_tags_for_ingest(conn, existing_id, primary_genre)
            corrupted_rows = conn.execute(
                "SELECT chapter_num, title, corruption_reason FROM chapters WHERE work_id = ? AND is_corrupted = 1 ORDER BY chapter_num",
                (existing_id,),
            ).fetchall()
            pending_count = conn.execute(
                "SELECT COUNT(*) FROM dialogue_snippets WHERE work_id = ? AND status = 'pending'",
                (existing_id,),
            ).fetchone()[0]
            return {
                "work_id": existing_id,
                "title": existing["title"],
                "author": existing["author"],
                "genre_id": existing["genre_id"],
                "tag_ids": [t["id"] for t in existing_tags],
                "tags": existing_tags,
                "auto_detected_tags": [],
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

        # Auto-detect 1~3 high-confidence secondary tags strictly from clean chapters + title
        clean_body_text = "\n".join(
            ch["text"] for ch in raw_chapters if not ch["is_corrupted"]
        )
        auto_detected: list[str] = []
        if auto_detect_tags:
            remaining_slots = max(0, (MAX_WORK_TAGS - 1) - len(manual_extras))
            auto_limit = min(3, remaining_slots)
            if auto_limit > 0:
                auto_detected = detect_secondary_tags(
                    text=clean_body_text,
                    title=book_title,
                    primary_genre_id=primary_genre,
                    existing_extra_ids=manual_extras,
                    max_auto=auto_limit,
                )

        all_extras = (
            manual_extras + [tid for tid in auto_detected if tid not in manual_extras]
        )[: MAX_WORK_TAGS - 1]
        work_tag_ids = [primary_genre, *all_extras]

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
                primary_genre,
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

        # Persist primary and secondary tags into work_tags
        conn.execute(
            """
            INSERT OR REPLACE INTO work_tags (work_id, tag_id, is_primary, created_at)
            VALUES (?, ?, 1, ?)
            """,
            (work_id, primary_genre, now),
        )
        for tid in all_extras:
            conn.execute(
                """
                INSERT OR IGNORE INTO work_tags (work_id, tag_id, is_primary, created_at)
                VALUES (?, ?, 0, ?)
                """,
                (work_id, tid, now),
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
            sn_genre_id = _assign_snippet_genre(
                raw_text=sn["raw_text"],
                trigger_context=sn["trigger_context"],
                primary_genre_id=primary_genre,
                work_tag_ids=work_tag_ids,
            )
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
                    sn_genre_id,
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
                    "genre_id": sn_genre_id,
                    "chapter_num": sn["chapter_num"],
                    "line_start": sn["line_start"],
                    "line_end": sn["line_end"],
                    "scene_tag": sn["scene_tag"],
                    "raw_text": sn["raw_text"],
                }
            )

        conn.commit()
        work_tags = _fetch_work_tags_for_ingest(conn, work_id, primary_genre)
        return {
            "work_id": work_id,
            "title": book_title,
            "author": author.strip(),
            "genre_id": primary_genre,
            "tag_ids": [t["id"] for t in work_tags],
            "tags": work_tags,
            "auto_detected_tags": auto_detected,
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
