"""End-to-end integration test suite for screenwriter-kb (isolated temporary data vault)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from swkb import mcp_server
from swkb.branching import (
    create_script_project,
    export_branching_script_markdown,
    get_project_graph,
    list_node_revisions,
    upsert_story_choice,
    upsert_story_node,
)
from swkb.config import get_data_dir, get_db_path
from swkb.db import get_conn
from swkb.ingest import import_novel_file, read_novel_span
from swkb.jinghua_lock import (
    add_jinghua_rule,
    check_text_with_jinghua_lock,
    list_jinghua_rules,
)
from swkb.originality import check_script_originality
from swkb.repository import (
    get_dashboard_stats,
    get_work_detail,
    list_characters,
    list_dialogue_snippets,
    list_genres,
    list_scene_highlights,
    review_dialogue_snippet,
    save_dialogue_snippet,
    save_scene_highlight,
    upsert_character,
    upsert_genre,
)
from swkb.web_api import app

SAMPLE_TXT = Path(r"D:\1\软件\小说\云糯糯.txt")
SAMPLE_FAKE_DOCX = Path(r"D:\1\软件\小说\兽世万人嫌？可我是死遁的白月光..docx")


@pytest.fixture()
def isolated_kb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate SWKB_DATA_DIR per test so D:\\ScreenwriterKB is never touched."""
    vault = tmp_path / "vault"
    monkeypatch.setenv("SWKB_DATA_DIR", str(vault))
    data_dir = get_data_dir()
    db_path = get_db_path()
    conn = get_conn(db_path)
    conn.close()
    assert data_dir == vault
    return db_path


def test_1_drawers_create_and_list(isolated_kb: Path) -> None:
    """测试 1：全类型抽屉预置、新建与读取。"""
    genres = list_genres()
    genre_ids = {g["id"] for g in genres}
    assert {"mengwa", "shoushi", "dushi", "chongsheng", "xuanyi", "guyan", "hudong"} <= genre_ids

    created = upsert_genre(
        genre_id="miniguo",
        name="民国谍战",
        description="双面身份潜伏、暗语试探与高压审讯交锋",
        channel_hint="男频/女频谍战短剧",
    )
    assert created["id"] == "miniguo"
    assert created["name"] == "民国谍战"

    updated_genres = list_genres()
    assert any(g["id"] == "miniguo" and g["channel_hint"] == "男频/女频谍战短剧" for g in updated_genres)

    stats = get_dashboard_stats()
    assert stats["total_genres"] == 8
    assert stats["total_works"] == 0
    assert stats["total_jinghua_rules"] >= 20


@pytest.mark.skipif(
    not (SAMPLE_TXT.exists() and SAMPLE_FAKE_DOCX.exists()),
    reason="本地测试小说样本不存在",
)
def test_2_import_real_novels_and_detect_fake_docx_corruption(isolated_kb: Path) -> None:
    """测试 2：只读导入真实样本（含伪 .docx）并准确识别第 18 章后的防盗坏章。"""
    res_txt = import_novel_file(SAMPLE_TXT, genre_id="mengwa")
    assert res_txt["already_exists"] is False
    assert res_txt["total_chapters"] >= 10
    assert res_txt["clean_chapters"] >= 10
    assert res_txt["corrupted_chapters"] == 0
    assert 10 <= res_txt["pending_snippets_count"] <= 15

    res_docx = import_novel_file(SAMPLE_FAKE_DOCX, genre_id="shoushi")
    assert res_docx["already_exists"] is False
    assert res_docx["total_chapters"] > 100
    assert res_docx["clean_chapters"] > 0
    assert res_docx["corrupted_chapters"] >= 10
    assert 10 <= res_docx["pending_snippets_count"] <= 15

    detail = get_work_detail(res_docx["work_id"])
    assert detail is not None
    ch_map = {ch["chapter_num"]: ch for ch in detail["chapters"]}
    assert 18 in ch_map
    assert ch_map[18]["is_corrupted"] is True
    assert "手打" in ch_map[18]["corruption_reason"] or "防盗" in ch_map[18]["corruption_reason"]
    assert any(ch["chapter_num"] > 18 and ch["is_corrupted"] for ch in detail["chapters"])

    # Verify idempotent re-import skips duplicate insertion
    res_dup = import_novel_file(SAMPLE_TXT, genre_id="mengwa")
    assert res_dup["already_exists"] is True
    assert res_dup["work_id"] == res_txt["work_id"]


