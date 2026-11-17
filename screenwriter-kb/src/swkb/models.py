"""Data models, constants, and SQLite row/JSON serialization helpers for swkb."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

SNIPPET_STATUSES: tuple[str, ...] = ("pending", "approved", "rejected")
PROJECT_MODES: tuple[str, ...] = ("branching", "short_drama")
NODE_TYPES: tuple[str, ...] = (
    "start",
    "scene",
    "climax",
    "good_ending",
    "bad_ending",
    "hidden_ending",
)
ENDING_NODE_TYPES: tuple[str, ...] = (
    "good_ending",
    "bad_ending",
    "hidden_ending",
)


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format (seconds precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    """Generate a compact unique ID with a readable domain prefix."""
    clean_prefix = prefix.strip("_") or "item"
    return f"{clean_prefix}_{uuid4().hex[:12]}"


def parse_json_field(raw: Any, default: Any = None) -> Any:
    """Safely parse a JSON column value into a Python object."""
    fallback = {} if default is None else (list(default) if isinstance(default, list) else dict(default) if isinstance(default, dict) else default)
    if raw is None or raw == "":
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return fallback
        if parsed is None:
            return fallback
        return parsed
    return fallback


def dump_json_field(value: Any, default: Any = None) -> str:
    """Serialize a Python dict/list (or JSON string) into a clean UTF-8 JSON string."""
    if value is None:
        target = {} if default is None else default
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            target = {} if default is None else default
        else:
            try:
                target = json.loads(stripped)
            except (json.JSONDecodeError, TypeError, ValueError):
                target = [value] if isinstance(default, list) else value
    else:
        target = value
    return json.dumps(target, ensure_ascii=False)


def row_to_dict(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Convert a sqlite3.Row or mapping to a plain dict, returning None if input is None."""
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    return {key: row[key] for key in row.keys()} if isinstance(row, sqlite3.Row) else dict(row)


def rows_to_dicts(rows: Iterable[sqlite3.Row | Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert an iterable of sqlite3.Row objects into a list of plain dicts."""
    result: list[dict[str, Any]] = []
    for row in rows:
        converted = row_to_dict(row)
        if converted is not None:
            result.append(converted)
    return result


def serialize_genre(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a genre row (including optional aggregate counts)."""
    data = row_to_dict(row)
    if data is None:
        return None
    for count_key in ("work_count", "approved_dialogue_count", "pending_dialogue_count", "character_count", "highlight_count"):
        if count_key in data and data[count_key] is not None:
            data[count_key] = int(data[count_key])
    return data


def serialize_work(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a work row into a clean dict."""
    data = row_to_dict(row)
    if data is None:
        return None
    for int_key in ("total_chars", "total_chapters", "clean_chapters", "corrupted_chapters"):
        if int_key in data and data[int_key] is not None:
            data[int_key] = int(data[int_key])
    return data


def serialize_chapter(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a chapter row into a clean dict."""
    data = row_to_dict(row)
    if data is None:
        return None
    if "is_corrupted" in data:
        data["is_corrupted"] = bool(data["is_corrupted"])
    for int_key in ("id", "chapter_num", "line_start", "line_end", "char_start", "char_end", "char_count"):
        if int_key in data and data[int_key] is not None:
            data[int_key] = int(data[int_key])
    return data


def serialize_character(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a character row and parse sample_lines_json -> sample_lines."""
    data = row_to_dict(row)
    if data is None:
        return None
    raw_lines = data.pop("sample_lines_json", None)
    parsed_lines = parse_json_field(raw_lines, default=[])
    if not isinstance(parsed_lines, list):
        parsed_lines = [str(parsed_lines)] if parsed_lines else []
    data["sample_lines"] = parsed_lines
    return data


def serialize_dialogue_snippet(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a dialogue_snippet row into a clean dict."""
    data = row_to_dict(row)
    if data is None:
        return None
    if "rating" in data and data["rating"] is not None:
        data["rating"] = int(data["rating"])
    for int_key in ("chapter_num", "line_start", "line_end"):
        if int_key in data and data[int_key] is not None:
            data[int_key] = int(data[int_key])
    return data


def serialize_scene_highlight(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a scene_highlight row into a clean dict."""
    data = row_to_dict(row)
    if data is None:
        return None
    for int_key in ("line_start", "line_end"):
        if int_key in data and data[int_key] is not None:
            data[int_key] = int(data[int_key])
    return data


def serialize_script_project(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a script_project row and parse state_vars_json -> state_vars."""
    data = row_to_dict(row)
    if data is None:
        return None
    raw_vars = data.pop("state_vars_json", None)
    parsed_vars = parse_json_field(raw_vars, default={})
    if not isinstance(parsed_vars, dict):
        parsed_vars = {}
    data["state_vars"] = parsed_vars
    return data


def serialize_story_node(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a story_node row into a clean dict."""
    data = row_to_dict(row)
    if data is None:
        return None
    for int_key in ("episode_num", "jinghua_score", "user_rating"):
        if int_key in data and data[int_key] is not None:
            data[int_key] = int(data[int_key])
    for float_key in ("pos_x", "pos_y"):
        if float_key in data and data[float_key] is not None:
            data[float_key] = float(data[float_key])
    return data


def serialize_story_choice(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a story_choice row and parse state_delta_json -> state_delta."""
    data = row_to_dict(row)
    if data is None:
        return None
    raw_delta = data.pop("state_delta_json", None)
    parsed_delta = parse_json_field(raw_delta, default={})
    if not isinstance(parsed_delta, dict):
        parsed_delta = {}
    data["state_delta"] = parsed_delta
    if "sort_order" in data and data["sort_order"] is not None:
        data["sort_order"] = int(data["sort_order"])
    return data


def serialize_script_revision(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a script_revision row into a clean dict."""
    data = row_to_dict(row)
    if data is None:
        return None
    if "id" in data and data["id"] is not None:
        data["id"] = int(data["id"])
    if "user_rating" in data and data["user_rating"] is not None:
        data["user_rating"] = int(data["user_rating"])
    return data


@dataclass(slots=True)
class GenreModel:
    id: str
    name: str
    description: str = ""
    channel_hint: str = ""
    created_at: str = ""
    work_count: int = 0
    approved_dialogue_count: int = 0
    character_count: int = 0


@dataclass(slots=True)
class CharacterModel:
    id: str
    name: str
    traits: str
    work_id: str | None = None
    genre_id: str = "mengwa"
    role_type: str = ""
    speech_style: str = ""
    catchphrases: str = ""
    forbidden_words: str = ""
    sample_lines: list[str] = field(default_factory=list)
    created_at: str = ""


@dataclass(slots=True)
class ScriptProjectModel:
    id: str
    title: str
    genre_id: str = "hudong"
    project_mode: str = "branching"
    synopsis: str = ""
    state_vars: dict[str, Any] = field(default_factory=dict)
    target_style_notes: str = ""
    created_at: str = ""
    updated_at: str = ""
