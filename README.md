# PR Watcher

A local, single-user dashboard for keeping on top of pull requests on one
GitHub repo. It detects new PRs, runs **headless Claude code reviews on
demand**, lets you post findings inline, approve, and merge — all from one page
at `http://127.0.0.1:4747`.

Everything runs on your machine. State is a single SQLite file. No server, no
account, nothing leaves your laptop except the `gh` and `claude` calls you'd
make anyway.

- **New-PR watcher** — polls the repo, queues PRs opened by other people.
- **On-demand review** — click Review (or hit the API) and a `claude -p`
  subprocess reviews the diff against *your* rules (`review_rules.md`) and
  returns findings you can Post or Skip per item.
- **Follow-up watcher** — notices when an author pushes commits or replies, and
  re-reviews.
- **Your own PRs sidebar** — your open PRs with approval status and a Merge
  button (plain squash merge, or a merge skill you configure).

The reviewer **never approves on its own** — it surfaces findings; you decide.

## Requirements

- macOS or Linux
- Python 3.11+
- [`gh`](https://cli.github.com/) authenticated (`gh auth login`)
- [`claude`](https://claude.com/claude-code) CLI on your PATH (for reviews/merges)

## Quick start

```bash
git clone https://github.com/jhon50/pr-watcher.git
cd pr-watcher

# Point it at your repo (auto-detected from gh if you skip this)
export PRW_REPO=owner/name
export PRW_SELF_LOGIN=your-github-login

./run.sh
# → http://127.0.0.1:4747
```

First run creates `.venv/` and installs FastAPI/uvicorn. State lives at
`~/.pr-watcher/state.db`.

## Configuration

Resolution order for every setting: **environment variable** → **`~/.pr-watcher/
config.json`** → **`gh` auto-detection** → default. Copy `config.example.json`
to `~/.pr-watcher/config.json` if you prefer a file to env vars.

| Setting | Env var | Default |
|---|---|---|
| Target repo (`owner/name`) | `PRW_REPO` | `gh repo view` default |
| Your GitHub login | `PRW_SELF_LOGIN` | `gh api user` |
| Authors to ignore | `PRW_SKIP_AUTHORS` (comma list) | you + dependabot |
| Port | `PRW_PORT` | `4747` |
| Merge skill (optional) | `PRW_MERGE_SKILL` | *(plain squash merge)* |
| Repo checkout for merge skill | `PRW_TARGET_REPO_DIR` | — |

**Merge behavior:** with no `merge_skill`, the Merge button runs
`gh pr merge <N> --squash --delete-branch`. Set `merge_skill` to a Claude
skill/slash-command name and merging instead runs `claude -p "/<skill> <N>"` in
`target_repo_dir`, so your own merge workflow (post-merge chores, ticket
updates, follow-up tests, etc.) runs.

**Review rules:** `run.sh` copies `review_rules.example.md` to `review_rules.md`
on first run. Edit `review_rules.md` — it's the entire ruleset the reviewer
follows (it deliberately ignores any other `CLAUDE.md`/skills so reviews are
reproducible), and it's git-ignored, so your edits never conflict on `git pull`.
Ships with a language-agnostic default.

## Run it in the background (launchd, macOS)

```bash
./install-launchd.sh
```

This writes `~/Library/LaunchAgents/pr-watcher.plist` (label `pr-watcher`),
starts it now, and relaunches it at login and if it crashes. Logs go to
`~/.pr-watcher/launchd.log`.

```bash
# status
launchctl print gui/$(id -u)/pr-watcher | grep state
# stop / uninstall
launchctl bootout gui/$(id -u)/pr-watcher
rm ~/Library/LaunchAgents/pr-watcher.plist
```

Use a different label with `PRW_LABEL=my-watcher ./install-launchd.sh`. Set
config via a `~/.pr-watcher/config.json` file (env vars from your shell aren't
seen by launchd).

> Only one process can bind the port. Don't run `./run.sh` and the launchd agent
> at the same time.

## Using it with Claude Code

Open this repo in Claude Code and the bundled skill
(`.claude/skills/pr-watcher/`) loads automatically — ask it to start the
watcher, review a PR, install the launchd agent, or debug a stuck PR.

## CLI (`./prw`)

```bash
./prw list                                   # dump current state
./prw add-pr 123 someuser "Fix the thing"    # queue a PR manually
./prw approve 123                            # approve + remove
./prw dismiss 123                            # remove without approving
```

## How review works

1. A PR is queued (watcher or `add-pr`).
2. You click **Review**. `reviewer.py` spawns `claude -p` with
   `reviewer_prompt.md` + `review_rules.md`, in this repo's dir so no external
   `CLAUDE.md` leaks in.
3. Claude returns `<FINDINGS>[…]</FINDINGS>`; findings land in SQLite as
   `pending`.
4. The UI shows each finding — **Post** sends it as an inline GitHub comment,
   **Skip** drops it.
5. **Approve** runs `gh pr review --approve` and clears the PR. **Approve if
   addressed** re-checks each posted finding before approving.

## Data & privacy

- SQLite at `~/.pr-watcher/state.db` (git-ignored, never leaves your machine).
- The only network calls are `gh`/`claude` on your behalf.
- No secrets in the repo. Everything machine-specific is env/`config.json`,
  both git-ignored.

## License

MIT — see [LICENSE](LICENSE).
