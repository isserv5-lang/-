from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_PROJECT_ROOT = Path(r"D:\Antigravity\zahuo-repo\screenwriter-kb")
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from swkb.config import get_data_dir, get_db_path  # noqa: E402
from swkb.db import get_conn, init_db  # noqa: E402

SERVER_NAME = "screenwriter-kb"
DEFAULT_DATA_DIR = Path(r"D:\ScreenwriterKB")


def resolve_project_root() -> Path:
    """Resolve the active D-drive project root and refuse legacy C-drive paths."""
    resolved = PROJECT_ROOT.resolve()
    norm_lower = resolved.as_posix().lower()
    if norm_lower.startswith("c:/users/visser/downloads/zahuo"):
        if CANONICAL_PROJECT_ROOT.exists():
            return CANONICAL_PROJECT_ROOT.resolve()
        raise RuntimeError(
            f"禁止从旧 C 盘路径配置 MCP：{resolved}，请从 {CANONICAL_PROJECT_ROOT} 运行本脚本。"
        )
    return resolved


def _configure_stdio() -> None:
    """Ensure UTF-8 output on Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def find_uv_executable() -> Path:
    """Locate the absolute path to uv.exe."""
    found = shutil.which("uv")
    if found:
        return Path(found).resolve()
    candidates = [
        Path.home() / ".local" / "bin" / "uv.exe",
        Path.home() / ".cargo" / "bin" / "uv.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError("未找到 uv 可执行文件，请先安装 uv 或将其加入 PATH。")


def backup_file(path: Path) -> Path | None:
    """Create a .bak backup alongside an existing config file before modifying it."""
    if not path.exists():
        return None
    backup_path = path.with_name(f"{path.name}.bak")
    shutil.copy2(path, backup_path)
    return backup_path


def _build_mcp_args(project_root: Path) -> list[str]:
    return [
        "run",
        "--python",
        "3.12",
        "--directory",
        project_root.as_posix(),
        "python",
        "-m",
        "swkb.mcp_server",
    ]


def _toml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _build_mcp_env(data_dir: Path) -> dict[str, str]:
    resolved = data_dir.resolve()
    return {
        "SWKB_DATA_DIR": resolved.as_posix(),
        "UV_CACHE_DIR": (resolved / ".uv-cache").as_posix(),
        "TEMP": (resolved / ".tmp").as_posix(),
    }


def _render_codex_mcp_block(uv_exe: Path, project_root: Path, data_dir: Path) -> str:
    args_toml = ", ".join(_toml_quote(a) for a in _build_mcp_args(project_root))
    env_map = _build_mcp_env(data_dir)
    env_toml = ", ".join(f"{k} = {_toml_quote(v)}" for k, v in env_map.items())
    return (
        f"[mcp_servers.{SERVER_NAME}]\n"
        f"command = {_toml_quote(uv_exe.as_posix())}\n"
        f"args = [{args_toml}]\n"
        f"env = {{ {env_toml} }}\n"
    )


_HEADER_RE = re.compile(r"^\s*\[")
_TARGET_SECTION_RE = re.compile(
    r"^\s*\[\s*mcp_servers\.(?:screenwriter-kb|\"screenwriter-kb\"|'screenwriter-kb')\s*\]\s*(?:#.*)?$"
)
_TARGET_SUBSECTION_RE = re.compile(
    r"^\s*\[\s*mcp_servers\.(?:screenwriter-kb|\"screenwriter-kb\"|'screenwriter-kb')\."
)


def update_codex_config(
    config_path: Path,
    uv_exe: Path,
    project_root: Path,
    data_dir: Path,
) -> dict[str, Any]:
    """Idempotently register screenwriter-kb in Codex config.toml without touching other keys."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    orig_text = ""
    orig_parsed: dict[str, Any] = {}
    if config_path.exists():
        orig_text = config_path.read_text(encoding="utf-8-sig")
        if orig_text.strip():
            orig_parsed = tomllib.loads(orig_text)

    block = _render_codex_mcp_block(uv_exe, project_root, data_dir)
    lines = orig_text.splitlines(keepends=True)
    out_lines: list[str] = []
    i = 0
    replaced = False

    while i < len(lines):
        line = lines[i]
        if _TARGET_SECTION_RE.match(line):
            if not replaced:
                out_lines.append(block)
                replaced = True
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if _HEADER_RE.match(nxt) and not _TARGET_SUBSECTION_RE.match(nxt):
                    break
                i += 1
            continue
        out_lines.append(line)
        i += 1

    if not replaced:
        if out_lines and not out_lines[-1].endswith("\n"):
            out_lines.append("\n")
        if out_lines and out_lines[-1].strip():
            out_lines.append("\n")
        out_lines.append(block)

    new_text = "".join(out_lines)
    new_parsed = tomllib.loads(new_text)

    for key in orig_parsed:
        if key not in new_parsed:
            raise RuntimeError(f"Codex 配置校验失败：原有字段 {key} 丢失")
    for k, v in orig_parsed.get("mcp_servers", {}).items():
        if k != SERVER_NAME and new_parsed.get("mcp_servers", {}).get(k) != v:
            raise RuntimeError(f"Codex 配置校验失败：原有 mcp_servers.{k} 发生意外变动")

    backup = backup_file(config_path)
    config_path.write_bytes(new_text.encode("utf-8"))
    return {
        "client": "codex",
        "path": str(config_path),
        "backup": str(backup) if backup else None,
        "status": "updated" if replaced else "added",
    }


