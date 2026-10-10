#!/usr/bin/env python3
"""Push study-pack JSON files (flashcards + notes) to Study Buddy. Stdlib only —
copy this one file into any project.

    export STUDYBOT_URL=https://your-server        # no trailing /api
    export STUDYBOT_TOKEN=...                      # personal token: web app -> Tokens tab
    python studybot_push.py pack.json [more.json ...]
    python studybot_push.py --dry-run pack.json    # report what would change, write nothing
    some-ai-output | python studybot_push.py -     # read JSON from stdin

Safe to re-run: cards are matched by question, notes by title, so pushing the
same file twice changes nothing and a corrected answer updates in place.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def push(url: str, token: str, pack: dict, dry_run: bool) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/import" + ("?dry_run=1" if dry_run else ""),
        data=json.dumps(pack).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "studybot-push/1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except ValueError:
            return e.code, {"error": e.reason}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", help="study-pack JSON files, or - for stdin")
    parser.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = parser.parse_args()

    url, token = os.environ.get("STUDYBOT_URL"), os.environ.get("STUDYBOT_TOKEN")
    if not url or not token:
        sys.exit("Set STUDYBOT_URL and STUDYBOT_TOKEN (token: web app -> Tokens tab).")

    failed = False
    for path in args.files:
        label = "stdin" if path == "-" else path
        try:
            pack = json.load(sys.stdin if path == "-" else open(path, encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"{label}: cannot read as JSON — {e}", file=sys.stderr)
            failed = True
            continue
        status, body = push(url, token, pack, args.dry_run)
        if status != 200:
            failed = True
            print(f"{label}: rejected ({status}) — {body.get('error', body)}", file=sys.stderr)
            for detail in body.get("details", []):
                print(f"  - {detail}", file=sys.stderr)
            continue
        c, n = body["cards"], body["notes"]
        print(f"{label}{' (dry run)' if args.dry_run else ''}: "
              f"cards {c['created']} new / {c['updated']} updated / {c['unchanged']} unchanged · "
              f"notes {n['created']} new / {n['updated']} updated / {n['unchanged']} unchanged")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
