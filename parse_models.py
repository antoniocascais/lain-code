#!/usr/bin/env python3
"""Parse Claude Code JSONL conversation logs and report model usage stats."""

import os, json, glob, sys
from datetime import datetime, timedelta
from collections import defaultdict

DEFAULT_BASE = os.path.expanduser("~/.claude/projects/")

def parse_args():
    """Parse: [--dir PATH] [--date YYYYMMDD]. Defaults: yesterday, ~/.claude/projects/."""
    target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    base = DEFAULT_BASE
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--dir" and i + 1 < len(args):
            base = os.path.abspath(args[i + 1])
            i += 2
        elif args[i] == "--date" and i + 1 < len(args):
            raw = args[i + 1].replace("-", "")
            target_date = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
            i += 2
        else:
            i += 1
    return target_date, base

def main():
    target_date, base = parse_args()
    print(f"Target date: {target_date}")
    print(f"Scanning:    {base}\n")

    models = defaultdict(int)
    files_matched = 0
    lines_total = 0
    sessions = set()

    for f in glob.glob(os.path.join(base, "**", "*.jsonl"), recursive=True):
        mod_date = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d")
        if mod_date != target_date:
            continue

        files_matched += 1
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                lines_total += 1
                try:
                    obj = json.loads(line)
                    sid = obj.get("sessionId", "")
                    if sid:
                        sessions.add(sid)
                    msg = obj.get("message", {})
                    if isinstance(msg, dict):
                        model = msg.get("model", "")
                        if model:
                            models[model] += 1
                except json.JSONDecodeError:
                    pass

    total = sum(models.values())
    print(f"Files matched:    {files_matched}")
    print(f"Sessions:         {len(sessions)}")
    print(f"JSONL lines:      {lines_total}")
    print(f"Model API calls:  {total}")
    print()

    if not models:
        print("No model data found.")
        return

    print(f"{'Model':<55} {'Count':>6}  {'%':>6}")
    print("-" * 72)
    for m, c in sorted(models.items(), key=lambda x: -x[1]):
        pct = c / total * 100
        print(f"  {m:<53} {c:>6}  {pct:>5.1f}%")
    print("-" * 72)
    print(f"  {'TOTAL':<53} {total:>6}  100.0%")

if __name__ == "__main__":
    main()
