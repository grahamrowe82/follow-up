"""Core logic for the T-009 Follow-Up Pack baseline slice."""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional

WEEKDAY_LOOKUP = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

SECTION_HEADER_PATTERN = re.compile(r"^(decisions?|actions?|questions?|risks?|next check-?in)[:\-]\s*", re.I)
ACTION_LINE_PATTERN = re.compile(
    r"^(?P<owner>[A-Za-z0-9 ._'/&-]+?)\s*[\u2013\-:\u2014]\s*(?P<action>.+?)(?:\s*\(due\s*(?P<due>[^\)]+)\))?\s*$",
    re.I,
)
DUE_INLINE_PATTERN = re.compile(r"\bby\s+(?P<due>today|tomorrow|next\s+\w+|\d{1,2}\s+\w+|\d{4}-\d{2}-\d{2}|\w+\s+\d{1,2})(?:\b|$)", re.I)
ISO_DATE_PATTERN = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})$")
DAY_MONTH_PATTERN = re.compile(r"^(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3,9})$")
TIME_PATTERN = re.compile(r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[ap]m)?", re.I)
BULLET_PREFIX_PATTERN = re.compile(r"^(?:[-*•]+|\d+[.)])\s*")


Snapshot = Dict[str, Any]
Action = Dict[str, Optional[str]]


def normalize_input(text_or_json: str) -> Snapshot:
    """Return a structured snapshot from raw text or JSON."""
    if text_or_json is None:
        raise ValueError("Input is required.")

    raw = text_or_json.strip()
    if not raw:
        return _empty_snapshot()

    if raw[0] in "{" or raw[0] in "[":
        return _normalize_from_json(raw)

    return _normalize_from_text(raw)


def group_by_owner(actions: Iterable[Mapping[str, Optional[str]]]) -> "OrderedDict[str, List[Dict[str, Optional[str]]]]":
    """Group normalized actions by owner while preserving order."""
    grouped: "OrderedDict[str, List[Dict[str, Optional[str]]]]" = OrderedDict()
    for action in actions:
        owner = (action.get("owner") or "TBD").strip() or "TBD"
        entry = {
            "action": (action.get("action") or "").strip(),
            "due": due_normalize(action.get("due")),
        }
        if owner not in grouped:
            grouped[owner] = []
        grouped[owner].append(entry)
    return grouped


def build_summary(snapshot: Snapshot, grouped: Mapping[str, List[Mapping[str, Optional[str]]]]) -> str:
    """Build the send-ready summary block."""
    lines: List[str] = []
    decisions: List[str] = [d.strip() for d in snapshot.get("decisions", []) if d and d.strip()]
    actions_total = sum(len(items) for items in grouped.values())

    lines.append("Decisions:")
    if decisions:
        for decision in decisions[:3]:
            lines.append(f"• {decision}")
        if len(decisions) > 3:
            lines.append(f"• +{len(decisions) - 3} more")
    else:
        lines.append("• —")

    lines.append("")
    lines.append("Actions by Owner:")
    if grouped and actions_total:
        listed_actions = 0
        for owner, items in grouped.items():
            if listed_actions >= 8:
                break
            visible_items: List[str] = []
            for item in items:
                if listed_actions >= 8:
                    break
                action_text = item.get("action") or "—"
                due = item.get("due") or "—"
                visible_items.append(f"{action_text} (due {due})")
                listed_actions += 1
            if visible_items:
                lines.append(f"• {owner} — " + "; ".join(visible_items))
        if actions_total > listed_actions:
            lines.append(f"• +{actions_total - listed_actions} more")
    else:
        lines.append("• —")

    lines.append("")
    next_checkin = snapshot.get("next_checkin")
    next_display = next_checkin.strip() if isinstance(next_checkin, str) and next_checkin.strip() else "— add"
    lines.append(f"Next check-in: {next_display}")

    return "\n".join(lines)