def test_3_taste_check_fts5_and_span_readback(isolated_kb: Path, tmp_path: Path) -> None:
    """测试 3：待试菜对白打 ✓/✗ 校准入库、FTS5 中文检索、按章号行号回读原文。"""
    novel_file = tmp_path / "sample_mengwa.txt"
    novel_file.write_text(
        "\n".join(
            [
                "第1章 小奶团进门",
                "顾老爷子板着脸坐在沙发上。",
                "小团子抱紧小书包：“太爷爷，你凶我一次，我就多吃一碗饭哦！”",
                "管家憋笑：“小小姐说得对，气大伤身，饭香养人。”",
                "第2章 书房反击",
                "反派堂姐故意打翻茶杯：“哎呀，乡下来的土包子就是毛手毛脚。”",
                "小团子立刻蹲下捡起瓷片：“姐姐别慌，监控就在头顶亮红灯呢。”",
                "满屋亲戚脸色一变，谁也没敢再帮腔。",
            ]
        ),
        encoding="utf-8",
    )

    imp = import_novel_file(novel_file, genre_id="mengwa")
    work_id = imp["work_id"]

    # Save 2 pending snippets and calibrate one as ✓ approved, one as ✗ rejected
    sn_good = save_dialogue_snippet(
        genre_id="mengwa",
        raw_text="小团子立刻蹲下捡起瓷片：“姐姐别慌，监控就在头顶亮红灯呢。”",
        work_id=work_id,
        chapter_num=2,
        line_start=3,
        line_end=3,
        scene_tag="奶凶反杀/亮监控",
        why_funny_or_effective="小孩用平淡语气戳穿栽赃，反差感极强",
        status="pending",
    )
    sn_bad = save_dialogue_snippet(
        genre_id="mengwa",
        raw_text="反派堂姐故意打翻茶杯：“哎呀，乡下来的土包子就是毛手毛脚。”",
        work_id=work_id,
        chapter_num=2,
        line_start=2,
        line_end=2,
        scene_tag="炮灰挑衅",
        status="pending",
    )

    approved = review_dialogue_snippet(sn_good["id"], status="approved", rating=5, scene_tag="反杀亮监控")
    rejected = review_dialogue_snippet(sn_bad["id"], status="rejected", rating=1)
    assert approved["status"] == "approved"
    assert rejected["status"] == "rejected"

    # Markdown card should exist only for approved snippet
    approved_card = get_data_dir() / "cards" / "mengwa" / f"dialogue_{sn_good['id']}.md"
    rejected_card = get_data_dir() / "cards" / "mengwa" / f"dialogue_{sn_bad['id']}.md"
    assert approved_card.exists()
    assert not rejected_card.exists()

    # FTS5 Chinese search with genre drawer isolation
    hits = list_dialogue_snippets(genre_id="mengwa", status="approved", query="监控")
    assert any(item["id"] == sn_good["id"] for item in hits)
    other_drawer = list_dialogue_snippets(genre_id="shoushi", status="approved", query="监控")
    assert all(item["id"] != sn_good["id"] for item in other_drawer)

    # Exact chapter & line span readback
    span = read_novel_span(work_id=work_id, chapter_num=2, line_start=2, line_end=4)
    assert span["chapter_num"] == 2
    assert span["line_start"] == 2
    assert span["line_end"] == 4
    assert len(span["lines"]) == 3
    assert span["lines"][1]["line_num"] == 3
    assert "监控就在头顶亮红灯呢" in span["lines"][1]["text"]


