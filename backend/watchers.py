"""Background watcher loop. Runs inside the FastAPI process as asyncio tasks."""
import asyncio
import subprocess
from datetime import datetime, timedelta

from . import config, db, gh

SELF_LOGIN = config.self_login()
SKIP_AUTHORS = config.skip_authors()

# (name, interval_seconds, handler)
WATCHERS = []


def register(name, interval_seconds):
    def deco(fn):
        WATCHERS.append((name, interval_seconds, fn))
        return fn
    return deco


def _upsert_run(name, result, interval):
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    nxt = (datetime.utcnow() + timedelta(seconds=interval)).isoformat(timespec="seconds") + "Z"
    with db.conn() as c:
        c.execute(
            """INSERT INTO watcher_runs (name, last_run_at, next_run_at, last_result, interval_seconds)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 last_run_at=excluded.last_run_at,
                 next_run_at=excluded.next_run_at,
                 last_result=excluded.last_result,
                 interval_seconds=excluded.interval_seconds""",
            (name, now, nxt, result, interval),
        )


def notify(msg):
    # macOS notifications disabled per user request — UI/status block is the visible signal.
    return


# --- New PRs watcher -------------------------------------------------------
@register("new_prs", 1200)
async def check_new_prs():
    prs = gh.list_open_prs()
    new_count = 0
    with db.conn() as c:
        known = {r["number"] for r in c.execute("SELECT number FROM prs").fetchall()}
    for p in prs:
        if p["isDraft"]:
            continue
        author = p["author"]["login"]
        if author in SKIP_AUTHORS:
            continue
        if p["number"] in known:
            continue
        # Skip already-approved PRs
        try:
            if gh.approvals_count(p["number"]) > 0:
                continue
        except Exception:
            continue
        with db.conn() as c:
            c.execute(
                """INSERT INTO prs (number, author, title, url, head_sha, status)
                   VALUES (?, ?, ?, ?, ?, 'queued')""",
                (p["number"], author, p["title"], p["url"], p["headRefOid"]),
            )
        db.log_action(p["number"], "detected", f"by {author}")
        new_count += 1
        # Reviews are user-triggered only — do NOT kick off a review here.
        # The PR sits as 'queued' in the UI until the user clicks Review.
        notify(f"New PR #{p['number']} — click Review to start")
    return f"{new_count} new" if new_count else "none"


# --- Stale-queue watcher ---------------------------------------------------
# Queued PRs aren't covered by the follow-up watcher (which only looks at
# awaiting_user/pending_author). This watcher drops any tracked PR that has
# been merged, closed, turned draft, or approved by someone else — none of
# which need a review from us.
@register("stale_queue", 600)
async def check_stale_queue():
    with db.conn() as c:
        tracked = c.execute(
            "SELECT number, author FROM prs WHERE status IN ('queued', 'review_failed')"
        ).fetchall()

    removed = 0
    for row in tracked:
        n = row["number"]
        author = row["author"]
        reason = None
        try:
            state = gh.pr_state(n)
        except Exception:
            continue
        if state in ("MERGED", "CLOSED"):
            reason = f"pr state: {state.lower()}"
        else:
            try:
                if gh.is_draft(n):
                    reason = "pr is draft"
            except Exception:
                pass
        if reason is None:
            try:
                if gh.approvals_count(n) > 0:
                    reason = "approved by another reviewer"
            except Exception:
                pass
        if reason is None:
            try:
                if gh.has_human_review_activity(n, SELF_LOGIN, author):
                    reason = "another human is already reviewing"
            except Exception:
                pass

        if reason:
            with db.conn() as c:
                c.execute("DELETE FROM prs WHERE number=?", (n,))
            db.log_action(n, "auto_removed", reason)
            removed += 1

    return f"{removed} removed" if removed else "none"


# --- Follow-up watcher -----------------------------------------------------
# Statuses where we actively watch for upstream changes.
# Only PRs that have been through a review (baseline timestamps set) — a
# queued PR that hasn't been reviewed yet has no baseline to compare against,
# so every pre-existing review comment would look like "new activity".
FOLLOWUP_STATUSES = ("awaiting_user", "pending_author")