def pick_clarify(snapshot: Snapshot, grouped: Mapping[str, List[Mapping[str, Optional[str]]]]) -> List[str]:
    """Return two clarify questions following the gap-first heuristic."""
    clarifies: List[str] = []
    actions = snapshot.get("actions", [])

    for action in actions:
        owner = (action.get("owner") or "").strip() or "TBD"
        if owner == "TBD" and len(clarifies) < 2:
            text = (action.get("action") or "this action").strip() or "this action"
            clarifies.append(f"Who owns: {text}?")
            if len(clarifies) >= 2:
                break

    if len(clarifies) < 2:
        for action in actions:
            due = action.get("due")
            if not (isinstance(due, str) and due.strip()):
                text = (action.get("action") or "this action").strip() or "this action"
                clarifies.append(f"Due date for: {text}?")
            if len(clarifies) >= 2:
                break

    if len(clarifies) < 2:
        for question in snapshot.get("questions", []) or []:
            clean = question.strip()
            if clean:
                clarifies.append(clean)
            if len(clarifies) >= 2:
                break

    if len(clarifies) < 2:
        next_checkin = snapshot.get("next_checkin") or "— add"
        clarifies.append(f"Confirm next check-in ({next_checkin})?")

    if len(clarifies) < 2:
        clarifies.append("Anything else to clarify before next sync?")

    return clarifies[:2]


def due_normalize(raw_due: Optional[str], *, reference: Optional[datetime] = None) -> Optional[str]:
    """Normalize due date strings to a short human-readable form."""
    if raw_due is None:
        return None
    if not isinstance(raw_due, str):
        return None

    text = raw_due.strip()
    if not text:
        return None

    lower = text.lower()
    today = reference or datetime.now()

    if lower in {"today", "by today"}:
        return "Today"
    if lower in {"tomorrow", "tmr", "by tomorrow"}:
        return "Tomorrow"

    iso_match = ISO_DATE_PATTERN.match(text)
    if iso_match:
        year = int(iso_match.group("year"))
        month = int(iso_match.group("month"))
        day = int(iso_match.group("day"))
        try:
            parsed = datetime(year, month, day)
        except ValueError:
            return text
        if parsed.year == today.year:
            return parsed.strftime("%d %b")
        return parsed.strftime("%d %b %Y")

    day_month = DAY_MONTH_PATTERN.match(text)
    if day_month:
        day = int(day_month.group("day"))
        month_name = day_month.group("month")
        try:
            parsed = datetime.strptime(f"{day} {month_name} {today.year}", "%d %B %Y")
        except ValueError:
            try:
                parsed = datetime.strptime(f"{day} {month_name} {today.year}", "%d %b %Y")
            except ValueError:
                return text
        if parsed < today and parsed.year == today.year:
            parsed = parsed.replace(year=today.year + 1)
        if parsed.year == today.year:
            return parsed.strftime("%d %b")
        return parsed.strftime("%d %b %Y")

    if lower.startswith("next "):
        remainder = lower.split(" ", 1)[1]
        weekday_index = WEEKDAY_LOOKUP.get(remainder)
        if weekday_index is not None:
            days_ahead = (weekday_index - today.weekday() + 7) % 7 or 7
            target = today + timedelta(days=days_ahead)
            return f"Next {target.strftime('%a')}"

    parts = lower.split()
    if parts and parts[0] in WEEKDAY_LOOKUP:
        weekday_index = WEEKDAY_LOOKUP[parts[0]]
        days_ahead = (weekday_index - today.weekday() + 7) % 7 or 7
        target = today + timedelta(days=days_ahead)
        if len(parts) > 1:
            time_part = "".join(parts[1:])
            time_match = TIME_PATTERN.fullmatch(time_part)
            if time_match:
                time_display = _format_time(time_match)
                return f"{target.strftime('%a')} {time_display}".strip()
        return target.strftime("%a")

    weekday_index = WEEKDAY_LOOKUP.get(lower)
    if weekday_index is not None:
        days_ahead = (weekday_index - today.weekday() + 7) % 7 or 7
        target = today + timedelta(days=days_ahead)
        return target.strftime("%a")

    time_match = TIME_PATTERN.fullmatch(lower.replace(" ", ""))
    if time_match:
        return _format_time(time_match)

    return text


def _format_time(match: re.Match) -> str:
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    ampm = match.group("ampm")
    specified_ampm = ampm.lower() if ampm else None
    if specified_ampm:
        if specified_ampm == "pm" and hour != 12:
            hour += 12
        if specified_ampm == "am" and hour == 12:
            hour = 0
    if hour >= 24 or minute >= 60:
        return match.group(0)

    suffix = "am" if (specified_ampm == "am" or (specified_ampm is None and hour < 12)) else "pm"
    display_hour = hour % 12 or 12
    if minute:
        time_body = f"{display_hour}:{minute:02d}"
    else:
        time_body = f"{display_hour}"
    return f"{time_body}{suffix}"


