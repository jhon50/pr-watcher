"""Central configuration.

Everything repo/user specific is resolved here so the rest of the code stays
generic. Resolution order for each setting:

  1. Environment variable (e.g. PRW_REPO)
  2. JSON file at ~/.pr-watcher/config.json (override path with PRW_CONFIG)
  3. Auto-detection via the `gh` CLI where possible
  4. A sensible default

Nothing here is specific to any one project — clone the repo, point it at your
own GitHub repository, and go.
"""
import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get("PRW_CONFIG", Path.home() / ".pr-watcher" / "config.json")
)


@lru_cache(maxsize=1)
def _file_cfg() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _get(key: str, env: str, default=None):
    if env in os.environ and os.environ[env] != "":
        return os.environ[env]
    fc = _file_cfg()
    if fc.get(key):
        return fc[key]
    return default


def _gh(args: list[str]) -> str | None:
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip() or None
    except FileNotFoundError:
        pass
    return None


@lru_cache(maxsize=1)
def repo() -> str:
    """GitHub repo slug, e.g. "owner/name".

    Falls back to the default repo of the current git checkout via
    `gh repo view`. Raise a clear error if it can't be determined so the
    server fails loudly at startup rather than 404ing every gh call.
    """
    r = _get("repo", "PRW_REPO")
    if r:
        return r
    detected = _gh(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])
    if detected:
        return detected
    raise RuntimeError(
        "Could not determine the target repo. Set PRW_REPO=owner/name, add "
        f'"repo" to {CONFIG_PATH}, or run gh in a repo with a default set.'
    )


@lru_cache(maxsize=1)
def self_login() -> str:
    """The GitHub login of the person running the watcher.

    Used to skip your own PRs when detecting new work, and to tell your review
    comments apart from other people's replies.
    """
    v = _get("self_login", "PRW_SELF_LOGIN")
    if v:
        return v
    detected = _gh(["api", "user", "--jq", ".login"])
    if detected:
        return detected
    raise RuntimeError(
        "Could not determine your GitHub login. Set PRW_SELF_LOGIN or add "
        f'"self_login" to {CONFIG_PATH}.'
    )


@lru_cache(maxsize=1)
def skip_authors() -> set[str]:
    """Authors whose PRs the new-PR watcher ignores.

    Always includes you (self_login) and the common bot logins. Extend with a
    comma-separated PRW_SKIP_AUTHORS or a "skip_authors" list in the config
    file.
    """
    base = {
        self_login(),
        "dependabot",
        "dependabot[bot]",
        "app/dependabot",
    }
    extra = _get("skip_authors", "PRW_SKIP_AUTHORS")
    if isinstance(extra, str):
        base |= {a.strip() for a in extra.split(",") if a.strip()}
    elif isinstance(extra, list):
        base |= set(extra)
    return base


@lru_cache(maxsize=1)
def bot_logins() -> set[str]:
    """Extra reviewer logins to treat as bots.

    The `login[bot]` / `app/` conventions are detected automatically, but some
    review bots authenticate with a plain login (e.g. an app slug with no
    `[bot]` suffix) that looks human. List those here via PRW_BOT_LOGINS (comma
    list) or a "bot_logins" array in the config file.
    """
    extra = _get("bot_logins", "PRW_BOT_LOGINS")
    if isinstance(extra, str):
        return {a.strip() for a in extra.split(",") if a.strip()}
    if isinstance(extra, list):
        return set(extra)
    return set()


def is_bot(login: str | None) -> bool:
    if not login:
        return False
    return (
        login.endswith("[bot]")
        or login.startswith("app/")
        or login in bot_logins()
    )


def bot_label() -> str:
    """Sidebar badge text when only bots have reviewed. Default "bot review";
    set PRW_BOT_LABEL / "bot_label" to name your bot (e.g. "cezbot review").
    """
    return _get("bot_label", "PRW_BOT_LABEL", "bot review")


def merge_skill() -> str | None:
    """Optional Claude skill/slash-command to run on merge instead of a plain
    squash merge. When set, merging spawns `claude -p "/<skill> <number>"` in
    `target_repo_dir`. Leave unset to merge with a plain
    `gh pr merge --squash --delete-branch`.
    """
    return _get("merge_skill", "PRW_MERGE_SKILL")


def target_repo_dir() -> str | None:
    """Local working copy of the target repo. Only used as the cwd for the
    merge skill (so project conventions / CLAUDE.md load). Optional.
    """
    return _get("target_repo_dir", "PRW_TARGET_REPO_DIR")


def port() -> int:
    return int(_get("port", "PRW_PORT", "4747"))