def test_4_jinghua_lock_intercept_and_dynamic_rule(isolated_kb: Path) -> None:
    """测试 4：精话锁拦截指定油腻旁白、保护引号内台词，并支持动态新增黑名单词。"""
    greasy_text = (
        "满屋杀气瞬间卡壳，粗嗓门硬生生夹细了八度，老脸涨成猪肝色。\n"
        "他冷冷说道：“你别以为我不敢动手。”"
    )
    res = check_text_with_jinghua_lock(greasy_text)
    assert res["passed"] is False
    assert res["score"] < 80
    matched = [h["matched_text"] for h in res["hits"]]
    assert "瞬间卡壳" in matched
    assert "夹细了八度" in matched
    assert "涨成猪肝色" in matched

    # Quoted dialogue should be isolated from narration blacklist checks
    dialogue_only = '他放下茶杯，低声说道：“刚才差点石化在原地，空气突然安静得吓人。”'
    res_quoted = check_text_with_jinghua_lock(dialogue_only)
    assert res_quoted["passed"] is True
    assert res_quoted["hits"] == []

    # Plain human-like narration passes
    clean_text = (
        "屋里几个人都没接着往下说，老大把茶杯放回桌上，指节在桌沿敲了两下。\n"
        "他抬眼看过去：“这事明天再算。”"
    )
    assert check_text_with_jinghua_lock(clean_text)["passed"] is True

    # Dynamically add a new blacklist phrase and verify immediate enforcement
    new_rule = add_jinghua_rule(
        bad_pattern="仿佛过了一个世纪",
        category="AI油腻旁白",
        reason="时间比喻太假太水字数",
        good_replacement_example="改写：他等了五六秒才开口",
    )
    assert new_rule["bad_pattern"] == "仿佛过了一个世纪"
    assert any(r["id"] == new_rule["id"] for r in list_jinghua_rules())

    after_add = check_text_with_jinghua_lock("走廊里静得出奇，仿佛过了一个世纪，门才打开。")
    assert after_add["passed"] is False
    assert any(h["matched_text"] == "仿佛过了一个世纪" for h in after_add["hits"])


def test_5_originality_check_detects_copy_and_passes_original(isolated_kb: Path, tmp_path: Path) -> None:
    """测试 5：原创度查重（照抄原句检出报警，全新创作通过）。"""
    novel_file = tmp_path / "source_dushi.txt"
    copied_sentence = "陈默把那份盖着红章的股权转让书直接拍在会议室长桌正中央，抬手看了一眼腕表。"
    novel_file.write_text(
        f"第1章 董事会摊牌\n{copied_sentence}\n股东们顿时互相交换眼神。\n",
        encoding="utf-8",
    )
    import_novel_file(novel_file, genre_id="dushi")

    copy_res = check_script_originality(
        draft_text=f"推开门后，{copied_sentence}全场瞬间安静。",
        genre_id="dushi",
    )
    assert copy_res["passed"] is False
    assert copy_res["max_similarity"] >= 0.35
    assert copy_res["matches"]
    assert copy_res["matches"][0]["chapter_num"] == 1

    fresh_res = check_script_originality(
        draft_text="林晚拎着帆布包走进便利店，把两瓶热豆浆放到柜台上扫码结账，顺手拿了张纸巾擦掉眼镜片上的雾气。",
        genre_id="dushi",
    )
    assert fresh_res["passed"] is True
    assert fresh_res["max_similarity"] < 0.35


