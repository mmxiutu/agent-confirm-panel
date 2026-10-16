"""Shared payload helpers for the panel server, hook, and ask tool."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

TITLE_LIMIT = 40
UNKNOWN_TITLE = "未识别的聊天"
MAX_QUESTIONS = 6
MAX_OPTIONS = 8
MAX_TRANSCRIPT_BYTES = 200_000
USER_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)
SOURCE_KINDS = {"chat", "subagent", "background"}


def short_title(text: str) -> str:
    folded = " ".join(text.split())
    if len(folded) <= TITLE_LIMIT:
        return folded
    return folded[:TITLE_LIMIT] + "…"


def sanitize_questions(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    found: list[dict] = []
    used: set[str] = set()
    for index, item in enumerate(raw):
        if len(found) >= MAX_QUESTIONS or not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt") or "").strip()
        if prompt == "":
            continue
        question_id = str(item.get("id") or "").strip() or f"q{index + 1}"
        if question_id in used:
            question_id = f"{question_id}-{index + 1}"
        used.add(question_id)
        options: list[dict] = []
        option_ids: set[str] = set()
        raw_options = item.get("options")
        if isinstance(raw_options, list):
            for option_index, option in enumerate(raw_options):
                if len(options) >= MAX_OPTIONS or not isinstance(option, dict):
                    continue
                label = str(option.get("label") or "").strip()
                if label == "":
                    continue
                option_id = str(option.get("id") or "").strip() or f"o{option_index + 1}"
                if option_id in option_ids:
                    option_id = f"{option_id}-{option_index + 1}"
                option_ids.add(option_id)
                options.append({"id": option_id[:80], "label": label[:200]})
        if not options:
            continue
        found.append(
            {
                "id": question_id[:80],
                "prompt": prompt[:500],
                "allow_multiple": bool(item.get("allow_multiple") or False),
                "options": options,
            }
        )
    return found


def question_key(questions: list[dict]) -> str:
    raw = json.dumps(questions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def project_name(workspace_roots: object, cwd: str = "") -> str:
    if isinstance(workspace_roots, list):
        for root in workspace_roots:
            text = str(root or "").strip()
            if text:
                return Path(text).name[:80]
    if cwd.strip():
        return Path(cwd.strip()).name[:80]
    return ""


def title_from_transcript(path: str) -> str:
    if path.strip() == "":
        return ""
    file_path = Path(path)
    try:
        if not file_path.is_file():
            return ""
    except OSError:
        return ""
    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as handle:
            consumed = 0
            for line in handle:
                consumed += len(line)
                if consumed > MAX_TRANSCRIPT_BYTES:
                    break
                text = _title_from_line(line)
                if text:
                    return short_title(text)
    except OSError:
        return ""
    return ""


def _title_from_line(line: str) -> str:
    stripped = line.strip()
    if stripped == "":
        return ""
    try:
        item = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    if not isinstance(item, dict):
        return ""
    role = item.get("role")
    if role not in (None, "user"):
        return ""
    return _text_from_item(item)


def _text_from_item(item: dict) -> str:
    message = item.get("message") if isinstance(item.get("message"), dict) else item
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    chunks: list[str] = []
    if isinstance(content, str):
        chunks.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict) and part.get("type") in (None, "text"):
                chunks.append(str(part.get("text") or ""))
    raw = "\n".join(chunks).strip()
    if raw == "":
        return ""
    matched = USER_QUERY.search(raw)
    if matched:
        return matched.group(1).strip()
    return raw


def public_source(source: dict) -> dict:
    kind = str(source.get("kind") or "chat")
    if kind not in SOURCE_KINDS:
        kind = "chat"
    return {
        "conversation_id": str(source.get("conversation_id") or "")[:80],
        "title": str(source.get("title") or "")[:TITLE_LIMIT + 1],
        "project": str(source.get("project") or "")[:80],
        "kind": kind,
    }


def normalize_answers(questions: list[dict], raw: object) -> list[dict] | None:
    if not isinstance(raw, list):
        return None
    by_id = {question["id"]: question for question in questions}
    if len(raw) != len(by_id):
        return None
    incoming: dict[str, dict] = {}
    for item in raw:
        if not isinstance(item, dict):
            return None
        question_id = str(item.get("id") or "")
        question = by_id.get(question_id)
        if question is None or question_id in incoming:
            return None
        allowed = {option["id"] for option in question["options"]}
        option_ids = item.get("option_ids")
        cleaned: list[str] = []
        if isinstance(option_ids, list):
            for option_id in option_ids:
                text = str(option_id)
                if text not in allowed or text in cleaned:
                    return None
                cleaned.append(text)
        if not question["allow_multiple"] and len(cleaned) > 1:
            return None
        custom = str(item.get("custom") or "").strip()
        if not cleaned and custom == "":
            return None
        incoming[question_id] = {"id": question_id, "option_ids": cleaned, "custom": custom[:500]}
    if set(incoming) != set(by_id):
        return None
    return [incoming[question["id"]] for question in questions]


def present_answers(questions: list[dict], answers: list[dict]) -> list[dict]:
    labels = {
        question["id"]: {option["id"]: option["label"] for option in question["options"]}
        for question in questions
    }
    presented: list[dict] = []
    for answer in answers:
        question_id = answer["id"]
        names = labels.get(question_id, {})
        presented.append(
            {
                "id": question_id,
                "option_ids": answer["option_ids"],
                "labels": [names.get(option_id, option_id) for option_id in answer["option_ids"]],
                "custom": answer["custom"],
            }
        )
    return presented


def coerce_answers(raw: object) -> object:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw
