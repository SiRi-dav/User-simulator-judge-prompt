"""Run the repository's judge prompts over local conversations.

Python standard library only. Invoke with: python3 -m eval_pipeline.runner --help
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any
from urllib import error, request


ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATHS = {
    "case_information_evidence": ROOT / "prompts/case_information_evidence.md",
    "natural_flow_turn": ROOT / "prompts/natural_flow_turn.md",
    "natural_flow_dialogue": ROOT / "prompts/natural_flow_dialogue.md",
    "information_leakage_dialogue": ROOT / "prompts/information_leakage_dialogue.md",
    "expression_realism_dialogue": ROOT / "prompts/expression_realism_dialogue.md",
    "expression_feature_annotation": ROOT / "prompts/expression_feature_annotation.md",
    "expression_realism_batch": ROOT / "prompts/expression_realism_batch.md",
}
PLACEHOLDERS = {
    "case_information_evidence": ["{{CASE_TEXT}}"],
    "natural_flow_turn": ["{{DIALOGUE_TEXT}}"],
    "natural_flow_dialogue": ["{{DIALOGUE_TEXT}}"],
    "information_leakage_dialogue": ["{{CASE_TEXT}}", "{{CASE_EVIDENCE}}", "{{DIALOGUE}}"],
    "expression_realism_dialogue": ["{{INPUT_MARKDOWN}}"],
    "expression_feature_annotation": ["{{INPUT_MARKDOWN}}"],
    "expression_realism_batch": ["{{INPUT_MARKDOWN}}"],
}
MESSAGE_RE = re.compile(r"^[ \t]{0,3}(?:\*\*)?[ \t]*(User|Assistant)[ \t]*:[ \t]*(?:\*\*)?[ \t]*", re.I)
TITLE_RE = re.compile(r"^[ \t]{0,3}##[ \t]+(.+?)[ \t]*$")
FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
ID_RE = re.compile(r"^(.+?)#(\d+)$")
SUPPORTED = {".md", ".txt", ".json", ".jsonl"}
FEATURE_LABELS = {
    "emotion": {"无明显情绪", "困惑", "担忧", "烦躁／不满", "满意／欣喜", "其他"},
    "formality": {"随意", "中性", "正式", "混合"},
    "organization": {"连续叙述", "分条", "混合"},
    "politeness": {"无", "感谢", "致歉", "客气请求", "其他"},
    "restatement": {"无", "复述助手", "重复已有背景", "总结进展"},
}
FEEDBACK_LABELS = {"无反馈", "明确肯定", "明确否定", "要求重做", "请求继续", "请求澄清", "提供澄清"}
TASK_LABELS = {"描述问题", "回答问题", "简单确认", "反馈结果", "追问求助", "澄清纠正", "质疑拒绝", "收束安排", "其他"}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_api_config(path: Path, explicit: bool = False) -> dict[str, str]:
    if not path.exists():
        if explicit:
            raise ValueError(f"API 配置文件不存在：{path}")
        return {}
    config = read_json(path)
    if not isinstance(config, dict) or any(key not in {"base_url", "model", "api_key"} or not isinstance(value, str) for key, value in config.items()):
        raise ValueError("API 配置仅支持字符串字段 base_url、model、api_key")
    return config


def read_run_config(path: Path, explicit: bool = False) -> dict[str, Any]:
    if not path.exists():
        if explicit:
            raise ValueError(f"运行路径配置不存在：{path}")
        return {}
    config = read_json(path)
    path_keys = {"dialogues", "cases", "real_dialogues", "output"}
    allowed = path_keys | {"start_dialogue", "limit_dialogues"}
    if not isinstance(config, dict) or any(key not in allowed for key in config):
        raise ValueError("运行路径配置含无效字段")
    result: dict[str, Any] = {}
    for key in path_keys & config.keys():
        value = config[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"运行路径配置 {key} 必须是非空路径字符串")
        result[key] = Path(value) if Path(value).is_absolute() else ROOT / value
    for key in ("start_dialogue", "limit_dialogues"):
        if key in config:
            value = config[key]
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"运行路径配置 {key} 必须是正整数或 null")
            result[key] = value
    return result


def list_files(source: Path) -> list[Path]:
    if source.is_file():
        if source.suffix.lower() not in SUPPORTED:
            raise ValueError(f"不支持的输入文件类型：{source}")
        return [source]
    if not source.is_dir():
        raise ValueError(f"输入文件或目录不存在：{source}")
    return sorted(p for p in source.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED)


def json_records(path: Path) -> list[Any]:
    raw = path.read_text(encoding="utf-8-sig").strip()
    if not raw:
        return []
    if path.suffix.lower() == ".json":
        obj = json.loads(raw)
    else:
        try:
            obj = json.loads(raw)  # Existing *.jsonl may actually be a JSON array.
        except json.JSONDecodeError:
            obj = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return obj if isinstance(obj, list) else [obj]


def iter_real_records(path: Path):
    """Stream large top-level JSON arrays or JSONL without loading every dialogue."""
    with path.open("r", encoding="utf-8-sig") as file:
        first = file.read(1)
        while first and first.isspace():
            first = file.read(1)
        if first == "[":
            decoder = json.JSONDecoder()
            buffer = ""
            expect_value = True
            seen = 0
            while True:
                buffer = buffer.lstrip()
                if not buffer:
                    chunk = file.read(65536)
                    if not chunk:
                        raise ValueError(f"真人对话 JSON 数组未闭合：{path}")
                    buffer += chunk
                    continue
                if buffer[0] == "]":
                    if expect_value and seen:
                        raise ValueError(f"真人对话 JSON 数组末尾多余逗号：{path}")
                    if (buffer[1:] + file.read()).strip():
                        raise ValueError(f"真人对话 JSON 数组后有多余内容：{path}")
                    return
                if not expect_value:
                    if buffer[0] != ",":
                        raise ValueError(f"真人对话 JSON 数组记录之间缺少逗号：{path}")
                    buffer, expect_value = buffer[1:], True
                    continue
                if buffer[0] == ",":
                    raise ValueError(f"真人对话 JSON 数组中存在多余逗号：{path}")
                try:
                    record, end = decoder.raw_decode(buffer)
                except json.JSONDecodeError as exc:
                    chunk = file.read(65536)
                    if not chunk:
                        raise ValueError(f"真人对话 JSON 无法解析：{path}: {exc}") from exc
                    buffer += chunk
                    continue
                yield record
                buffer = buffer[end:]
                expect_value = False
                seen += 1
        elif first:
            if path.suffix.lower() == ".jsonl":
                yield json.loads(first + file.readline())
                for line in file:
                    if line.strip():
                        yield json.loads(line)
            else:
                yield json.loads(first + file.read())


def real_record_case_id(obj: dict[str, Any]) -> str:
    if "caseID" in obj:
        values = obj["caseID"]
        if isinstance(values, str):
            values = [values]
        return values[0].strip() if isinstance(values, list) and len(values) == 1 and isinstance(values[0], str) else ""
    case_id = obj.get("case_id")
    if isinstance(case_id, str) and case_id.strip():
        return case_id.strip()
    identifier = str(obj.get("dialogue_id") or obj.get("session_id") or "")
    match = ID_RE.match(identifier)
    return match.group(1) if match else ""


@dataclass(frozen=True)
class Dialogue:
    dialogue_id: str
    case_id: str
    session_index: int
    messages: tuple[dict[str, str], ...]
    source: str
    metadata: dict[str, Any]


def make_dialogue(obj: dict[str, Any], source: Path, fallback_id: str | None = None) -> Dialogue:
    did = str(obj.get("dialogue_id") or obj.get("session_id") or fallback_id or "").strip()
    cid = str(obj.get("case_id") or "").strip()
    session = obj.get("session_index")
    if not cid and did and ID_RE.match(did):
        cid = ID_RE.match(did).group(1)
    if session is None and did and ID_RE.match(did):
        session = int(ID_RE.match(did).group(2))
    if not cid:
        raise ValueError(f"缺少 case_id：{source}")
    if session is None:
        raise ValueError(f"缺少 session_index：{source}；可以在文件名使用 CASE#1")
    session = int(session)
    did = did or f"{cid}#{session}"
    raw_messages = obj.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ValueError(f"缺少 messages：{source}")
    messages = []
    for n, item in enumerate(raw_messages, 1):
        if not isinstance(item, dict):
            raise ValueError(f"消息 {n} 不是对象：{source}")
        role = str(item.get("role", "")).lower()
        content = item.get("content", item.get("text", ""))
        if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
            raise ValueError(f"消息 {n} 缺少有效 role/content：{source}")
        messages.append({"role": role, "content": content.strip()})
    metadata = {k: obj[k] for k in ("turn_count", "solution_status", "stop_reason", "scenario_id", "persona_id", "group_id", "user_context") if k in obj}
    return Dialogue(did, cid, session, tuple(messages), str(source), metadata)


def outer_markers(raw: str) -> list[tuple[int, int, str, str]]:
    """Find dialogue headings and role markers outside quoted/fenced material."""
    markers = []
    offset = 0
    fence: tuple[str, int] | None = None
    for line in raw.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        fence_match = FENCE_RE.match(content)
        if fence_match:
            token = fence_match.group(1)
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1]:
                fence = None
        elif fence is None and not content.lstrip().startswith(">"):
            title = TITLE_RE.match(content)
            role = MESSAGE_RE.match(content)
            if title:
                markers.append((offset + title.start(), offset + title.end(), "title", title.group(1).strip()))
            elif role:
                markers.append((offset + role.start(), offset + role.end(), "role", role.group(1).lower()))
                inline_fence = FENCE_RE.match(content[role.end():])
                if inline_fence:
                    token = inline_fence.group(1)
                    fence = (token[0], len(token))
        offset += len(line)
    if fence is not None:
        raise ValueError("Markdown 存在未闭合代码块，无法可靠切分消息；请修复或使用 JSON messages")
    return markers


def parse_markdown_dialogues(path: Path) -> list[Dialogue]:
    raw = path.read_text(encoding="utf-8-sig")
    markers = outer_markers(raw)
    headings = []
    for i, marker in enumerate(markers):
        if marker[2] != "title":
            continue
        next_marker = markers[i + 1][0] if i + 1 < len(markers) else len(raw)
        preamble = raw[marker[1]:next_marker]
        has_metadata = re.search(r"(?m)^\s*-\s*case_id:\s*\S+", preamble) and re.search(r"(?m)^\s*-\s*session_index:\s*\S+", preamble)
        if ID_RE.match(marker[3]) or has_metadata:
            headings.append(marker)
    blocks = [(m[3], m[1], headings[i + 1][0] if i + 1 < len(headings) else len(raw)) for i, m in enumerate(headings)] if headings else [(path.stem, 0, len(raw))]
    result = []
    for title, start, end in blocks:
        roles = [m for m in markers if m[2] == "role" and start <= m[0] < end]
        if not roles:
            continue
        meta = {}
        preamble = raw[start:roles[0][0]]
        for key in ("case_id", "session_index", "turn_count", "solution_status", "stop_reason", "scenario_id", "persona_id", "group_id", "user_context"):
            hit = re.search(rf"(?m)^\s*-\s*{key}:\s*(.+?)\s*$", preamble)
            if hit:
                meta[key] = hit.group(1).strip()
        if title and ID_RE.match(title):
            meta["dialogue_id"] = title
        elif not headings and ID_RE.match(path.stem):
            meta["dialogue_id"] = path.stem
        msgs = []
        for i, marker in enumerate(roles):
            content = raw[marker[1]:roles[i + 1][0] if i + 1 < len(roles) else end].strip()
            if not content:
                raise ValueError(f"空消息：{path}, {title}")
            msgs.append({"role": marker[3], "content": content})
        meta["messages"] = msgs
        result.append(make_dialogue(meta, path, title if ID_RE.match(title) else None))
    if not result:
        raise ValueError(f"未找到有效对话：{path}")
    return result


def make_real_dialogue(obj: dict[str, Any], source: Path) -> Dialogue:
    """Read the exported human-service format without treating its numeric key as a case ID."""
    if "caseID" not in obj:
        return make_dialogue(obj, source, source.stem if ID_RE.match(source.stem) else None)
    case_ids = obj["caseID"]
    if isinstance(case_ids, str):
        case_ids = [case_ids]
    if not isinstance(case_ids, list) or len(case_ids) != 1 or not isinstance(case_ids[0], str) or not case_ids[0].strip():
        raise ValueError(f"真人对话 caseID 应恰有一个非空案例编号：{source}")
    conversation_keys = [key for key, value in obj.items() if isinstance(key, str) and key.isdigit() and isinstance(value, list)]
    if len(conversation_keys) != 1:
        raise ValueError(f"真人对话应恰有一个数字对话编号：{source}")
    conversation_id = conversation_keys[0]
    messages = []
    for index, entry in enumerate(obj[conversation_id], 1):
        if not isinstance(entry, dict) or len(entry) != 1:
            raise ValueError(f"真人对话 {conversation_id} 第 {index} 条消息格式不符：{source}")
        role, content = next(iter(entry.items()))
        if role not in {"用户", "客服"} or not isinstance(content, str) or not content.strip():
            raise ValueError(f"真人对话 {conversation_id} 第 {index} 条消息缺少用户/客服文本：{source}")
        messages.append({"role": "user" if role == "用户" else "assistant", "content": content})
    return make_dialogue({"dialogue_id": f"REAL_{conversation_id}", "case_id": case_ids[0].strip(), "session_index": 1, "messages": messages}, source)


def load_dialogues(folder: Path, real_reference: bool = False) -> list[Dialogue]:
    items = []
    for path in list_files(folder):
        if path.suffix.lower() in {".md", ".txt"}:
            items.extend(parse_markdown_dialogues(path))
        else:
            for obj in json_records(path):
                if not isinstance(obj, dict):
                    raise ValueError(f"对话记录不是对象：{path}")
                items.append(make_real_dialogue(obj, path) if real_reference else make_dialogue(obj, path, path.stem if ID_RE.match(path.stem) else None))
    if not items:
        raise ValueError(f"没有找到对话：{folder}")
    seen = set()
    for d in items:
        if d.dialogue_id in seen:
            raise ValueError(f"重复 dialogue_id：{d.dialogue_id}")
        seen.add(d.dialogue_id)
    return items


def load_matching_real_dialogues(source: Path, case_ids: set[str]) -> tuple[list[Dialogue], dict[str, int]]:
    """Select exact-case human references before annotation or batch requests."""
    selected = []
    audit = {"scanned": 0, "missing_case_id": 0, "matched": 0}
    for path in list_files(source):
        if path.suffix.lower() in {".md", ".txt"}:
            records = parse_markdown_dialogues(path)
            audit["scanned"] += len(records)
            selected.extend(d for d in records if d.case_id in case_ids)
            continue
        for obj in iter_real_records(path):
            audit["scanned"] += 1
            if not isinstance(obj, dict):
                raise ValueError(f"真人对话记录不是对象：{path}")
            case_id = real_record_case_id(obj)
            if not case_id:
                audit["missing_case_id"] += 1
                continue
            if case_id in case_ids:
                selected.append(make_real_dialogue(obj, path))
    seen = set()
    for dialogue in selected:
        if dialogue.dialogue_id in seen:
            raise ValueError(f"匹配到重复真人对话编号：{dialogue.dialogue_id}")
        seen.add(dialogue.dialogue_id)
    audit["matched"] = len(selected)
    return selected, audit


def normalize_case(obj: dict[str, Any], source: Path, fallback_id: str | None = None) -> tuple[str, dict[str, Any]]:
    if "target_case" in obj and isinstance(obj["target_case"], dict):
        target = obj["target_case"]
        cid = str(target.get("case_id") or obj.get("case_id") or "")
        parts = list(dict.fromkeys(str(target[k]) for k in ("phenomenon", "solution") if target.get(k)))
        content = {"case_name": target.get("title", ""), "text": parts, "source_note": "历史归一化案例快照，不等于已核验的原始案例库"}
    else:
        cid = str(obj.get("case_id") or fallback_id or "")
        text = obj.get("text", obj.get("content"))
        if isinstance(text, str):
            text = [text]
        if not isinstance(text, list) or not all(isinstance(x, str) for x in text):
            raise ValueError(f"案例 text 必须是字符串或字符串数组：{source}")
        content = {"case_name": obj.get("case_name", obj.get("title", cid)), "text": text}
    if not cid:
        raise ValueError(f"案例缺少 case_id：{source}")
    content["source_file"] = str(source)
    return cid, content


def load_cases(folder: Path) -> dict[str, dict[str, Any]]:
    cases = {}
    for path in list_files(folder):
        if path.suffix.lower() in {".md", ".txt"}:
            cid, data = path.stem, {"case_name": path.stem, "text": [path.read_text(encoding="utf-8-sig")], "source_file": str(path)}
            records = [(cid, data)]
        else:
            records = []
            for obj in json_records(path):
                if not isinstance(obj, dict):
                    raise ValueError(f"案例记录不是对象：{path}")
                if not any(k in obj for k in ("case_id", "target_case", "text", "content")):
                    records += [normalize_case(v, path, str(k)) for k, v in obj.items() if isinstance(v, dict)]
                else:
                    records.append(normalize_case(obj, path, path.stem))
        for cid, data in records:
            if cid in cases:
                raise ValueError(f"重复案例 ID：{cid}")
            cases[cid] = data
    return cases


def render_dialogue(d: Dialogue, upto: int | None = None, include_outcome: bool = True) -> str:
    lines = [f"## {d.dialogue_id}", "", f"- case_id: {d.case_id}", f"- session_index: {d.session_index}"]
    if include_outcome:
        for key in ("turn_count", "solution_status", "stop_reason"):
            if key in d.metadata:
                lines.append(f"- {key}: {d.metadata[key]}")
    if d.metadata.get("scenario_id"):
        lines.append(f"- scenario_id: {d.metadata['scenario_id']}")
    for key in ("persona_id", "group_id", "user_context"):
        if key in d.metadata:
            lines.append(f"- {key}: {json.dumps(d.metadata[key], ensure_ascii=False)}")
    for msg in d.messages[:upto]:
        lines.extend(["", f"**{msg['role'].title()}:** {msg['content']}"])
    return "\n".join(lines) + "\n"


def render_prompt(kind: str, replacements: dict[str, str]) -> str:
    template = PROMPT_PATHS[kind].read_text(encoding="utf-8")
    expected = set(PLACEHOLDERS[kind])
    if set(replacements) != expected or any(template.count(k) != 1 for k in expected):
        raise ValueError(f"prompt 占位符不符：{kind}")
    return re.sub(r"\{\{[A-Z_]+\}\}", lambda m: replacements[m.group()], template)


def job(kind: str, label: str, replacements: dict[str, str], model: str) -> dict[str, Any]:
    prompt = render_prompt(kind, replacements)
    identity = digest(json.dumps({"kind": kind, "label": label, "model": model, "prompt": prompt}, ensure_ascii=False, sort_keys=True))
    return {"job_id": identity[:24], "kind": kind, "label": label, "model": model, "prompt_sha256": digest(prompt), "prompt": prompt}


def make_core_jobs(dialogues: list[Dialogue], cases: dict[str, dict[str, Any]], model: str, evidence: dict[str, dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    jobs, skipped = [], []
    for d in dialogues:
        full = render_dialogue(d, include_outcome=False)
        jobs.append(job("natural_flow_dialogue", d.dialogue_id, {"{{DIALOGUE_TEXT}}": full}, model))
        jobs.append(job("expression_realism_dialogue", d.dialogue_id, {"{{INPUT_MARKDOWN}}": full}, model))
        for i, msg in enumerate(d.messages, 1):
            if msg["role"] == "user" and any(m["role"] == "assistant" for m in d.messages[:i - 1]):
                prefix = render_dialogue(d, i, include_outcome=False)
                jobs.append(job("natural_flow_turn", f"{d.dialogue_id}__m{i}", {"{{DIALOGUE_TEXT}}": prefix}, model))
        case = cases.get(d.case_id)
        if case and case.get("text") and any(str(x).strip() for x in case["text"]):
            if evidence is None:
                continue  # Leakage requests are built only after the evidence dependency succeeds.
            if d.case_id not in evidence:
                skipped.append({"kind": "information_leakage_dialogue", "dialogue_id": d.dialogue_id, "reason": "案例核验依据失败或完全不足，未运行泄漏评测"})
                continue
            payload = case_payload(d.case_id, case)
            item = job("information_leakage_dialogue", d.dialogue_id, {"{{CASE_TEXT}}": json.dumps(payload, ensure_ascii=False), "{{CASE_EVIDENCE}}": json.dumps(evidence[d.case_id], ensure_ascii=False, sort_keys=True), "{{DIALOGUE}}": full}, model)
            item["_case_evidence"] = evidence[d.case_id]
            item["_case_source"] = payload[d.case_id]
            jobs.append(item)
        else:
            skipped.append({"kind": "information_leakage_dialogue", "dialogue_id": d.dialogue_id, "reason": "缺少匹配案例或案例正文为空"})
    return jobs, skipped


def case_payload(case_id: str, case: dict[str, Any]) -> dict[str, Any]:
    body = {"case_name": case["case_name"], "text": case["text"]}
    if case.get("source_note"):
        body["source_note"] = case["source_note"]
    return {case_id: body}


def make_case_jobs(dialogues: list[Dialogue], cases: dict[str, dict[str, Any]], model: str) -> list[dict[str, Any]]:
    jobs = []
    for case_id in sorted({d.case_id for d in dialogues}):
        case = cases.get(case_id)
        if not case or not case.get("text") or not any(str(x).strip() for x in case["text"]):
            continue
        payload = case_payload(case_id, case)
        item = job("case_information_evidence", case_id, {"{{CASE_TEXT}}": json.dumps(payload, ensure_ascii=False, sort_keys=True)}, model)
        item["_case_source"] = payload[case_id]
        jobs.append(item)
    return jobs


def validate_source(source: Any, case: dict[str, Any]) -> None:
    if not isinstance(source, dict) or not isinstance(source.get("quote"), str) or not source["quote"].strip():
        raise ValueError("案例依据缺少原文引用")
    location = source.get("location", "")
    match = re.fullmatch(r"text\[(\d+)\]", location) if isinstance(location, str) else None
    original = case.get("case_name", "") if location == "case_name" else ""
    if match and int(match[1]) < len(case["text"]):
        original = case["text"][int(match[1])]
    if source["quote"] not in original:
        raise ValueError("案例依据引用不属于指定原文位置")


def validate_checks(kind: str, data: dict[str, Any]) -> None:
    prefix = {"natural_flow_turn": "T", "natural_flow_dialogue": "D", "expression_realism_dialogue": "R", "information_leakage_dialogue": "L"}[kind]
    keys = {prefix + str(i) for i in range(1, 5 if prefix == "L" else 4)}
    checks = data.get("checks")
    if not isinstance(checks, dict) or set(checks) != keys:
        raise ValueError("checks 必须完整包含本模块小指标")
    for key, check in checks.items():
        if not isinstance(check, dict) or check.get("status") not in {"no_issue", "issue", "not_applicable", "insufficient_evidence"} or not isinstance(check.get("reason"), str) or not check["reason"].strip() or "score" not in check:
            raise ValueError(f"{key} 缺少状态、分数或理由")
        status, score = check["status"], check["score"]
        if status == "no_issue" and (type(score) is not int or score != 5) or status == "issue" and (type(score) is not int or not 1 <= score <= 4) or status in {"not_applicable", "insufficient_evidence"} and score is not None:
            raise ValueError(f"{key} 小分与状态不符")
        if prefix != "L":
            evidence = check.get("evidence")
            if not isinstance(evidence, list) or status == "issue" and not evidence:
                raise ValueError(f"{key} 缺少证据数组")
            if any(not isinstance(e, dict) or not isinstance(e.get("message_id"), str) or not isinstance(e.get("quote"), str) or not e["quote"].strip() for e in evidence):
                raise ValueError(f"{key} 原文证据格式无效")
    declared = {k for k, c in checks.items() if c["status"] == "issue"}
    if prefix == "L":
        events = data["violations"]
        ids = [e.get("event_id") for e in events]
        if any(not isinstance(e, str) or not e for e in ids) or len(set(ids)) != len(ids):
            raise ValueError("泄漏事件编号必须唯一")
        if any(e.get("category") not in keys for e in events):
            raise ValueError("泄漏事件 category 无效")
        cited = {e["category"] for e in events}
        for key, check in checks.items():
            expected = [e["event_id"] for e in events if e["category"] == key]
            refs = check.get("event_ids")
            if not isinstance(refs, list) or any(not isinstance(x, str) for x in refs) or sorted(refs) != sorted(expected):
                raise ValueError(f"{key} 引用事件与入口不一致")
            if expected and check["score"] != max(1, 5 - sum(e["deduction"] for e in events if e["category"] == key)):
                raise ValueError(f"{key} 小分与事件扣分不一致")
        if data["assessment_status"] == "assessed" and any(c["status"] == "insufficient_evidence" for c in checks.values()):
            raise ValueError("存在未核验入口时泄漏总体必须为 partial 或 insufficient_evidence")
    else:
        findings = data["findings" if prefix == "R" else "issues"]
        cited = set()
        for finding in findings:
            if not isinstance(finding, dict) or not isinstance(finding.get("criteria"), list) or not finding["criteria"] or any(c not in keys for c in finding["criteria"]):
                raise ValueError("问题记录缺少有效 criteria")
            cited.update(finding["criteria"])
    if declared != cited:
        raise ValueError("小项 issue 与确认事件不一致")
    assessable = any(c["status"] in {"no_issue", "issue"} for c in checks.values())
    if assessable != (data["assessment_status"] in {"scored", "assessed", "partial"}):
        raise ValueError("小项可评性与总体状态不一致")


def group_chunks(items: list[Dialogue], count: int) -> list[list[Dialogue]]:
    return [items[i:i + count] for i in range(0, len(items), count)]


def make_annotation_jobs(dialogues: list[Dialogue], model: str, count: int, set_name: str) -> list[dict[str, Any]]:
    jobs = []
    for i, group in enumerate(group_chunks(dialogues, count), 1):
        payload = "\n".join(render_dialogue(d, include_outcome=False) for d in group)
        item = job("expression_feature_annotation", f"{set_name}_{i:03d}", {"{{INPUT_MARKDOWN}}": payload}, model)
        item["_sources"] = group
        jobs.append(item)
    return jobs


def parse_model_json(raw: str) -> dict[str, Any]:
    s = raw.strip()
    # Some Qwen deployments place a complete reasoning block in content.
    # Never extract a tentative JSON object from inside that block.
    while s.startswith("<think>"):
        end = s.find("</think>", len("<think>"))
        if end < 0:
            raise ValueError("模型思考块未闭合，未取得完整最终 JSON")
        s = s[end + len("</think>"):].strip()
    if not s:
        raise ValueError("模型未返回最终 JSON（可能只返回了思考内容）")
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I).strip()
    value = json.loads(s)
    if not isinstance(value, dict):
        raise ValueError("模型输出不是 JSON 对象")
    return value


def validate_output(kind: str, data: dict[str, Any], b3_stat_ids: list[str] | None = None, has_real_reference: bool = False) -> None:
    required = {
        "case_information_evidence": ("schema_version", "case_id", "assessment_status", "facts", "limitations"),
        "natural_flow_turn": ("assessment_status", "score", "checks"),
        "natural_flow_dialogue": ("assessment_status", "score", "checks"),
        "information_leakage_dialogue": ("assessment_status", "score", "violations"),
        "expression_realism_dialogue": ("assessment_status", "overall_score", "findings"),
        "expression_feature_annotation": ("schema_version", "dialogues", "excluded_dialogues"),
        "expression_realism_batch": ("B1", "B2", "B3", "scope"),
    }[kind]
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"模型输出缺少字段：{','.join(missing)}")
    if kind == "case_information_evidence":
        if data["schema_version"] != "case_evidence_v1" or data["assessment_status"] not in {"ready", "partial", "insufficient_evidence"} or not isinstance(data["facts"], list) or not isinstance(data["limitations"], list):
            raise ValueError("案例核验依据结构无效")
        if bool(data["facts"]) != (data["assessment_status"] != "insufficient_evidence"):
            raise ValueError("案例核验依据状态与事实数量不符")
        ids = set()
        for fact in data["facts"]:
            if not isinstance(fact, dict) or not isinstance(fact.get("fact_id"), str) or not re.fullmatch(r"F[1-9][0-9]*", fact["fact_id"]) or fact["fact_id"] in ids:
                raise ValueError("案例事实编号无效或重复")
            ids.add(fact["fact_id"])
            field_rules = {
                "level": ("level" in fact and (fact["level"] is None or type(fact["level"]) is int and fact["level"] in {1, 2, 3}), "整数 1/2/3 或 null"),
                "is_answer": (type(fact.get("is_answer")) is bool, "布尔值 true/false"),
                "importance": (isinstance(fact.get("importance"), str) and fact["importance"] in {"low", "medium", "high"}, "low/medium/high"),
            }
            for field, (valid, expected) in field_rules.items():
                if not valid:
                    value = repr(fact[field])[:80] if field in fact else "<缺失>"
                    raise ValueError(f"案例事实 {fact['fact_id']}.{field} 无效：收到 {value}；要求 {expected}")
            if any(not isinstance(fact.get(k), str) or not fact[k].strip() for k in ("content", "reason")) or not isinstance(fact.get("conditions"), list):
                raise ValueError("案例事实缺少内容、理由或条件")
            if "level_evidence" not in fact or fact["level"] is None and fact["level_evidence"] is not None:
                raise ValueError("未知等级的依据必须为 null")
            for condition in fact["conditions"]:
                if not isinstance(condition, dict) or condition.get("type") not in {"acquisition", "disclosure"} or not isinstance(condition.get("description"), str) or not condition["description"].strip():
                    raise ValueError("案例条件结构无效")
        return
    statuses = {
        "natural_flow_turn": {"scored", "not_applicable", "insufficient_evidence"},
        "natural_flow_dialogue": {"scored", "not_applicable", "insufficient_evidence"},
        "information_leakage_dialogue": {"assessed", "partial", "insufficient_evidence"},
        "expression_realism_dialogue": {"scored", "insufficient_evidence"},
    }
    if kind in statuses and data["assessment_status"] not in statuses[kind]:
        raise ValueError(f"{kind} 的 assessment_status 无效")
    score = data.get("score", data.get("overall_score"))
    if kind in statuses:
        scored = data["assessment_status"] in {"scored", "assessed", "partial"}
        if (scored and not (type(score) is int and 1 <= score <= 5)) or (not scored and score is not None):
            raise ValueError("评分状态与 score/overall_score 不一致")
        evidence_key = "findings" if kind == "expression_realism_dialogue" else "violations" if kind == "information_leakage_dialogue" else "issues"
        if not isinstance(data.get(evidence_key), list):
            raise ValueError(f"{kind} 的 {evidence_key} 必须是数组")
        if scored and score < 5 and not data[evidence_key]:
            raise ValueError("低于 5 分却没有确认的问题证据")
        if scored and score == 5 and data[evidence_key]:
            raise ValueError("存在确认的问题证据却给出 5 分")
        if not scored and data[evidence_key]:
            raise ValueError("不可评分状态不能包含确认问题事件")
        if kind in {"natural_flow_turn", "natural_flow_dialogue"} and not isinstance(data["checks"], dict):
            raise ValueError("checks 必须是对象")
        if kind in {"natural_flow_turn", "natural_flow_dialogue"}:
            prefix = "T" if kind == "natural_flow_turn" else "D"
            for criterion in (prefix + str(i) for i in range(1, 4)):
                check = data["checks"].get(criterion)
                if not isinstance(check, dict) or check.get("status") not in {"no_issue", "issue", "not_applicable", "insufficient_evidence"} or not isinstance(check.get("reason"), str) or not check["reason"].strip():
                    raise ValueError(f"checks 缺少有效的 {criterion} 状态和理由")
            declared = {k for k, v in data["checks"].items() if isinstance(v, dict) and v.get("status") == "issue"}
            cited = set()
            for issue in data["issues"]:
                if not isinstance(issue, dict) or not isinstance(issue.get("criteria"), list) or not issue["criteria"]:
                    raise ValueError("issues 缺少 criteria")
                cited.update(issue["criteria"])
            if declared != cited:
                raise ValueError("checks 的 issue 状态与 issues 不一致")
            check_statuses = {data["checks"][prefix + str(i)]["status"] for i in range(1, 4)}
            if scored and not check_statuses & {"no_issue", "issue"}:
                raise ValueError("全部小项不可评时不能给整体分数")
            if not scored and check_statuses & {"no_issue", "issue"}:
                raise ValueError("整体不可评状态与 checks 可评状态不一致")
        if kind == "information_leakage_dialogue" and scored:
            deductions = []
            for violation in data["violations"]:
                if not isinstance(violation, dict) or violation.get("severity") not in {"low", "medium", "high"}:
                    raise ValueError("信息泄漏事件缺少有效 severity")
                deduction = violation.get("deduction")
                if type(deduction) is not int or deduction != {"low": 1, "medium": 2, "high": 4}[violation["severity"]]:
                    raise ValueError("信息泄漏 severity 与 deduction 不一致")
                deductions.append(deduction)
            if score != max(1, 5 - sum(deductions)):
                raise ValueError("信息泄漏 score 与事件扣分总和不一致")
        validate_checks(kind, data)
    elif kind == "expression_feature_annotation":
        if data["schema_version"] != "expression_features_v1" or not isinstance(data["dialogues"], list) or not isinstance(data["excluded_dialogues"], list):
            raise ValueError("表达特征标注的结构或版本无效")
    else:
        if not isinstance(data["scope"], dict):
            raise ValueError("Batch scope 必须是对象")
        allowed = {
            "B1": {"issue", "no_issue", "insufficient_evidence"},
            "B2": {"issue", "no_issue", "descriptive_only", "insufficient_evidence"},
            "B3": {"compared", "not_evaluated", "insufficient_evidence"},
        }
        for key, options in allowed.items():
            item = data[key]
            details = "comparisons" if key == "B3" else "findings"
            if not isinstance(item, dict) or item.get("status") not in options or not isinstance(item.get(details), list):
                raise ValueError(f"Batch {key} 结构或状态无效")
        comparisons = data["B3"]["comparisons"]
        if bool(comparisons) != (data["B3"]["status"] == "compared"):
            raise ValueError("B3 状态与统计比较记录不一致")
        if any(not isinstance(item, dict) or not isinstance(item.get("stat_id"), str) for item in comparisons):
            raise ValueError("B3 comparison 缺少 stat_id")
        if b3_stat_ids is not None:
            b3_status = data["B3"]["status"]
            if not has_real_reference and b3_status != "not_evaluated":
                raise ValueError("没有真人参照时 B3 必须为 not_evaluated")
            if has_real_reference and not b3_stat_ids and b3_status != "insufficient_evidence":
                raise ValueError("没有可比统计时 B3 必须为 insufficient_evidence")
            if has_real_reference and b3_stat_ids and b3_status == "not_evaluated":
                raise ValueError("已有真人统计却将 B3 标为未评估")
            if any(item["stat_id"] not in b3_stat_ids for item in comparisons):
                raise ValueError("B3 comparison 引用了不存在的统计编号")


def api_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def call_model(prompt: str, model: str, base_url: str, api_key: str, timeout: float, retries: int) -> tuple[str, dict[str, Any]]:
    payload = json.dumps({"model": model, "temperature": 0, "messages": [{"role": "user", "content": prompt}]}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    endpoint = api_endpoint(base_url)
    for attempt in range(retries + 1):
        try:
            req = request.Request(endpoint, data=payload, headers=headers, method="POST")
            with request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            finish_reason = data["choices"][0].get("finish_reason")
            choice = data["choices"][0]["message"]
            content = choice.get("content")
            if isinstance(content, list):
                content = "".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
            if not isinstance(content, str) or not content.strip():
                raise ValueError("模型返回空内容")
            return content, {"usage": data.get("usage"), "model": data.get("model", model), "finish_reason": finish_reason}
        except error.HTTPError as exc:
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == retries:
                raise RuntimeError(f"HTTP {exc.code}；检查服务地址、模型和权限") from None
        except (error.URLError, TimeoutError) as exc:
            if attempt == retries:
                raise RuntimeError(f"连接失败：{type(exc).__name__}；检查服务地址或网络") from None
        time.sleep(min(2 ** attempt, 8))
    raise AssertionError("unreachable")


def validate_job_result(item: dict[str, Any], data: dict[str, Any]) -> None:
    validate_output(item["kind"], data, item.get("b3_stat_ids"), item.get("has_real_reference", False))
    if item["kind"] == "case_information_evidence":
        if data["case_id"] != item["label"]:
            raise ValueError("案例依据 case_id 与输入不一致")
        for fact in data["facts"]:
            validate_source(fact.get("source"), item["_case_source"])
            if fact["level"] is not None:
                validate_source(fact.get("level_evidence"), item["_case_source"])
            for condition in fact["conditions"]:
                validate_source(condition.get("source"), item["_case_source"])
    if item["kind"] == "information_leakage_dialogue":
        facts = {f["fact_id"]: f for f in item["_case_evidence"]["facts"]}
        for event in data["violations"]:
            refs = event.get("fact_ids")
            if not isinstance(refs, list) or not refs or any(not isinstance(r, str) or r not in facts for r in refs):
                raise ValueError("泄漏事件缺少有效案例事实编号")
            if not any(facts[r]["conditions"] for r in refs):
                raise ValueError("泄漏事件引用的案例事实没有获知/披露条件")
    if item["kind"] == "expression_feature_annotation":
        if data["excluded_dialogues"]:
            raise ValueError("标注排除了输入会话，需修复后再生成统计")
        validate_annotation_set(annotation_index(data["dialogues"]), item["_sources"])
    if item["kind"] == "expression_realism_batch":
        if item["batch_dialogue_count"] < 2 and data["B1"]["status"] != "insufficient_evidence":
            raise ValueError("少于两段对话时 B1 必须为 insufficient_evidence")
        if not item["has_target_population"] and data["B2"]["status"] not in {"descriptive_only", "insufficient_evidence"}:
            raise ValueError("未提供目标人群时 B2 只能描述或标记证据不足")


def execute_jobs(jobs: list[dict[str, Any]], output: Path, base_url: str | None, api_key: str, timeout: float, retries: int, dry_run: bool, force: bool = False) -> tuple[int, int]:
    result_dir = output / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    ok = failed = 0
    for n, item in enumerate(jobs, 1):
        dest = result_dir / (item["job_id"] + ".json")
        service_sha256 = digest(api_endpoint(base_url or ""))
        if dest.exists() and not force:
            try:
                old = read_json(dest)
                if old.get("status") == "completed" and old.get("prompt_sha256") == item["prompt_sha256"] and old.get("model") == item["model"] and old.get("service_sha256") == service_sha256:
                    validate_job_result(item, old["result"])
                    ok += 1
                    continue
                if (not dry_run and old.get("status") == "failed"
                        and old.get("prompt_sha256") == item["prompt_sha256"]
                        and old.get("model") == item["model"]
                        and old.get("service_sha256") == service_sha256
                        and isinstance(old.get("raw_response"), str)
                        and isinstance(old.get("api"), dict)
                        and old["api"].get("finish_reason") in {None, "stop"}):
                    parsed = parse_model_json(old["raw_response"])
                    validate_job_result(item, parsed)
                    old.update(status="completed", result=parsed, recovered_from_raw_response=True)
                    old.pop("error", None)
                    write_json(dest, old)
                    ok += 1
                    print(f"[{n}/{len(jobs)}] {item['kind']} {item['label']}: recovered（复用已保存回复）", flush=True)
                    continue
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                pass  # A stale or damaged cache entry is regenerated below.
        if dry_run:
            continue
        raw, meta = None, {}
        try:
            raw, meta = call_model(item["prompt"], item["model"], base_url or "", api_key, timeout, retries)
            if meta.get("finish_reason") not in {None, "stop"}:
                raise ValueError(f"模型未正常结束：finish_reason={meta['finish_reason']}")
            parsed = parse_model_json(raw)
            validate_job_result(item, parsed)
            record = {"job_id": item["job_id"], "kind": item["kind"], "label": item["label"], "model": item["model"], "prompt_sha256": item["prompt_sha256"], "status": "completed", "result": parsed, "api": meta}
            ok += 1
        except (ValueError, KeyError, IndexError, TypeError, AttributeError, RuntimeError, OSError) as exc:
            record = {"job_id": item["job_id"], "kind": item["kind"], "label": item["label"], "model": item["model"], "prompt_sha256": item["prompt_sha256"], "status": "failed", "error": str(exc)}
            failed += 1
            record["raw_response"] = raw
            record["api"] = meta
        record["service_sha256"] = service_sha256
        write_json(dest, record)
        print(f"[{n}/{len(jobs)}] {item['kind']} {item['label']}: {record['status']}", flush=True)
    return ok, failed


def annotations_for(jobs: list[dict[str, Any]], output: Path) -> list[dict[str, Any]] | None:
    all_items = []
    for item in jobs:
        dest = output / "results" / (item["job_id"] + ".json")
        if not dest.exists():
            return None
        record = read_json(dest)
        if record.get("status") != "completed":
            return None
        all_items += record["result"]["dialogues"]
    return all_items


def annotation_index(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    indexed = {}
    for d in items:
        did = d.get("dialogue_id")
        if not isinstance(did, str) or did in indexed or not isinstance(d.get("messages"), list):
            raise ValueError("标注结果含缺失或重复 dialogue_id")
        indexed[did] = d["messages"]
    return indexed


def validate_annotation_set(indexed: dict[str, list[dict[str, Any]]], sources: list[Dialogue]) -> None:
    if set(indexed) != {d.dialogue_id for d in sources}:
        raise ValueError("标注结果的会话编号与输入不一致，不能生成真人对照统计")
    for d in sources:
        actual = indexed[d.dialogue_id]
        expected = [m["content"] for m in d.messages if m["role"] == "user"]
        numbered, positions, counts = {}, {}, Counter()
        for position, message in enumerate(d.messages):
            prefix = "U" if message["role"] == "user" else "A"
            counts[prefix] += 1
            identifier = f"{prefix}{counts[prefix]}"
            numbered[identifier], positions[identifier] = message["content"], position
        if len(actual) != len(expected):
            raise ValueError(f"{d.dialogue_id} 标注消息数与原文不符")
        for n, (item, original) in enumerate(zip(actual, expected), 1):
            copied = item.get("user_text")
            if item.get("message_id") != f"U{n}" or not isinstance(copied, str) or re.sub(r"\s+", " ", copied).strip() != re.sub(r"\s+", " ", original).strip():
                raise ValueError(f"{d.dialogue_id} U{n} 标注原文或编号与输入不符")
            item["user_text"] = original  # Numeric features always use the source text.
            features = item.get("features")
            if not isinstance(features, dict):
                raise ValueError(f"{d.dialogue_id} U{n} 缺少表达特征")
            context = item.get("context")
            if not isinstance(context, dict):
                raise ValueError(f"{d.dialogue_id} U{n} 缺少交流情境")
            if "assistant_feedback" not in context:
                raise ValueError(f"{d.dialogue_id} U{n} 缺少 assistant_feedback 标签")
            if item.get("status") not in {"annotated", "partial", "insufficient_evidence"}:
                raise ValueError(f"{d.dialogue_id} U{n} 的标注 status 无效")
            for key in ("tasks", "detail_requested", "format_requested", "has_quoted_material", "quoted_spans"):
                if key not in context:
                    raise ValueError(f"{d.dialogue_id} U{n} 缺少 {key}")
            tasks = context["tasks"]
            if tasks is not None and (not isinstance(tasks, list) or not tasks or any(not isinstance(x, str) or x not in TASK_LABELS for x in tasks) or len(set(tasks)) != len(tasks)):
                raise ValueError(f"{d.dialogue_id} U{n} tasks 无效")
            for key in ("detail_requested", "format_requested", "has_quoted_material"):
                if context[key] is not None and type(context[key]) is not bool:
                    raise ValueError(f"{d.dialogue_id} U{n} {key} 必须是布尔值或 null")
            spans = context["quoted_spans"]
            if spans is not None and (not isinstance(spans, list) or any(not isinstance(x, str) or not x or x not in original for x in spans)):
                raise ValueError(f"{d.dialogue_id} U{n} quoted_spans 不在原文中")
            if context["has_quoted_material"] is True and not spans or context["has_quoted_material"] is False and spans:
                raise ValueError(f"{d.dialogue_id} U{n} 引用标记与 quoted_spans 冲突")
            if any(name not in features for name in FEATURE_LABELS):
                raise ValueError(f"{d.dialogue_id} U{n} 表达特征缺字段")
            values = list(context.values()) + [features[name] for name in FEATURE_LABELS]
            if item["status"] == "annotated" and any(v is None for v in values):
                raise ValueError(f"{d.dialogue_id} U{n} annotated 不允许 null 标签")
            if item["status"] == "insufficient_evidence" and any(v is not None for v in values):
                raise ValueError(f"{d.dialogue_id} U{n} insufficient_evidence 不应含已判标签")
            if not isinstance(item.get("limitations"), list) or not isinstance(item.get("evidence"), list):
                raise ValueError(f"{d.dialogue_id} U{n} 缺少 evidence/limitations 数组")
            if item["status"] != "annotated" and not item["limitations"]:
                raise ValueError(f"{d.dialogue_id} U{n} 不完整标注需要说明限制")
            if item["status"] == "partial" and not any(v is None for v in values):
                raise ValueError(f"{d.dialogue_id} U{n} partial 应标明哪些标签不能判断")
            for evidence in item["evidence"]:
                if not isinstance(evidence, dict):
                    raise ValueError("标注 evidence 必须为对象")
                identifier, quote = evidence.get("message_id"), evidence.get("quote")
                if not isinstance(identifier, str) or identifier not in numbered or not isinstance(quote, str) or not quote or quote not in numbered[identifier]:
                    raise ValueError(f"{d.dialogue_id} U{n} 标注引文与原文不符")
                if positions[identifier] > positions[f"U{n}"]:
                    raise ValueError(f"{d.dialogue_id} U{n} 标注不能引用未来消息")
                related = evidence.get("related_message_ids", [])
                if not isinstance(related, list) or any(not isinstance(mid, str) or mid not in positions or positions[mid] >= positions[f"U{n}"] for mid in related):
                    raise ValueError(f"{d.dialogue_id} U{n} related_message_ids 必须指向此前消息")
            feedback = context.get("assistant_feedback")
            if feedback is None and item.get("status") == "annotated":
                raise ValueError(f"{d.dialogue_id} U{n} 已完整标注却缺少 assistant_feedback")
            if feedback is not None:
                if not isinstance(feedback, list) or not feedback or any(not isinstance(x, str) or x not in FEEDBACK_LABELS for x in feedback) or len(set(feedback)) != len(feedback) or ("无反馈" in feedback and len(feedback) > 1):
                    raise ValueError(f"{d.dialogue_id} U{n} 的 assistant_feedback 标签无效")
            for name, allowed in FEATURE_LABELS.items():
                value = features.get(name)
                if value is None:
                    continue
                if name in {"formality", "organization"} and not isinstance(value, str) or name not in {"formality", "organization"} and not isinstance(value, list):
                    raise ValueError(f"{d.dialogue_id} U{n} 的 {name} 类型不符")
                observed = [value] if isinstance(value, str) else value
                if not isinstance(observed, list) or any(not isinstance(x, str) or x not in allowed for x in observed):
                    raise ValueError(f"{d.dialogue_id} U{n} 的 {name} 标签不在词表中")
                if name in {"formality", "organization"} and len(observed) != 1:
                    raise ValueError(f"{d.dialogue_id} U{n} 的 {name} 必须单选")
                empty_label = {"emotion": "无明显情绪", "politeness": "无", "restatement": "无"}.get(name)
                if not observed or len(set(observed)) != len(observed) or (empty_label in observed and len(observed) > 1):
                    raise ValueError(f"{d.dialogue_id} U{n} 的 {name} 标签重复、为空或互斥冲突")


def comparable_rows(indexed: dict[str, list[dict[str, Any]]], scenarios: dict[str, str] | None = None) -> dict[str, list[dict[str, Any]]]:
    groups = defaultdict(list)
    for dialogue_id, messages in indexed.items():
        for m in messages:
            if m.get("status") not in {"annotated", "partial"}:
                continue
            c, f = m.get("context", {}), m.get("features", {})
            if c.get("has_quoted_material") is not False or c.get("detail_requested") is None or c.get("format_requested") is None:
                continue
            tasks = c.get("tasks")
            if not isinstance(tasks, list) or not tasks or not isinstance(m.get("user_text"), str):
                continue
            for task in dict.fromkeys(tasks):
                prefix = f"情境={scenarios[dialogue_id]}；" if scenarios and dialogue_id in scenarios else ""
                key = f"{prefix}任务={task}；要求详细={str(c['detail_requested']).lower()}；要求格式={str(c['format_requested']).lower()}；无直接引用"
                groups[key].append({**m, "_dialogue_id": dialogue_id})
    return groups


def labeled_rows(rows: list[dict[str, Any]], field: str) -> list[tuple[dict[str, Any], set[str]]]:
    valid = []
    for m in rows:
        val = (m.get("context", {}) if field == "assistant_feedback" else m.get("features", {})).get(field)
        if val is None or not (isinstance(val, str) or isinstance(val, list)):
            continue
        labels = [val] if isinstance(val, str) else val
        if not labels:
            continue
        valid.append((m, set(labels)))
    return valid


def distribution(rows: list[dict[str, Any]], field: str) -> tuple[int, dict[str, float]]:
    valid = labeled_rows(rows, field)
    if not valid:
        return 0, {}
    counts = Counter(label for _, labels in valid for label in labels)
    return len(valid), {k: round(v / len(valid), 4) for k, v in sorted(counts.items())}


def wasserstein_1(left: list[int], right: list[int]) -> float:
    """Exact 1D empirical W1: integrate the gap between both ECDFs."""
    a, b = sorted(left), sorted(right)
    if not a or not b:
        raise ValueError("Wasserstein 距离要求两组均非空")
    i = j = 0
    previous = min(a[0], b[0])
    distance = 0.0
    for current in sorted(set(a) | set(b)):
        distance += (current - previous) * abs(i / len(a) - j / len(b))
        while i < len(a) and a[i] == current:
            i += 1
        while j < len(b) and b[j] == current:
            j += 1
        previous = current
    return round(distance, 4)


def feature_statistics(batch: list[dict[str, Any]], real: list[dict[str, Any]], min_n: int, comparability_note: str, scenario_by_id: dict[str, str] | None = None) -> list[dict[str, Any]]:
    batch_ids = {d["dialogue_id"] for d in batch}
    real_ids = {d["dialogue_id"] for d in real}
    shared_scenarios = ({scenario_by_id[k] for k in batch_ids if k in scenario_by_id} & {scenario_by_id[k] for k in real_ids if k in scenario_by_id}) if scenario_by_id else set()
    if not shared_scenarios:
        return []
    matched = bool(shared_scenarios)
    scenario_filter = {k: v for k, v in scenario_by_id.items() if v in shared_scenarios} if matched and scenario_by_id else None
    bg = comparable_rows(annotation_index(batch), scenario_filter)
    rg = comparable_rows(annotation_index(real), scenario_filter)
    if matched:
        bg = {k: v for k, v in bg.items() if k.startswith("情境=")}
        rg = {k: v for k, v in rg.items() if k.startswith("情境=")}
    out = []
    for condition in sorted(bg.keys() & rg.keys()):
        left, right = bg[condition], rg[condition]
        if min(len(left), len(right)) < min_n:
            continue
        for field in ("assistant_feedback", "emotion", "formality", "organization", "politeness", "restatement"):
            bn, bv = distribution(left, field)
            rn, rv = distribution(right, field)
            if min(bn, rn) < min_n:
                continue
            labels = sorted(FEEDBACK_LABELS if field == "assistant_feedback" else FEATURE_LABELS[field])
            b_valid, r_valid = labeled_rows(left, field), labeled_rows(right, field)
            b_counts = Counter(label for _, row_labels in b_valid for label in row_labels)
            r_counts = Counter(label for _, row_labels in r_valid for label in row_labels)
            raw_gaps = {label: b_counts[label] / bn - r_counts[label] / rn for label in labels}
            gaps = {label: round(raw_gaps[label], 4) for label in labels}
            is_single = field in {"formality", "organization"}
            metric = "total_variation" if is_single else "mean_absolute_prevalence_gap"
            value = (sum(abs(raw_gaps[label]) for label in labels) / 2 if is_single else sum(abs(raw_gaps[label]) for label in labels) / len(labels))
            note = comparability_note + "；按相同 case_id 配对"
            out.append({"stat_id": f"B3_{len(out)+1:04d}", "criterion": "B3", "condition": condition, "matching_level": "same_scenario" if matched else "context_only", "feature": field, "definition": "每条USER消息可观察标签的出现比例；多选类别各自计入，分母是该特征可判的消息数", "unit": "比例分布", "sample_unit": "USER消息", "batch_n": bn, "batch_dialogue_n": len({m['_dialogue_id'] for m, _ in b_valid}), "batch_value": bv, "reference_n": rn, "reference_dialogue_n": len({m['_dialogue_id'] for m, _ in r_valid}), "reference_value": rv, "category_gaps": gaps, "distance": {"metric": metric, "value": round(value, 4), "unit": "0-1", "direction": "越小越接近；仅为本条件下的分布差异"}, "method": "两组使用同一标注prompt；按表达任务和助手要求分层，排除直接引用；程序计数并计算分布距离", "comparability_notes": note})
        lengths_left = [len(m["user_text"].strip()) for m in left]
        lengths_right = [len(m["user_text"].strip()) for m in right]
        if min(len(lengths_left), len(lengths_right)) >= min_n:
            note = comparability_note + "；按相同 case_id 配对"
            out.append({"stat_id": f"B3_{len(out)+1:04d}", "criterion": "B3", "condition": condition, "matching_level": "same_scenario" if matched else "context_only", "feature": "reply_length", "definition": "USER消息原文去首尾空白后的Unicode字符数，包含标点，不含Markdown角色标签；直接引用消息已排除", "unit": "字符", "sample_unit": "USER消息", "batch_n": len(left), "batch_dialogue_n": len({m['_dialogue_id'] for m in left}), "batch_value": {"median": median(lengths_left)}, "reference_n": len(right), "reference_dialogue_n": len({m['_dialogue_id'] for m in right}), "reference_value": {"median": median(lengths_right)}, "distance": {"metric": "wasserstein_1", "value": wasserstein_1(lengths_left, lengths_right), "unit": "字符", "direction": "越小越接近；仅为本条件下的长度分布差异"}, "method": "程序按相同规则计算两组中位数及逐条长度的经验Wasserstein-1距离", "comparability_notes": note})
    for stat in out:
        field = stat["feature"]
        for name, groups in (("batch", bg), ("reference", rg)):
            rows = groups[stat["condition"]]
            if field != "reply_length":
                rows = [m for m, _ in labeled_rows(rows, field)]
            stat[name + "_messages"] = [{"dialogue_id": m["_dialogue_id"], "message_id": m["message_id"]} for m in rows]
    return out


def comparison_coverage(batch: list[dict[str, Any]], real: list[dict[str, Any]], stats: list[dict[str, Any]], scenarios: dict[str, str]) -> dict[str, Any]:
    """Count unique messages actually used, without adding overlapping strata."""
    shared = {scenarios[d["dialogue_id"]] for d in batch} & {scenarios[d["dialogue_id"]] for d in real}
    report = {"matching_level": "same_scenario" if shared else "context_only", "stat_count": len(stats), "groups": {}}
    for name, rows in (("batch", batch), ("reference", real)):
        total = {(d["dialogue_id"], m["message_id"]) for d in rows for m in d["messages"]}
        eligible_groups = comparable_rows(annotation_index(rows))
        eligible = {(m["_dialogue_id"], m["message_id"]) for group in eligible_groups.values() for m in group}
        matched = {key for key in eligible if shared and scenarios[key[0]] in shared}
        used = {(m["dialogue_id"], m["message_id"]) for stat in stats for m in stat[name + "_messages"]}
        report["groups"][name] = {
            "input_dialogue_n": len(rows), "input_user_message_n": len(total),
            "eligible_user_message_n": len(eligible),
            "excluded_by_annotation_or_quote_n": len(total - eligible),
            "excluded_by_scenario_n": len(eligible - matched),
            "used_in_any_stat_message_n": len(used),
            "used_in_any_stat_dialogue_n": len({did for did, _ in used}),
            "eligible_but_no_stat_n": len(matched - used),
            "used_by_feature": {feature: len({(m["dialogue_id"], m["message_id"]) for stat in stats if stat["feature"] == feature for m in stat[name + "_messages"]}) for feature in ("assistant_feedback", *FEATURE_LABELS, "reply_length")},
        }
    report["limitations"] = ["不同会话编号数不保证用户或会话独立；多任务分组可重叠，不能相加", "未进入统计可能来自条件不重合、特征缺失或样本数不足；不能解释为没有差异", "统计只描述已纳入样本，不推断总体；同一真人集合可能被多个batch重复使用"]
    return report


def write_feature_statistics_csv(path: Path, stats_by_batch: dict[str, list[dict[str, Any]]]) -> None:
    columns = ["batch_id", "stat_id", "matching_level", "condition", "feature", "metric", "distance_value", "distance_unit", "batch_n", "batch_dialogue_n", "reference_n", "reference_dialogue_n", "batch_value", "reference_value", "category_gaps", "comparability_notes"]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for batch_id, stats in stats_by_batch.items():
            for stat in stats:
                writer.writerow({
                    "batch_id": batch_id,
                    "stat_id": stat["stat_id"],
                    "matching_level": stat["matching_level"],
                    "condition": stat["condition"],
                    "feature": stat["feature"],
                    "metric": stat["distance"]["metric"],
                    "distance_value": stat["distance"]["value"],
                    "distance_unit": stat["distance"]["unit"],
                    "batch_n": stat["batch_n"],
                    "batch_dialogue_n": stat["batch_dialogue_n"],
                    "reference_n": stat["reference_n"],
                    "reference_dialogue_n": stat["reference_dialogue_n"],
                    "batch_value": json.dumps(stat["batch_value"], ensure_ascii=False),
                    "reference_value": json.dumps(stat["reference_value"], ensure_ascii=False),
                    "category_gaps": json.dumps(stat.get("category_gaps", {}), ensure_ascii=False),
                    "comparability_notes": stat["comparability_notes"],
                })


def compact_annotations(dialogues: list[Dialogue], indexed: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Keep only the labels needed to audit the selected dialogue texts."""
    return [
        {
            "dialogue_id": d.dialogue_id,
            "messages": [
                {"message_id": m["message_id"], "status": m["status"], "context": m["context"], "features": m["features"]}
                for m in indexed[d.dialogue_id]
            ],
        }
        for d in dialogues
    ]


