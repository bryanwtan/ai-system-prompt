#!/usr/bin/env python3
"""Scan assistant output for banned lexical patterns.

Host-agnostic contract:
  stdin  = the text to scan (or a file path via argv)
  stdout = report, text or JSON per --format
  exit 0 = clean, exit 1 = violations at or above the failure threshold

Adapters translate this into whatever envelope their host speaks.
"""

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_RULES = Path(__file__).with_name("rules.json")
SEVERITIES = ("warn", "error")

IGNORE_LINE = re.compile(r"lexical-guard:\s*ignore(?!-file)", re.I)
IGNORE_FILE = re.compile(r"lexical-guard:\s*ignore-file", re.I)


def mask_code(text):
    """Blank out code and URLs, preserving offsets so line/col stay accurate."""
    chars = list(text)
    offset = 0
    in_fence = False
    fence = None

    for line in text.split("\n"):
        stripped = line.lstrip()
        marker = stripped[:3]
        is_fence = marker in ("```", "~~~")

        if is_fence and not in_fence:
            in_fence, fence = True, marker
        elif is_fence and marker == fence:
            in_fence, fence = False, None
        elif not in_fence:
            offset += len(line) + 1
            continue

        for i in range(offset, offset + len(line)):
            chars[i] = " "
        offset += len(line) + 1

    masked = "".join(chars)
    blank = lambda m: " " * len(m.group(0))
    masked = re.sub(r"`[^`\n]*`", blank, masked)
    masked = re.sub(r"https?://\S+", blank, masked)
    masked = re.sub(r"\[[^\]\n]*\]\([^)\n]*\)", blank, masked)
    return masked


def load_rules(path):
    data = json.loads(Path(path).read_text())
    compiled = []
    for rule in data.get("rules", []):
        compiled.append(
            {
                "id": rule["id"],
                "severity": rule.get("severity", "error"),
                "message": rule.get("message", ""),
                "fix": rule.get("fix", ""),
                "patterns": [re.compile(p, re.I) for p in rule["patterns"]],
                "allow": [re.compile(p, re.I) for p in rule.get("allow", [])],
            }
        )
    compiled.extend(load_banned(data.get("banned") or []))
    return compiled


def load_banned(entries):
    """Flat literal ban list. Entries match anywhere; anchor with spaces."""
    pats = [re.escape(s) for s in entries if s.strip()]
    if not pats:
        return []
    return [
        {
            "id": "banned",
            "severity": "error",
            "message": "Banned phrase.",
            "fix": "Rephrase it.",
            "patterns": [re.compile(p, re.I) for p in pats],
            "allow": [],
        }
    ]


def scan(text, rules, disabled=()):
    if IGNORE_FILE.search(text):
        return []

    source_lines = text.split("\n")
    exempt = {i + 1 for i, line in enumerate(source_lines) if IGNORE_LINE.search(line)}

    masked = mask_code(text)
    line_starts = [0]
    for i, ch in enumerate(masked):
        if ch == "\n":
            line_starts.append(i + 1)

    def position(index):
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= index:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1, index - line_starts[lo] + 1

    findings = []
    seen = set()
    for rule in rules:
        if rule["id"] in disabled:
            continue
        for pattern in rule["patterns"]:
            for match in pattern.finditer(masked):
                window = masked[max(0, match.start() - 24) : match.end() + 24]
                if any(a.search(window) for a in rule["allow"]):
                    continue
                key = (rule["id"], match.start())
                if key in seen:
                    continue
                line, col = position(match.start())
                if line in exempt:
                    continue
                seen.add(key)
                findings.append(
                    {
                        "rule": rule["id"],
                        "severity": rule["severity"],
                        "line": line,
                        "column": col,
                        "match": match.group(0).strip(),
                        "message": rule["message"],
                        "fix": rule["fix"],
                    }
                )

    findings.sort(key=lambda f: (f["line"], f["column"], f["rule"]))
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", help="file to scan; omit to read stdin")
    parser.add_argument("--rules", default=str(DEFAULT_RULES))
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--fail-on",
        choices=SEVERITIES,
        default="error",
        help="minimum severity that produces exit code 1",
    )
    parser.add_argument(
        "--disable", default="", help="comma-separated rule ids to skip"
    )
    args = parser.parse_args()

    text = Path(args.file).read_text() if args.file else sys.stdin.read()
    disabled = {r.strip() for r in args.disable.split(",") if r.strip()}
    findings = scan(text, load_rules(args.rules), disabled)

    threshold = SEVERITIES.index(args.fail_on)
    blocking = [f for f in findings if SEVERITIES.index(f["severity"]) >= threshold]

    if args.format == "json":
        print(
            json.dumps(
                {
                    "clean": not blocking,
                    "counts": {
                        s: sum(1 for f in findings if f["severity"] == s)
                        for s in SEVERITIES
                    },
                    "findings": findings,
                },
                indent=2,
            )
        )
    else:
        for f in findings:
            print(
                f"{f['line']}:{f['column']} {f['severity']} {f['rule']}: "
                f"\"{f['match']}\" {f['fix']}"
            )
        if not findings:
            print("clean")

    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