def _empty_snapshot() -> Snapshot:
    return {
        "decisions": [],
        "actions": [],
        "questions": [],
        "risks": [],
        "next_checkin": "",
    }


def _normalize_from_json(raw: str) -> Snapshot:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON payload.") from exc

    if isinstance(payload, dict):
        snapshot = _empty_snapshot()
        snapshot["decisions"] = _ensure_list_of_strings(payload.get("decisions"))
        snapshot["actions"] = _normalize_actions(payload.get("actions"))
        snapshot["questions"] = _ensure_list_of_strings(payload.get("questions"))
        snapshot["risks"] = _ensure_list_of_strings(payload.get("risks"))
        snapshot["next_checkin"] = _ensure_string(payload.get("next_checkin"))
        return snapshot

    raise ValueError("JSON input must be an object with meeting fields.")


def _normalize_from_text(raw: str) -> Snapshot:
    snapshot = _empty_snapshot()
    sections = _split_sections(raw)
    snapshot["decisions"] = sections.get("decisions", [])
    snapshot["questions"] = sections.get("questions", [])
    snapshot["risks"] = sections.get("risks", [])
    snapshot["next_checkin"] = sections.get("next_checkin", "")
    snapshot["actions"] = sections.get("actions", [])
    return snapshot


def _split_sections(text: str) -> Dict[str, Any]:
    sections: Dict[str, Any] = {
        "decisions": [],
        "actions": [],
        "questions": [],
        "risks": [],
        "next_checkin": "",
    }

    current_key: Optional[str] = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        header_match = SECTION_HEADER_PATTERN.match(line)
        if header_match:
            key = header_match.group(1).lower()
            if key.startswith("decision"):
                current_key = "decisions"
            elif key.startswith("action"):
                current_key = "actions"
            elif key.startswith("question"):
                current_key = "questions"
            elif key.startswith("risk"):
                current_key = "risks"
            else:
                current_key = "next_checkin"
            remainder = SECTION_HEADER_PATTERN.sub("", line).strip()
            if current_key == "next_checkin":
                sections[current_key] = remainder or sections[current_key]
            elif remainder:
                if current_key == "actions":
                    sections[current_key].append(_parse_action_line(remainder))
                else:
                    sections[current_key].append(_clean_bullet(remainder))
            continue

        if current_key == "actions":
            sections[current_key].append(_parse_action_line(line))
        elif current_key == "next_checkin":
            sections[current_key] = line
        elif current_key in sections and current_key is not None:
            sections[current_key].append(_clean_bullet(line))
        else:
            # Fallback: try to interpret standalone action lines.
            if ACTION_LINE_PATTERN.match(line):
                sections["actions"].append(_parse_action_line(line))
            else:
                sections.setdefault("notes", []).append(line)

    return sections


def _parse_action_line(line: str) -> Action:
    match = ACTION_LINE_PATTERN.match(line)
    if match:
        owner = match.group("owner")
        action = match.group("action")
        due = match.group("due")
    else:
        owner = ""
        action = line
        due = None
        inline_match = DUE_INLINE_PATTERN.search(line)
        if inline_match:
            due = inline_match.group("due")
            action = DUE_INLINE_PATTERN.sub("", line).strip()

    return {"owner": owner or "TBD", "action": action.strip(), "due": due}


def _ensure_list_of_strings(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        strings: List[str] = []
        for item in value:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    strings.append(stripped)
        return strings
    return []


def _normalize_actions(value: Any) -> List[Action]:
    actions: List[Action] = []
    if isinstance(value, Mapping):
        # Single action as dict.
        value = [value]
    if isinstance(value, Iterable):
        for item in value:
            if isinstance(item, Mapping):
                action_text = _ensure_string(item.get("action"))
                owner = _ensure_string(item.get("owner")) or "TBD"
                due = _ensure_string(item.get("due"))
                if action_text:
                    actions.append({"action": action_text, "owner": owner or "TBD", "due": due})
    return actions


def _ensure_string(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _clean_bullet(value: str) -> str:
    return BULLET_PREFIX_PATTERN.sub("", value).strip()