def make_batch_jobs(dialogues: list[Dialogue], real: list[Dialogue], model: str, batch_size: int, stats_by_batch: dict[str, list[dict[str, Any]]], target_population: str, comparison_setup: str, sim_labels: dict[str, list[dict[str, Any]]] | None = None, real_labels: dict[str, list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
    jobs = []
    # The displayed real dialogues/labels are a bounded audit sample; the
    # external statistics are computed from the full real reference set.
    for i, group in enumerate(group_chunks(dialogues, batch_size), 1):
        batch_id = f"batch_{i:03d}"
        case_ids = {d.case_id for d in group}
        matched_real = [d for d in real if d.case_id in case_ids]
        reference_sample = matched_real[:batch_size]
        real_text = "\n".join(render_dialogue(d, include_outcome=False) for d in reference_sample)
        setup = comparison_setup + ("；展示的真人原文只是同案例参照样例，外部统计使用全部同案例真人对话" if matched_real else "；本批没有同 case_id 真人对话")
        parts = ["# 评测补充信息", f"- batch_id: {batch_id}", f"- comparison_setup: {setup}", f"- target_population: {target_population}", "", "# 待评价对话", "", "\n".join(render_dialogue(d, include_outcome=False) for d in group)]
        if matched_real:
            parts += ["", "# 真实参照对话", "", real_text]
            if sim_labels is not None and real_labels is not None:
                labels = {
                    "schema_version": "expression_features_v1",
                    "batch": compact_annotations(group, sim_labels),
                    "reference_sample": compact_annotations(reference_sample, real_labels),
                    "note": "batch 覆盖本次全部模拟对话；reference_sample 仅覆盖上面展示的真人原文；外部统计使用完整真人集合。",
                }
                parts += ["", "# 表达特征标注", "", json.dumps(labels, ensure_ascii=False)]
            if stats_by_batch.get(batch_id):
                # Full contributing IDs stay in the audit file; repeating every
                # reference message for every feature would inflate model input.
                prompt_stats = [{k: v for k, v in stat.items() if k not in {"batch_messages", "reference_messages"}} for stat in stats_by_batch[batch_id]]
                parts += ["", "# 外部统计", "", json.dumps(prompt_stats, ensure_ascii=False)]
        batch_job = job("expression_realism_batch", batch_id, {"{{INPUT_MARKDOWN}}": "\n".join(parts)}, model)
        batch_job["b3_stat_ids"] = [stat["stat_id"] for stat in stats_by_batch.get(batch_id, [])]
        batch_job["has_real_reference"] = bool(matched_real)
        batch_job["batch_dialogue_count"] = len(group)
        batch_job["has_target_population"] = target_population.strip().lower() not in {"", "未提供", "未知", "unknown"}
        jobs.append(batch_job)
    return jobs


def summarize(output: Path, jobs: list[dict[str, Any]], skipped: list[dict[str, str]], preview: bool = False, selection: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = []
    for item in jobs:
        path = output / "results" / (item["job_id"] + ".json")
        result = read_json(path) if path.exists() and not preview else {}
        body = result.get("result", {})
        rows.append({"kind": item["kind"], "label": item["label"], "job_id": item["job_id"], "run_status": result.get("status", "not_run"), "assessment_status": body.get("assessment_status", ""), "score": body.get("score", body.get("overall_score", "")), "B1": body.get("B1", {}).get("status", ""), "B2": body.get("B2", {}).get("status", ""), "B3": body.get("B3", {}).get("status", ""), "checks": json.dumps(body.get("checks", {}), ensure_ascii=False), "summary": body.get("summary", ""), "limitations": json.dumps(body.get("limitations", []), ensure_ascii=False), "error": result.get("error", "")})
        for prefix, count in (("T", 3), ("D", 3), ("R", 3), ("L", 4)):
            for i in range(1, count + 1):
                key = f"{prefix}{i}"
                check = body.get("checks", {}).get(key, {})
                rows[-1][key + "_score"] = check.get("score")
                rows[-1][key + "_status"] = check.get("status", "")
                rows[-1][key + "_reason"] = check.get("reason", "")
    counts = Counter(f"{x['kind']}:{x['run_status']}" for x in rows)
    summary = {"job_count": len(rows), "status_counts": dict(sorted(counts.items())), "skipped": skipped, "note": "各入口分数不相加；batch 三项是诊断状态，不是分数。未运行与失败不等于0分。"}
    summary["assessment_counts"] = dict(Counter(f"{r['kind']}:{r['assessment_status']}" for r in rows if r["assessment_status"]))
    summary["batch_assessment_counts"] = {k: dict(Counter(r[k] for r in rows if r[k])) for k in ("B1", "B2", "B3")}
    summary["interpretation_warning"] = "completed/退出码0仅代表程序成功处理任务；partial、insufficient_evidence、not_applicable、descriptive_only和跳过项均不能解释为已通过评测。请同时查看 checks、limitations 和 comparison_coverage.json。"
    if selection is not None:
        summary["selection"] = selection
    write_json(output / "summary.json", summary)
    with (output / "results.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]) if rows else ["kind", "label", "job_id", "run_status", "assessment_status", "score", "B1", "B2", "B3", "error"])
        writer.writeheader()
        writer.writerows(rows)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用户模拟器评测：读取对话与案例、调用兼容 Chat Completions 的模型、输出结果")
    parser.add_argument("--dialogues", type=Path, help="模拟对话文件或目录；默认从运行路径配置读取")
    parser.add_argument("--cases", type=Path, help="案例文件或目录；默认从运行路径配置读取")
    parser.add_argument("--real-dialogues", type=Path, help="可选真人对话文件或目录，供 B3 对照")
    parser.add_argument("--output", type=Path, help="结果目录；默认从运行路径配置读取")
    parser.add_argument("--model", default=os.environ.get("EVAL_MODEL", ""), help="评测模型名；也可设 EVAL_MODEL")
    parser.add_argument("--base-url", default=os.environ.get("EVAL_BASE_URL", ""), help="兼容 OpenAI Chat Completions 的 /v1 地址；也可设 EVAL_BASE_URL")
    parser.add_argument("--api-key-env", default="EVAL_API_KEY", help="保存 API key 的环境变量名；本地无认证服务可不设")
    parser.add_argument("--api-config", type=Path, help="本机 API 配置 JSON；默认读取 eval_pipeline/.local_api.json（若存在）")
    parser.add_argument("--run-config", type=Path, help="输入与输出路径配置 JSON；默认读取 eval_pipeline/run_config.json")
    parser.add_argument("--start-dialogue", type=int, help="从第几段模拟对话开始，按输入文件顺序，第一段为 1")
    parser.add_argument("--limit-dialogues", type=int, help="本次最多评多少段模拟对话；与 --batch-size 不同")
    parser.add_argument("--batch-size", type=int, default=8, help="每个 Batch 任务包含多少段模拟对话")
    parser.add_argument("--annotation-batch-size", type=int, default=4, help="每个标注任务包含多少段对话")
    parser.add_argument("--min-comparison-n", type=int, default=5, help="B3 单个比较条件每组最少的 USER 消息数")
    parser.add_argument("--target-population", default="未提供", help="B2 目标用户及覆盖要求；未知则只描述")
    parser.add_argument("--comparison-setup", default="未核验；需检查案例、助手、开头和生成设置差异", help="两组可比性说明")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true", help="生成任务清单，不调用模型")
    parser.add_argument("--force", action="store_true", help="忽略成功缓存重新调用；重复性试验应分别使用新的输出目录")
    args = parser.parse_args(argv)
    try:
        config = read_api_config(args.api_config or Path(__file__).resolve().parent / ".local_api.json", args.api_config is not None)
        paths = read_run_config(args.run_config or Path(__file__).resolve().parent / "run_config.json", args.run_config is not None)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(f"配置错误：{exc}")
    args.model = args.model or config.get("model", "")
    args.base_url = args.base_url or config.get("base_url", "")
    use_config_real = args.dialogues is None and args.cases is None and args.real_dialogues is None
    use_config_selection = args.dialogues is None and args.cases is None
    args.dialogues = args.dialogues or paths.get("dialogues")
    args.cases = args.cases or paths.get("cases")
    args.output = args.output or paths.get("output")
    if args.start_dialogue is None:
        args.start_dialogue = paths.get("start_dialogue") if use_config_selection else None
    args.start_dialogue = args.start_dialogue or 1
    if args.limit_dialogues is None and use_config_selection:
        args.limit_dialogues = paths.get("limit_dialogues")
    real_from_config = use_config_real and "real_dialogues" in paths
    if use_config_real:
        args.real_dialogues = paths.get("real_dialogues")
    if not args.dialogues or not args.cases or not args.output:
        parser.error("缺少模拟对话、案例或输出路径；请设置 eval_pipeline/run_config.json 或使用 --dialogues、--cases、--output")
    if real_from_config and (not args.real_dialogues.exists() or not list_files(args.real_dialogues)):
        print(f"真人对话路径尚无可读取文件，本次不执行 B3 真人对比：{args.real_dialogues}", file=sys.stderr)
        args.real_dialogues = None
    if not args.model:
        parser.error("缺少 --model 或 EVAL_MODEL")
    if not args.dry_run and not args.base_url:
        parser.error("缺少 --base-url 或 EVAL_BASE_URL")
    if min(args.batch_size, args.annotation_batch_size, args.min_comparison_n) < 1 or args.retries < 0 or args.timeout <= 0:
        parser.error("批量大小和最小样本数必须为正数，重试次数不能为负")
    if args.start_dialogue < 1 or args.limit_dialogues is not None and args.limit_dialogues < 1:
        parser.error("--start-dialogue 和 --limit-dialogues 必须是正整数")
    try:
        output_root = args.output.resolve()
        for source in (args.dialogues, args.cases, args.real_dialogues):
            if source and source.is_dir() and output_root.is_relative_to(source.resolve()):
                raise ValueError("输出目录不能位于对话、案例或真人对话输入目录内")
        all_dialogues = load_dialogues(args.dialogues)
        if args.start_dialogue > len(all_dialogues):
            raise ValueError(f"起始序号 {args.start_dialogue} 超出模拟对话总数 {len(all_dialogues)}")
        start_index = args.start_dialogue - 1
        dialogues = all_dialogues[start_index:start_index + args.limit_dialogues if args.limit_dialogues else None]
        end_index = start_index + len(dialogues)
        selection = {"total_dialogues": len(all_dialogues), "start_dialogue": args.start_dialogue, "end_dialogue": end_index, "selected_count": len(dialogues), "limit_dialogues": args.limit_dialogues, "dialogue_ids": [d.dialogue_id for d in dialogues]}
        output = output_root / f"dialogues_{args.start_dialogue:05d}-{end_index:05d}" if args.limit_dialogues is not None or args.start_dialogue != 1 else output_root
        cases = load_cases(args.cases)
        wanted_cases = {d.case_id for d in dialogues}
        real, real_audit = load_matching_real_dialogues(args.real_dialogues, wanted_cases) if args.real_dialogues else ([], {"scanned": 0, "missing_case_id": 0, "matched": 0})
        if args.real_dialogues:
            print(f"真人对话已扫描 {real_audit['scanned']} 条；同 case_id 匹配 {real_audit['matched']} 条；缺少单一 caseID {real_audit['missing_case_id']} 条。", flush=True)
        if args.real_dialogues and not real:
            print("真人对话中没有与本批模拟对话相同的 case_id；本次不执行 B3 真人比较。", file=sys.stderr)
        if real and set(d.dialogue_id for d in real) & set(d.dialogue_id for d in dialogues):
            # Source sets are distinguished in prompt, but duplicate IDs complicate
            # joining annotation records and audit trails.
            raise ValueError("真人与模拟对话存在重复 dialogue_id，请先分别改为唯一编号")
        output.mkdir(parents=True, exist_ok=True)
        # Aggregates describe this run only. Keep per-job caches for resume.
        for name in ("summary.json", "results.csv", "annotations_simulated.json", "annotations_real.json", "feature_statistics.json", "feature_statistics.csv", "comparison_coverage.json", "case_evidence_index.json"):
            (output / name).unlink(missing_ok=True)
        api_key = os.environ.get(args.api_key_env) or config.get("api_key", "")
        core, skipped = make_core_jobs(dialogues, cases, args.model)
        manifest_base = {"dialogue_count": len(dialogues), "total_dialogue_count": len(all_dialogues), "case_count": len(cases), "real_dialogue_count": len(real), "real_reference_selection": {**real_audit, "case_ids": sorted(wanted_cases)}, "selection": selection}
        sim_ann = make_annotation_jobs(dialogues, args.model, args.annotation_batch_size, "sim") if real else []
        real_ann = make_annotation_jobs(real, args.model, args.annotation_batch_size, "real")
        case_jobs = make_case_jobs(dialogues, cases, args.model)
        jobs = case_jobs + core + sim_ann + real_ann
        requests_dir = output / "requests"
        requests_dir.mkdir(exist_ok=True)
        for item in jobs:
            (requests_dir / f"{item['job_id']}.txt").write_text(item["prompt"], encoding="utf-8")
        # Save requests locally for review. They may contain internal dialogue/case text.
        write_json(output / "manifest.json", {**manifest_base, "jobs": [{k: v for k, v in j.items() if k != "prompt" and not k.startswith("_")} for j in jobs], "skipped": skipped})
        if args.dry_run:
            preview = make_batch_jobs(dialogues, real, args.model, args.batch_size, {}, args.target_population, args.comparison_setup)
            jobs += preview
            for item in preview:
                (requests_dir / f"{item['job_id']}.txt").write_text(item["prompt"], encoding="utf-8")
            write_json(output / "manifest.json", {**manifest_base, "jobs": [{k: v for k, v in j.items() if k != "prompt" and not k.startswith("_")} for j in jobs], "skipped": skipped, "pending_leakage": [d.dialogue_id for d in dialogues if d.case_id in {j["label"] for j in case_jobs}], "note": "泄漏任务等待案例依据成功后生成；Batch任务为预览，真人统计完成后请求ID会变化"})
            summarize(output, jobs, skipped, preview=True, selection=selection)
            print(f"准备完成：{len(jobs)} 个任务，{len(skipped)} 个跳过；没有调用模型。结果：{output}")
            return 0
        # Shared across slices, versioned by case text, prompt, model and service.
        case_cache = output_root / "case_evidence" / digest(api_endpoint(args.base_url))[:16]
        case_ok, case_failed = execute_jobs(case_jobs, case_cache, args.base_url, api_key, args.timeout, args.retries, False, args.force)
        evidence = {}
        evidence_files = {}
        for item in case_jobs:
            path = case_cache / "results" / (item["job_id"] + ".json")
            record = read_json(path)
            write_json(output / "results" / path.name, record)
            evidence_files[item["label"]] = {"path": str(path), "job_id": item["job_id"], "status": record["status"]}
            if record["status"] == "completed":
                validate_job_result(item, record["result"])
                if record["result"]["assessment_status"] != "insufficient_evidence":
                    evidence[item["label"]] = record["result"]
        write_json(output / "case_evidence_index.json", evidence_files)
        core, skipped = make_core_jobs(dialogues, cases, args.model, evidence)
        jobs = case_jobs + core + sim_ann + real_ann
        for item in core:
            (requests_dir / f"{item['job_id']}.txt").write_text(item["prompt"], encoding="utf-8")
        write_json(output / "manifest.json", {**manifest_base, "case_evidence": evidence_files, "jobs": [{k: v for k, v in j.items() if k != "prompt" and not k.startswith("_")} for j in jobs], "skipped": skipped})
        manifest_base["case_evidence"] = evidence_files
        ok, failed = execute_jobs(core + sim_ann + real_ann, output, args.base_url, api_key, args.timeout, args.retries, False, args.force)
        ok += case_ok
        failed += case_failed
        stats_by_batch = {}
        coverage_by_batch = {}
        if real:
            a, b = annotations_for(sim_ann, output), annotations_for(real_ann, output)
            if a is None or b is None:
                skipped.append({"kind": "expression_realism_batch", "dialogue_id": "all", "reason": "真人对照标注未全部成功；修复后重跑，避免把 B3 误记为未评估"})
                write_json(output / "manifest.json", {**manifest_base, "jobs": [{k: v for k, v in j.items() if k != "prompt" and not k.startswith("_")} for j in jobs], "skipped": skipped})
                summarize(output, jobs, skipped, selection=selection)
                return 1
            sim_index = annotation_index(a)
            real_index = annotation_index(b)
            validate_annotation_set(sim_index, dialogues)
            validate_annotation_set(real_index, real)
            write_json(output / "annotations_simulated.json", a)
            write_json(output / "annotations_real.json", b)
            scenario_by_id = {d.dialogue_id: d.case_id for d in dialogues + real}
            for i, group in enumerate(group_chunks(dialogues, args.batch_size), 1):
                subset = [{"dialogue_id": d.dialogue_id, "messages": sim_index[d.dialogue_id]} for d in group]
                batch_cases = {d.case_id for d in group}
                matched_real = [{"dialogue_id": d.dialogue_id, "messages": real_index[d.dialogue_id]} for d in real if d.case_id in batch_cases]
                stats_by_batch[f"batch_{i:03d}"] = feature_statistics(subset, matched_real, args.min_comparison_n, args.comparison_setup, scenario_by_id)
                coverage_by_batch[f"batch_{i:03d}"] = comparison_coverage(subset, matched_real, stats_by_batch[f"batch_{i:03d}"], scenario_by_id)
            write_json(output / "feature_statistics.json", stats_by_batch)
            write_json(output / "comparison_coverage.json", coverage_by_batch)
            write_feature_statistics_csv(output / "feature_statistics.csv", stats_by_batch)
        batch_jobs = make_batch_jobs(dialogues, real, args.model, args.batch_size, stats_by_batch, args.target_population, args.comparison_setup, sim_index if real else None, real_index if real else None)
        jobs += batch_jobs
        for item in batch_jobs:
            (requests_dir / f"{item['job_id']}.txt").write_text(item["prompt"], encoding="utf-8")
        write_json(output / "manifest.json", {**manifest_base, "jobs": [{k: v for k, v in j.items() if k != "prompt" and not k.startswith("_")} for j in jobs], "skipped": skipped, "feature_stat_count": sum(len(v) for v in stats_by_batch.values())})
        more_ok, more_failed = execute_jobs(batch_jobs, output, args.base_url, api_key, args.timeout, args.retries, False, args.force)
        summary = summarize(output, jobs, skipped, selection=selection)
        print(f"完成 {ok+more_ok}，失败 {failed+more_failed}，跳过 {len(skipped)}；结果：{output}")
        return 1 if failed + more_failed else 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"输入错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