@register("followups", 900)
async def check_followups():
    """Detect author activity on watched PRs and auto-trigger a re-review.

    Triggers on ANY of:
      - new head commit (SHA changed)
      - new inline review comment from someone else (reply on our line comments)
      - new issue comment from someone else (general PR thread)
    """
    placeholders = ",".join("?" * len(FOLLOWUP_STATUSES))
    with db.conn() as c:
        tracked = c.execute(
            f"""SELECT number, status, head_sha, last_seen_commit_sha,
                       last_seen_review_comment_at, last_seen_issue_comment_at
                FROM prs WHERE status IN ({placeholders})""",
            FOLLOWUP_STATUSES,
        ).fetchall()

    changed = 0
    for pr in tracked:
        # Drop PRs that were merged/closed externally — no review, no HEAD.
        try:
            state = gh.pr_state(pr["number"])
        except Exception:
            state = None
        if state in ("MERGED", "CLOSED"):
            with db.conn() as c:
                c.execute("DELETE FROM prs WHERE number=?", (pr["number"],))
            db.log_action(pr["number"], "auto_removed", f"pr state: {state.lower()}")
            continue

        # Drop PRs that flipped back to draft — author is iterating, no point
        # keeping the row until it's marked ready again.
        try:
            if gh.is_draft(pr["number"]):
                with db.conn() as c:
                    c.execute("DELETE FROM prs WHERE number=?", (pr["number"],))
                db.log_action(pr["number"], "auto_removed", "pr is draft")
                continue
        except Exception:
            pass

        try:
            sha = gh.latest_commit_sha(pr["number"])
            review_at = gh.latest_review_comment_at(pr["number"], SELF_LOGIN)
            issue_at = gh.latest_issue_comment_at(pr["number"], SELF_LOGIN)
        except Exception:
            continue

        baseline_sha = pr["last_seen_commit_sha"] or pr["head_sha"] or ""
        baseline_review = pr["last_seen_review_comment_at"] or ""
        baseline_issue = pr["last_seen_issue_comment_at"] or ""

        reasons = []
        if sha and sha != baseline_sha:
            reasons.append(f"new commit {sha[:7]}")
        if review_at and review_at > baseline_review:
            reasons.append(f"inline reply {review_at}")
        if issue_at and issue_at > baseline_issue:
            reasons.append(f"PR comment {issue_at}")

        if not reasons:
            continue

        # Update baseline so we don't fire repeatedly for the same event,
        # then flag activity and kick off a re-review.
        with db.conn() as c:
            c.execute(
                """UPDATE prs SET
                     head_sha=?,
                     last_seen_commit_sha=?,
                     last_seen_review_comment_at=?,
                     last_seen_issue_comment_at=?,
                     has_new_activity=1,
                     updated_at=datetime('now')
                   WHERE number=?""",
                (sha, sha, review_at, issue_at, pr["number"]),
            )
        db.log_action(pr["number"], "author_activity", "; ".join(reasons))

        # Drop pending findings (stale vs new code). Posted/skipped findings
        # are preserved so user decisions aren't lost.
        #
        # For 'awaiting_user' PRs we re-queue (user owes a decision; new code
        # invalidates the previous review). For 'pending_author' PRs we keep
        # the status — the posted comments are still posted, and the user
        # wants to see the PR stay put with a 'RECENT CHANGES' indicator so
        # they can click 'Approve if addressed' without losing the row.
        with db.conn() as c:
            c.execute(
                "DELETE FROM findings WHERE pr_number=? AND status='pending'",
                (pr["number"],),
            )
            if pr["status"] == "awaiting_user":
                c.execute(
                    "UPDATE prs SET status='queued' WHERE number=?",
                    (pr["number"],),
                )
        notify(f"#{pr['number']} has new activity")
        changed += 1

    return f"{changed} updated" if changed else "none"


# --- Stuck-review sweeper --------------------------------------------------
# Resets PRs stranded in 'reviewing' without a live subprocess. The canonical
# causes are macOS lid-close (asyncio loop + `claude -p` suspended together —
# on resume the wait_for timer has *usually* already fired, but edge cases
# exist where the whole coroutine is lost) and any unhandled exception that
# escapes _do_review_locked before the status is updated.
#
# Startup self-heal in app.py already covers process restarts. This watcher
# covers the case where the FastAPI process stays alive but an individual
# review task is effectively dead.
#
# Threshold = REVIEW_TIMEOUT + buffer. We only consider `reviewing` — queued
# PRs that simply haven't been picked up are NOT stuck; they're waiting on
# HEAD. `review_failed` is intentional and user-actionable.
STUCK_REVIEW_THRESHOLD_SECONDS = 900  # REVIEW_TIMEOUT (600) + 5 min buffer


@register("stuck_reviews", 300)
async def check_stuck_reviews():
    """Reset any PR stuck in 'reviewing' past the timeout threshold.

    Safe to run alongside a live review: the `updated_at` is set when the
    status flips to 'reviewing', so a fresh review will always be under the
    threshold. Only truly-stranded rows get caught.
    """
    with db.conn() as c:
        stuck = c.execute(
            """SELECT number FROM prs
               WHERE status='reviewing'
                 AND (strftime('%s','now') - strftime('%s', updated_at)) > ?""",
            (STUCK_REVIEW_THRESHOLD_SECONDS,),
        ).fetchall()

    if not stuck:
        return "none"

    for pr in stuck:
        with db.conn() as c:
            c.execute(
                """UPDATE prs SET status='queued', updated_at=datetime('now')
                   WHERE number=?""",
                (pr["number"],),
            )
        db.log_action(
            pr["number"],
            "stuck_review_reset",
            f"exceeded {STUCK_REVIEW_THRESHOLD_SECONDS}s in 'reviewing'",
        )

    return f"{len(stuck)} reset"


# --- Scheduler entry point -------------------------------------------------
async def run_forever():
    # Stagger first runs so they don't all fire at once.
    for i, (name, interval, fn) in enumerate(WATCHERS):
        asyncio.create_task(_loop(name, interval, fn, initial_delay=i * 5))


async def _loop(name, interval, fn, initial_delay=0):
    await asyncio.sleep(initial_delay)
    while True:
        try:
            result = await fn()
            _upsert_run(name, result, interval)
        except Exception as e:
            _upsert_run(name, f"error: {e}", interval)
        await asyncio.sleep(interval)
