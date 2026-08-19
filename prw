#!/usr/bin/env python3
"""prw — CLI to seed and inspect the PR watcher DB. Useful for testing.

Usage:
  prw list
  prw add-pr <number> <author> <title> [url]
  prw add-finding <pr> <severity> <title> --message=... [--file=...] [--line=...]
  prw approve <number>
  prw dismiss <number>
"""
import argparse
import json
import os
import sys
import urllib.request


BASE = f"http://localhost:{os.environ.get('PRW_PORT', '4747')}"
REPO = os.environ.get("PRW_REPO", "OWNER/REPO")


def post(path, body=None):
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(
        BASE + path, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def get(path):
    with urllib.request.urlopen(BASE + path) as r:
        return json.loads(r.read())


def cmd_list(_):
    s = get("/api/state")
    print(json.dumps(s, indent=2))


def cmd_add_pr(a):
    body = {
        "number": a.number,
        "author": a.author,
        "title": a.title,
        "url": a.url or f"https://github.com/{REPO}/pull/{a.number}",
        "head_sha": a.head_sha,
    }
    print(post("/api/prs", body))


def cmd_add_finding(a):
    body = [{
        "severity": a.severity,
        "title": a.title,
        "message": a.message,
        "file": a.file,
        "line": a.line,
        "code_snippet": a.code_snippet,
        "blast_radius": a.blast_radius,
        "confidence": a.confidence,
        "fix": a.fix,
        "suggestion_body": a.suggestion_body,
    }]
    print(post(f"/api/prs/{a.pr}/findings", body))


def cmd_approve(a):
    print(post(f"/api/prs/{a.number}/approve"))


def cmd_dismiss(a):
    print(post(f"/api/prs/{a.number}/dismiss"))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(required=True)

    s = sub.add_parser("list"); s.set_defaults(fn=cmd_list)

    s = sub.add_parser("add-pr")
    s.add_argument("number", type=int)
    s.add_argument("author")
    s.add_argument("title")
    s.add_argument("url", nargs="?", default=None)
    s.add_argument("--head-sha", default=None)
    s.set_defaults(fn=cmd_add_pr)

    s = sub.add_parser("add-finding")
    s.add_argument("pr", type=int)
    s.add_argument("severity", choices=["critical", "important", "suggestion"])
    s.add_argument("title")
    s.add_argument("--message", required=True)
    s.add_argument("--file", default=None)
    s.add_argument("--line", type=int, default=None)
    s.add_argument("--code-snippet", default=None)
    s.add_argument("--blast-radius", default=None)
    s.add_argument("--confidence", default=None)
    s.add_argument("--fix", default=None)
    s.add_argument("--suggestion-body", default=None)
    s.set_defaults(fn=cmd_add_finding)

    s = sub.add_parser("approve"); s.add_argument("number", type=int); s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("dismiss"); s.add_argument("number", type=int); s.set_defaults(fn=cmd_dismiss)

    args = ap.parse_args()
    try:
        args.fn(args)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
