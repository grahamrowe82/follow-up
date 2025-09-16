"""Flask entrypoint for the T-009 Follow-Up Pack baseline slice."""
from __future__ import annotations

from typing import Any, Dict

from flask import Flask, render_template, request

from .logic import build_summary, group_by_owner, normalize_input, pick_clarify

app = Flask(__name__)

TEXT_LIMIT = 8000
JSON_LIMIT = 4000


@app.route("/", methods=["GET"])
def index() -> str:
    return render_template(
        "index.html",
        input_text="",
        result=None,
        error=None,
        text_limit=TEXT_LIMIT,
        json_limit=JSON_LIMIT,
    )


@app.route("/pack", methods=["POST"])
def build_pack() -> str:
    raw_input = request.form.get("meeting_input", "")
    error = None
    result: Dict[str, Any] | None = None

    if len(raw_input) > TEXT_LIMIT:
        error = f"Input exceeds {TEXT_LIMIT} characters."
    else:
        stripped = raw_input.strip()
        is_json = bool(stripped and stripped[0] in "{[")
        if is_json and len(raw_input) > JSON_LIMIT:
            error = f"JSON input exceeds {JSON_LIMIT} characters."
        else:
            try:
                snapshot = normalize_input(raw_input)
            except ValueError as exc:
                error = str(exc)
            else:
                grouped = group_by_owner(snapshot.get("actions", []))
                summary_block = build_summary(snapshot, grouped)
                clarifies = pick_clarify(snapshot, grouped)
                result = {
                    "snapshot": snapshot,
                    "grouped": grouped,
                    "grouped_items": list(grouped.items()),
                    "summary": summary_block,
                    "clarifies": clarifies,
                    "actions_total": len(snapshot.get("actions", [])),
                }

    return render_template(
        "index.html",
        input_text=raw_input,
        result=result,
        error=error,
        text_limit=TEXT_LIMIT,
        json_limit=JSON_LIMIT,
    )


if __name__ == "__main__":
    app.run(debug=True)