def test_6_interactive_branching_diagnostics_and_export(isolated_kb: Path) -> None:
    """测试 6：互动影游多支线创建、好感度数值配置、死胡同诊断与台本导出。"""
    proj = create_script_project(
        title="总裁的试镜夜",
        genre_id="hudong",
        project_mode="branching",
        synopsis="艺人面对资本潜规则与合同陷阱的抉择。",
        state_vars={"好感度": 10, "警惕值": 0},
        target_style_notes="台词口语短脆",
    )
    pid = proj["id"]

    n_start = upsert_story_node(
        project_id=pid,
        node_code="S1",
        title="开场试镜室",
        node_type="start",
        speaker_summary="沈安、制片人",
        script_text="制片人把空白补充协议推过来：“签了字，女一号归你。”",
        pos_x=100,
        pos_y=160,
    )
    n_dead = upsert_story_node(
        project_id=pid,
        node_code="S2",
        title="走廊暂避（未接结局）",
        node_type="scene",
        speaker_summary="沈安",
        script_text="沈安拿着协议走出试镜室，在楼梯口停住。",
        pos_x=380,
        pos_y=80,
    )
    n_good = upsert_story_node(
        project_id=pid,
        node_code="E1",
        title="好结局：保留证据反杀",
        node_type="good_ending",
        speaker_summary="沈安、法务",
        script_text="沈安亮出录音笔，直接把合同交给了赶来的法务总监。",
        pos_x=380,
        pos_y=260,
    )

    # First connect S1 -> S2 and S1 -> E1, leaving S2 as a non-ending dead end
    upsert_story_choice(
        project_id=pid,
        from_node_id=n_start["id"],
        to_node_id=n_dead["id"],
        choice_label="先借口接电话离开试镜室",
        choice_tag="缓兵之计",
        state_delta={"警惕值": 15},
        sort_order=1,
    )
    upsert_story_choice(
        project_id=pid,
        from_node_id=n_start["id"],
        to_node_id=n_good["id"],
        choice_label="当场按下录音键拒绝签字",
        choice_tag="爽点反杀",
        condition_expr="好感度>=10",
        state_delta={"好感度": 20, "警惕值": -5},
        sort_order=2,
    )

    graph_with_dead_end = get_project_graph(pid)
    diag1 = graph_with_dead_end["diagnostics"]
    assert diag1["is_healthy"] is False
    assert any(d["id"] == n_dead["id"] for d in diag1["dead_end_nodes"])

    # Edit S2 text to verify revision tracking, and connect S2 -> E1 to heal dead end
    upsert_story_node(
        project_id=pid,
        node_id=n_dead["id"],
        node_code="S2",
        title="走廊暂避并联系法务",
        node_type="scene",
        speaker_summary="沈安",
        script_text="沈安拿着协议走出试镜室，拨通了公司法务的内线电话。",
        revision_note="补全行动动机",
    )
    revs = list_node_revisions(n_dead["id"])
    assert len(revs) == 1
    assert "楼梯口停住" in revs[0]["old_text"]
    assert "法务的内线电话" in revs[0]["new_text"]

    upsert_story_choice(
        project_id=pid,
        from_node_id=n_dead["id"],
        to_node_id=n_good["id"],
        choice_label="把协议拍照发给法务",
        choice_tag="稳健破局",
        state_delta={"好感度": 10},
        sort_order=1,
    )

    healed_graph = get_project_graph(pid)
    assert healed_graph["diagnostics"]["is_healthy"] is True
    assert healed_graph["diagnostics"]["dead_end_nodes"] == []

    md = export_branching_script_markdown(pid)
    assert "总裁的试镜夜" in md
    assert "好感度" in md
    assert "当场按下录音键拒绝签字" in md
    export_files = list((get_data_dir() / "exports").glob(f"*{pid}.md"))
    assert len(export_files) == 1


