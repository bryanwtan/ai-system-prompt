#!/usr/bin/env python3
"""Cursor adapter for lexical-guard.

Translates Cursor's hook envelope into the host-agnostic check.py contract.

Usage (from hooks.json):
    python3 <path>/cursor_hook.py audit     # afterAgentResponse: log only
    python3 <path>/cursor_hook.py enforce   # stop: request a rewrite

Cursor's payload field names are not contractually stable across events, so the
text is located by probing known keys and falling back to the longest string in
the payload. Every payload is appended to the log for inspection.
"""

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECK = HERE.parent.parent / "check.py"
RULES = HERE.parent.parent / "rules.json"
LOG = Path(os.environ.get("LEXICAL_GUARD_LOG", Path.home() / ".cursor/lexical-guard.log"))

TEXT_KEYS = (
    "response",
    "agent_response",
    "assistant_message",
    "message",
    "text",
    "content",
    "output",
    "final_message",
)

sys.path.insert(0, str(CHECK.parent))
import check  # noqa: E402


def find_text(payload):
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return ""

    for key in TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            nested = find_text(value)
            if nested:
                return nested

    longest = ""
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str) and len(node) > len(longest):
            longest = node
    return longest if len(longest) > 40 else ""


def log(record):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "audit"
    raw = sys.stdin.read()

    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = raw

    text = find_text(payload)
    if not text:
        log({"mode": mode, "status": "no-text", "payload": payload})
        print("{}")
        return 0

    findings = check.scan(text, check.load_rules(RULES))
    errors = [f for f in findings if f["severity"] == "error"]
    log(
        {
            "mode": mode,
            "chars": len(text),
            "errors": len(errors),
            "warns": len(findings) - len(errors),
            "findings": findings,
        }
    )

    if mode == "enforce" and errors:
        lines = "\n".join(
            f"- line {f['line']}: \"{f['match']}\" ({f['rule']}) {f['fix']}"
            for f in errors[:8]
        )
        print(
            json.dumps(
                {
                    "followup_message": (
                        "lexical-guard found style violations in your last "
                        f"message:\n{lines}\n"
                        "Reissue that message with these corrected. Change wording "
                        "only; do not redo the work or add commentary about this fix."
                    )
                }
            )
        )
        return 0

    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
