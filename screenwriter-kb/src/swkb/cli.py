from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from swkb.config import get_data_dir, get_db_path
from swkb.db import get_conn, init_db
from swkb.ingest import import_novel_file
from swkb.jinghua_lock import check_text_with_jinghua_lock
from swkb.repository import get_dashboard_stats, list_genres


def _configure_stdio() -> None:
    """Ensure UTF-8 output on Windows consoles when printing Chinese text."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _ensure_db() -> Path:
    """Initialize the SQLite database if it has not been created yet."""
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


def _cmd_mcp(_: argparse.Namespace) -> int:
    from swkb.mcp_server import main as mcp_main

    mcp_main()
    return 0


def _cmd_web(args: argparse.Namespace) -> int:
    from swkb.web_api import run_server

    run_server(
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
    )
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    _ensure_db()
    result = import_novel_file(
        file_path=args.path,
        genre_id=args.genre,
        title=args.title or None,
        author=args.author,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_lock_check(args: argparse.Namespace) -> int:
    _ensure_db()
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8-sig")
    else:
        text = " ".join(args.text).strip()
    if not text:
        print("请提供需要体检的文本，或使用 --file 指定文本文件。", file=sys.stderr)
        return 2

    result = check_text_with_jinghua_lock(text)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("passed", False) else 1

    passed = result.get("passed", False)
    score = result.get("score", 0)
    hits = result.get("hits", [])
    print(f"精话锁体检结果：{'通过' if passed else '拦截'} | 健康分：{score}/100 | 命中 {len(hits)} 处")
    for idx, hit in enumerate(hits, 1):
        line_no = hit.get("line_num", hit.get("line_no", "?"))
        pattern = hit.get("matched_text") or hit.get("bad_pattern", "")
        reason = hit.get("reason", "")
        example = hit.get("good_replacement_example", "")
        print(f"  {idx}. [第{line_no}行] 命中「{pattern}」")
        if reason:
            print(f"     原因：{reason}")
        if example:
            print(f"     改写示范：{example}")
    guide = result.get("rewrite_guide", "")
    if guide:
        print(f"\n改写指引：{guide}")
    return 0 if passed else 1


def _cmd_stats(args: argparse.Namespace) -> int:
    _ensure_db()
    stats = get_dashboard_stats()
    genres = list_genres()
    payload = {
        "data_dir": str(get_data_dir()),
        "db_path": str(get_db_path()),
        "stats": stats,
        "genres": [
            {"id": g.get("id"), "name": g.get("name"), "channel_hint": g.get("channel_hint")}
            for g in genres
        ],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"数据目录：{payload['data_dir']}")
    print(f"数据库：{payload['db_path']}")
    print("统计概览：")
    for k, v in stats.items():
        print(f"  - {k}: {v}")
    print("类型抽屉：")
    for g in genres:
        hint = f"（{g['channel_hint']}）" if g.get("channel_hint") else ""
        print(f"  - [{g.get('id')}] {g.get('name')}{hint}")
    return 0


def _cmd_init(_: argparse.Namespace) -> int:
    db_path = get_db_path()
    conn = get_conn(db_path)
    try:
        init_db(conn)
    finally:
        conn.close()
    print(f"已初始化编剧本地知识库：{db_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the swkb CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="swkb",
        description="编剧本地知识库、精话锁去油与互动影游多支线工具箱",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_mcp = sub.add_parser("mcp", help="启动标准 stdio MCP 服务（供 Codex / Claude / Antigravity 调用）")
    p_mcp.set_defaults(func=_cmd_mcp)

    p_web = sub.add_parser("web", help="启动本地可视化知识库与互动影游网页工作台")
    p_web.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    p_web.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    p_web.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    p_web.set_defaults(func=_cmd_web)

    p_import = sub.add_parser("import", help="导入本地小说 (.txt / .docx) 到指定类型抽屉")
    p_import.add_argument("path", help="小说文件路径")
    p_import.add_argument("--genre", default="mengwa", help="类型抽屉 ID（默认 mengwa）")
    p_import.add_argument("--title", default="", help="书名（留空则自动用文件名）")
    p_import.add_argument("--author", default="", help="作者名（可选）")
    p_import.set_defaults(func=_cmd_import)

    p_lock = sub.add_parser("lock-check", help="对文本运行「精话锁」去油体检")
    p_lock.add_argument("text", nargs="*", help="待体检的正文片段")
    p_lock.add_argument("--file", default="", help="从文件读取待体检文本")
    p_lock.add_argument("--json", action="store_true", help="以 JSON 格式输出结果")
    p_lock.set_defaults(func=_cmd_lock_check)

    p_stats = sub.add_parser("stats", help="查看当前知识库统计与所有类型抽屉")
    p_stats.add_argument("--json", action="store_true", help="以 JSON 格式输出结果")
    p_stats.set_defaults(func=_cmd_stats)

    p_init = sub.add_parser("init", help="初始化默认数据目录与 SQLite 数据库")
    p_init.set_defaults(func=_cmd_init)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for `swkb`."""
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