def update_json_mcp_config(
    client_name: str,
    config_path: Path,
    uv_exe: Path,
    project_root: Path,
    data_dir: Path,
) -> dict[str, Any]:
    """Idempotently register screenwriter-kb in a JSON mcpServers config file."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if config_path.exists():
        raw = config_path.read_text(encoding="utf-8-sig")
        if raw.strip():
            loaded = json.loads(raw)
            if not isinstance(loaded, dict):
                raise RuntimeError(f"{client_name} 配置文件根节点不是 JSON 对象：{config_path}")
            data = loaded

    orig_keys = set(data.keys())
    mcp_servers = data.get("mcpServers")
    if mcp_servers is None:
        mcp_servers = {}
        data["mcpServers"] = mcp_servers
    if not isinstance(mcp_servers, dict):
        raise RuntimeError(f"{client_name} 配置中的 mcpServers 不是对象：{config_path}")

    orig_other_servers = {k: v for k, v in mcp_servers.items() if k != SERVER_NAME}
    existed = SERVER_NAME in mcp_servers

    mcp_servers[SERVER_NAME] = {
        "command": uv_exe.as_posix(),
        "args": _build_mcp_args(project_root),
        "env": _build_mcp_env(data_dir),
    }

    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    verified = json.loads(serialized)
    for k in orig_keys:
        if k not in verified:
            raise RuntimeError(f"{client_name} 配置校验失败：原有字段 {k} 丢失")
    for k, v in orig_other_servers.items():
        if verified["mcpServers"].get(k) != v:
            raise RuntimeError(f"{client_name} 配置校验失败：原有 mcpServers.{k} 发生意外变动")

    backup = backup_file(config_path)
    config_path.write_bytes(serialized.encode("utf-8"))
    return {
        "client": client_name,
        "path": str(config_path),
        "backup": str(backup) if backup else None,
        "status": "updated" if existed else "added",
    }


def write_data_dir_launcher(data_dir: Path, uv_exe: Path, project_root: Path) -> Path:
    """Create D:\\ScreenwriterKB\\打开可视化知识库.cmd with CRLF and no UTF-8 BOM."""
    launcher = data_dir / "打开可视化知识库.cmd"
    uv_cache_dir = data_dir / ".uv-cache"
    tmp_dir = data_dir / ".tmp"
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "title 编剧本地知识库 & 互动影游工作台",
        f'set "SWKB_DATA_DIR={data_dir}"',
        f'set "UV_CACHE_DIR={uv_cache_dir}"',
        f'set "TEMP={tmp_dir}"',
        f'set "TMP={tmp_dir}"',
        'if not exist "%UV_CACHE_DIR%" mkdir "%UV_CACHE_DIR%"',
        'if not exist "%TEMP%" mkdir "%TEMP%"',
        f'cd /d "{project_root}"',
        "echo 正在启动本地可视化知识库工作台 (http://127.0.0.1:8765) ...",
        "echo 浏览器将自动打开；关闭本窗口即可停止服务。",
        f'"{uv_exe}" run --python 3.12 swkb web --host 127.0.0.1 --port 8765',
        "if errorlevel 1 (",
        "    echo.",
        "    echo 启动失败，请检查上方错误信息。",
        "    pause",
        ")",
    ]
    payload = ("\r\n".join(lines) + "\r\n").encode("utf-8")
    launcher.write_bytes(payload)
    return launcher


def sync_skill_to_globals(project_root: Path) -> list[str]:
    """Sync skills/novel-craft-feeder to global Antigravity and Codex skill directories."""
    src_dir = project_root / "skills" / "novel-craft-feeder"
    src_skill = src_dir / "SKILL.md"
    if not src_skill.exists():
        raise FileNotFoundError(f"未找到源 Skill 文件：{src_skill}")

    targets = [
        Path.home()
        / ".gemini"
        / "config"
        / "plugins"
        / "project-capsule"
        / "skills"
        / "novel-craft-feeder",
        Path.home() / ".codex" / "skills" / "novel-craft-feeder",
    ]
    installed: list[str] = []
    for target_dir in targets:
        target_dir.mkdir(parents=True, exist_ok=True)
        for item in src_dir.iterdir():
            if item.is_file():
                shutil.copy2(item, target_dir / item.name)
        installed.append(str(target_dir / "SKILL.md"))
    return installed


def init_data_vault(data_dir: Path) -> Path:
    """Initialize the local data directory and SQLite schema with preset drawers & rules."""
    os.environ["SWKB_DATA_DIR"] = str(data_dir)
    resolved_dir = get_data_dir()
    (resolved_dir / ".uv-cache").mkdir(parents=True, exist_ok=True)
    (resolved_dir / ".tmp").mkdir(parents=True, exist_ok=True)
    db_path = get_db_path()
    conn = get_conn(db_path)
    try:
        init_db(conn)
    finally:
        conn.close()
    return resolved_dir


def main() -> int:
    _configure_stdio()
    os.environ.setdefault("SWKB_DATA_DIR", str(DEFAULT_DATA_DIR))
    data_dir = Path(os.environ["SWKB_DATA_DIR"])
    project_root = resolve_project_root()
    uv_exe = find_uv_executable()

    resolved_dir = init_data_vault(data_dir)
    launcher = write_data_dir_launcher(resolved_dir, uv_exe, project_root)

    codex_cfg = Path.home() / ".codex" / "config.toml"
    appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    claude_cfg = appdata / "Claude" / "claude_desktop_config.json"
    agy_cfg = Path.home() / ".gemini" / "config" / "mcp_config.json"

    results = [
        update_codex_config(codex_cfg, uv_exe, project_root, resolved_dir),
        update_json_mcp_config("claude-desktop", claude_cfg, uv_exe, project_root, resolved_dir),
        update_json_mcp_config("antigravity", agy_cfg, uv_exe, project_root, resolved_dir),
    ]
    synced_skills = sync_skill_to_globals(project_root)

    print(f"[OK] D 盘项目根目录：{project_root}")
    print(f"[OK] 数据保险箱已初始化：{resolved_dir}")
    print(f"[OK] 一键启动脚本已生成：{launcher}")
    for item in results:
        bak_note = f"（已备份 -> {item['backup']}）" if item["backup"] else ""
        print(f"[OK] {item['client']} MCP 配置已{item['status']}：{item['path']}{bak_note}")
    for sk in synced_skills:
        print(f"[OK] Skill 已同步：{sk}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