def test_7_web_api_and_mcp_tools(isolated_kb: Path, tmp_path: Path) -> None:
    """测试 7：FastAPI TestClient 全核心接口返回 200 与静态首页可访问，且 MCP 12 个工具函数全部正常调用。"""
    # Part A: FastAPI TestClient
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/index.html").status_code == 200
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/dashboard").status_code == 200
        assert client.get("/api/genres").status_code == 200
        assert client.post(
            "/api/genres",
            json={"genre_id": "xuanhuan", "name": "东方玄幻", "channel_hint": "男频长篇"},
        ).status_code == 200

        sn_resp = client.post(
            "/api/snippets",
            json={
                "genre_id": "xuanhuan",
                "raw_text": "少年收起木剑：“师兄，这招你练了三年，我就看一遍。”",
                "scene_tag": "扮猪吃虎",
                "status": "approved",
            },
        )
        assert sn_resp.status_code == 200
        assert client.get("/api/snippets?genre_id=xuanhuan&status=approved").status_code == 200
        assert client.post(
            "/api/characters",
            json={"genre_id": "xuanhuan", "name": "陆小舟", "traits": "表面懒散实则记仇"},
        ).status_code == 200
        assert client.get("/api/characters?genre_id=xuanhuan").status_code == 200
        assert client.post(
            "/api/highlights",
            json={"genre_id": "xuanhuan", "plain_summary": "大比现场用断剑挑飞对方佩剑"},
        ).status_code == 200
        assert client.get("/api/highlights?genre_id=xuanhuan").status_code == 200

        assert client.get("/api/jinghua/rules").status_code == 200
        jh_check = client.post(
            "/api/jinghua/check",
            json={"text": "他脸色涨成猪肝色，瞬间卡壳。"},
        )
        assert jh_check.status_code == 200
        assert jh_check.json()["result"]["passed"] is False

        orig_check = client.post(
            "/api/originality/check",
            json={"draft_text": "全新原创句子完全不重样测试文本", "genre_id": "xuanhuan"},
        )
        assert orig_check.status_code == 200
        assert orig_check.json()["result"]["passed"] is True

        proj_resp = client.post(
            "/api/projects",
            json={"title": "Web测试剧", "genre_id": "hudong", "state_vars": {"好感度": 0}},
        )
        assert proj_resp.status_code == 200
        proj_id = proj_resp.json()["item"]["id"]

        n1 = client.post(
            f"/api/projects/{proj_id}/nodes",
            json={"node_code": "S1", "title": "开场", "node_type": "start", "script_text": "你好"},
        )
        n2 = client.post(
            f"/api/projects/{proj_id}/nodes",
            json={"node_code": "E1", "title": "结局", "node_type": "good_ending", "script_text": "再见"},
        )
        assert n1.status_code == 200 and n2.status_code == 200
        n1_id = n1.json()["item"]["id"]
        n2_id = n2.json()["item"]["id"]

        assert client.patch(f"/api/nodes/{n1_id}/position", json={"pos_x": 222, "pos_y": 333}).status_code == 200
        assert client.post(
            f"/api/projects/{proj_id}/choices",
            json={"from_node_id": n1_id, "to_node_id": n2_id, "choice_label": "直接开局"},
        ).status_code == 200
        assert client.get(f"/api/projects/{proj_id}/graph").status_code == 200
        assert client.get(f"/api/projects/{proj_id}/export").status_code == 200

    # Part B: All 12 MCP tool functions
    mcp_novel = tmp_path / "mcp_test_book.txt"
    mcp_novel.write_text(
        "\n".join(
            [
                "第1章 初遇",
                "顾昭把账本拍在桌上。",
                "“这笔账怎么算？”她问。",
                "“按规矩算，少一文钱都不行！”对方回道。",
                "第2章 反转",
                "小团子从门后探出头：“可是我刚才数过，箱子里全是石头呀！”",
                "全场立刻没了声响，几个人对视一眼。",
            ]
        ),
        encoding="utf-8",
    )

    ov = mcp_server.kb_overview_and_drawers()
    assert len(ov["genres"]) >= 7

    g_res = mcp_server.create_genre_drawer(
        genre_id="kuaichuan",
        name="快穿打脸",
        description="多世界切换打脸",
        channel_hint="女频快穿",
    )
    assert g_res["ok"] is True

    imp_res = mcp_server.import_novel(str(mcp_novel), genre_id="kuaichuan")
    assert imp_res["ok"] is True
    wk_id = imp_res["import_result"]["work_id"]

    span_res = mcp_server.read_novel_lines(work_id=wk_id, chapter_num=2, line_start=1, line_end=3)
    assert "箱子里全是石头" in span_res["text"]

    cand_res = mcp_server.get_taste_check_candidates(work_id=wk_id, genre_id="kuaichuan", limit=10)
    if cand_res["candidates"]:
        first_id = cand_res["candidates"][0]["id"]
        rev_res = mcp_server.submit_taste_reviews(
            json.dumps([{"snippet_id": first_id, "status": "✓", "rating": 5}], ensure_ascii=False)
        )
        assert rev_res["approved_count"] == 1

    craft_res = mcp_server.save_craft_items(
        genre_id="kuaichuan",
        work_id=wk_id,
        dialogues_json=json.dumps(
            [{"raw_text": "“这账本是假的，真账本在我鞋垫底下。”", "chapter_num": 1, "line_start": 2, "line_end": 2, "scene_tag": "藏账反杀"}],
            ensure_ascii=False,
        ),
        characters_json=json.dumps(
            [{"name": "顾昭", "traits": "表面温顺实则精明", "speech_style": "短句算账"}],
            ensure_ascii=False,
        ),
        highlights_json=json.dumps(
            [{"plain_summary": "众目睽睽下从鞋底抽出真账本", "chapter_range": "第1-2章", "hook_type": "反转打脸"}],
            ensure_ascii=False,
        ),
    )
    assert craft_res["ok"] is True
    assert craft_res["saved_counts"] == {"dialogues": 1, "characters": 1, "highlights": 1}

    fuel = mcp_server.draw_writing_fuel(genre_id="kuaichuan", scene_query="账本")
    assert len(fuel["approved_dialogues"]) >= 1
    assert len(fuel["character_voice_cards"]) >= 1
    assert len(fuel["jinghua_lock_blacklist"]) >= 20

    lock_res = mcp_server.run_jinghua_lock("他嘴角抽搐，空气突然安静。")
    assert lock_res["passed"] is False

    add_lock_res = mcp_server.add_to_jinghua_lock(
        bad_pattern="嘴角扯出一抹邪笑",
        category="AI油腻旁白",
        reason="霸总油腻套话",
        good_replacement_example="他笑了笑，没说话",
    )
    assert add_lock_res["ok"] is True

    orig_res = mcp_server.check_draft_originality("全新手写桥段不重样测试内容", genre_id="kuaichuan")
    assert orig_res["passed"] is True

    batch_res = mcp_server.manage_interactive_drama(
        action="batch_save",
        payload_json=json.dumps(
            {
                "title": "MCP批量建图测试",
                "state_vars": {"好感度": 0},
                "nodes": [
                    {"node_code": "S1", "title": "开场", "node_type": "start", "script_text": "开场白"},
                    {"node_code": "E1", "title": "结局", "node_type": "good_ending", "script_text": "收尾"},
                ],
                "choices": [
                    {"from_node_code": "S1", "to_node_code": "E1", "choice_label": "前往结局", "state_delta": {"好感度": 5}},
                ],
            },
            ensure_ascii=False,
        ),
    )
    assert batch_res["ok"] is True
    assert batch_res["diagnostics"]["is_healthy"] is True

    exp_res = mcp_server.manage_interactive_drama(
        action="export_markdown",
        payload_json=json.dumps({"project_id": batch_res["project"]["id"]}),
    )
    assert exp_res["ok"] is True
    assert "MCP批量建图测试" in exp_res["markdown"]
