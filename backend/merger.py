"""Merge runner for your own PRs.

Two modes, chosen by config:

- **Skill mode** (config.merge_skill() set): spawn `claude -p "/<skill> N"` in
  config.target_repo_dir() so a project-specific merge workflow runs (post-merge
  chores, ticket updates, follow-up tests, whatever the skill does). The UI
  click is treated as the explicit confirmation the skill needs.

- **Plain mode** (no skill configured): run `gh pr merge N --squash
  --delete-branch` directly. No Claude, no project conventions — just merge.

Either way the merge is only *started* here; the sidebar reflects progress via
/api/my_prs (MERGING / MERGE_ERRORS).
"""
import asyncio
from pathlib import Path

from . import config, db, gh

# How long to allow a skill-mode merge to run (it may do post-merge chores).
MERGE_TIMEOUT = 1200

# PRs with a merge in flight + the last error per PR, so the sidebar can show a
# "merging…" state and surface failures. my_prs is a stateless gh fetch, so
# this in-memory state is how the UI knows.
MERGING: set[int] = set()
MERGE_ERRORS: dict[int, str] = {}


def _build_skill_prompt(pr_number: int) -> str:
    skill = config.merge_skill()
    return (
        f"/{skill} {pr_number}\n\n"
        "Context: the user clicked the Merge button in the PR Watcher UI for "
        f"PR #{pr_number}. That click IS the explicit merge confirmation the "
        "skill requires — do NOT wait for any further yes/confirmation, this "
        "session is non-interactive.\n"
        "Follow the skill exactly, including addressing any pending review-bot "
        "findings first (fix the code + tests, push, wait for CI to go green) "
        "before merging.\n"
        "Only stop WITHOUT merging for blockers the skill can't resolve on its "
        "own: not approved, merge conflicts, or CI that stays red after your "
        "fixes. In those cases report the blocker and don't merge.\n"
        "This is a background run with no human to answer questions: never "
        "prompt for input. If a step genuinely needs a decision, skip it and "
        "note it rather than blocking."
    )


async def _merge_with_skill(pr_number: int) -> dict:
    cwd = config.target_repo_dir()
    if cwd and not Path(cwd).is_dir():
        MERGE_ERRORS[pr_number] = f"target_repo_dir does not exist: {cwd}"
        db.log_action(pr_number, "merge_failed", MERGE_ERRORS[pr_number])
        return {"ok": False, "error": "bad target_repo_dir"}

    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", _build_skill_prompt(pr_number),
        "--dangerously-skip-permissions",
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=MERGE_TIMEOUT
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        MERGE_ERRORS[pr_number] = f"merge timed out after {MERGE_TIMEOUT}s"
        db.log_action(pr_number, "merge_timeout", f"exceeded {MERGE_TIMEOUT}s")
        return {"ok": False, "error": "timeout"}

    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    if proc.returncode != 0:
        MERGE_ERRORS[pr_number] = (err or out)[-500:]
        db.log_action(pr_number, "merge_failed", (err or out)[-500:])
        return {"ok": False, "error": err or out}

    # Truth lives on GitHub — confirm the PR actually merged rather than
    # trusting the exit code (a skill can exit 0 after correctly refusing to
    # merge a blocked PR).
    try:
        state = gh.pr_state(pr_number)
    except Exception:
        state = None
    if state == "MERGED":
        db.log_action(pr_number, "merged", f"via /{config.merge_skill()} skill")
        return {"ok": True, "merged": True}

    MERGE_ERRORS[pr_number] = f"not merged (state: {state or 'unknown'}). " + out[-400:]
    db.log_action(pr_number, "merge_blocked", out[-400:])
    return {"ok": True, "merged": False, "state": state}


async def _merge_plain(pr_number: int) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "gh", "pr", "merge", str(pr_number), "--repo", config.repo(),
        "--squash", "--delete-branch",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    if proc.returncode != 0:
        MERGE_ERRORS[pr_number] = (err or out)[-500:]
        db.log_action(pr_number, "merge_failed", (err or out)[-500:])
        return {"ok": False, "error": err or out}
    db.log_action(pr_number, "merged", "gh pr merge --squash --delete-branch")
    return {"ok": True, "merged": True}


async def merge_pr(pr_number: int) -> dict:
    """Start a merge for `pr_number`. Fire-and-forget from the caller's side —
    this coroutine is created as a task; the outcome lands in activity_log +
    MERGE_ERRORS.
    """
    if pr_number in MERGING:
        return {"ok": False, "error": "merge already in progress"}
    MERGING.add(pr_number)
    MERGE_ERRORS.pop(pr_number, None)
    db.log_action(pr_number, "merge_started", "user clicked Merge in UI")
    try:
        if config.merge_skill():
            return await _merge_with_skill(pr_number)
        return await _merge_plain(pr_number)
    finally:
        MERGING.discard(pr_number)
