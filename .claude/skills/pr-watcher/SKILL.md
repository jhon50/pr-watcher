---
name: pr-watcher
description: Operate the PR Watcher — a local dashboard that detects new PRs on a GitHub repo, runs headless Claude code reviews on demand, and lets you post findings, approve, and merge from one page. Use this skill when working in the pr-watcher repo: starting/stopping the UI, wiring it to a repo, installing the launchd agent, triggering or debugging a review, seeding test data, or explaining how any part works. Triggers on "start the pr watcher", "review PR N", "why is this PR stuck", "install the launchd agent", "configure pr-watcher for <repo>".
---

# PR Watcher

Local, single-user PR review dashboard. FastAPI backend + one-file Alpine/Tailwind
frontend + SQLite state. Watchers run inside the FastAPI process. Reviews and
merges are **user-triggered** and spawn `claude -p` headless.

## Architecture (read before changing anything)

- `backend/app.py` — FastAPI app: serves the UI, the JSON API, and starts the
  watcher loop in its lifespan. HEAD-selection logic lives in `_get_state()`.
- `backend/watchers.py` — background asyncio watchers (new PRs, follow-ups,
  stale-queue, stuck-review). Registered with `@register(name, interval)`.
- `backend/reviewer.py` — spawns `claude -p` with `reviewer_prompt.md` +
  `review_rules.md`; parses a `<FINDINGS>[...]</FINDINGS>` block; writes
  findings straight to SQLite. Also the "approve-if-addressed" verifier.
- `backend/merger.py` — merges your own PRs: a configured merge skill, else a
  plain `gh pr merge --squash --delete-branch`.
- `backend/gh.py` — thin `gh` CLI wrappers. Repo comes from `config.repo()`.
- `backend/config.py` — all repo/user config. Env (`PRW_*`) > `~/.pr-watcher/
  config.json` > `gh` auto-detection > default. **Nothing repo-specific lives
  anywhere else.**
- `backend/db.py` + `backend/schema.sql` — SQLite at `~/.pr-watcher/state.db`.
- `frontend/index.html` — the whole UI. Polls `/api/state` (5s) and
  `/api/my_prs` (60s); gets the repo slug from `/api/meta`.
- `prw` — CLI to seed/inspect the DB for testing.

## Config

Never hardcode a repo or login. Everything routes through `backend/config.py`:

- `repo()` — `PRW_REPO` / `config.json:repo` / `gh repo view` default.
- `self_login()` — `PRW_SELF_LOGIN` / `config.json:self_login` / `gh api user`.
- `merge_skill()` / `target_repo_dir()` — optional; drive merger.py.

To point it at a repo: set `PRW_REPO=owner/name` (and `PRW_SELF_LOGIN` if
auto-detect is wrong), or write `~/.pr-watcher/config.json` from
`config.example.json`.

## Run / stop

```bash
./run.sh                      # foreground, http://127.0.0.1:4747
./install-launchd.sh          # background via launchd, starts at login
launchctl bootout gui/$(id -u)/pr-watcher   # stop the launchd agent
```

Only one process can bind the port — don't run `./run.sh` and the launchd agent
at once.

## Common operations

- **Review a PR now**: `curl -X POST localhost:4747/api/prs/<N>/review` (or click
  Review in the UI). Reviews are never automatic.
- **Add a PR manually**: `./prw add-pr <N> <author> "<title>"`.
- **Trigger a watcher by hand**: `curl -X POST localhost:4747/api/watchers/<name>/run`.
- **Inspect state**: `./prw list` or `sqlite3 ~/.pr-watcher/state.db "SELECT number,status FROM prs"`.
- **Activity log for a PR**: `sqlite3 ~/.pr-watcher/state.db "SELECT * FROM activity_log WHERE pr_number=<N> ORDER BY id DESC LIMIT 20"`.

## Debugging a stuck PR

1. `sqlite3 ~/.pr-watcher/state.db "SELECT number,status,updated_at FROM prs"` —
   check the status.
2. Read the activity log (above) for the last action.
3. `reviewing` past ~15 min → the `stuck_reviews` watcher resets it; or restart
   the server (startup self-heal re-queues stranded rows).
4. `review_failed` → click Review again / re-POST to retry; check the log for
   `review_parse_failed` (Claude didn't emit a `<FINDINGS>` block).
5. Watcher errors show in the UI's watcher status and as `error: …` in
   `watcher_runs.last_result`.
6. Every review/merge fails with *"OAuth session expired and could not be
   refreshed"* / "Not logged in" → the launchd agent's `claude` can't see your
   auth. launchd doesn't inherit your shell env; if you use a non-default
   `CLAUDE_CONFIG_DIR` or `ANTHROPIC_API_KEY`, export it and re-run
   `./install-launchd.sh` (it bakes those into the plist). Running from a
   terminal (`./run.sh`) masks this because the shell env is inherited.

## Editing rules / prompts

The reviewer's judgement is governed entirely by `review_rules.md` (injected as
`{RULES}`). Edit that file, not the Python, to change what gets flagged. It's
git-ignored and seeded from `review_rules.example.md` on first run, so edits
never conflict on `git pull` — read fresh on every review (no restart needed). The
prompt templates use `{PR_NUMBER}`, `{REPO}`, `{SELF_LOGIN}`, `{RULES}`,
`{PREVIOUS_REVIEW_CONTEXT}`, `{POSTED_FINDINGS}` placeholders — keep them intact.

## Guardrails

- The reviewer **never approves** — it only surfaces findings for the user.
- Merges only *start* from the UI; pre-flight blockers (not approved, conflicts,
  CI red) still stop them.
