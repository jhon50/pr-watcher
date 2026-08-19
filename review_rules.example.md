# Review Rules

This file is the **single source of truth** for the headless reviewer. It is
injected into the review prompt as `{RULES}`. Edit it to match your team's
conventions — the reviewer ignores any other `CLAUDE.md` / skills in scope.

This ships as a sensible, language-agnostic default. Make it yours.

---

## What to flag (keep list)

Report a finding, at the appropriate severity, when you see:

- **Correctness bugs** — logic that produces the wrong result, off-by-one,
  inverted conditions, wrong operator (`<` vs `<=`), missing `await`, unhandled
  `nil`/`null`/`undefined`, resource leaks.
- **Security** — injection (SQL/shell/HTML), missing auth/authorization checks,
  secrets committed, unsafe deserialization, SSRF, path traversal, missing
  validation on user input.
- **Data integrity** — destructive operations without guards, migrations that
  can lose data, non-idempotent writes, race conditions on shared state.
- **Missing test coverage** — new behavior (a new branch, method, endpoint,
  prop) with no test exercising it. See the coverage-map step in the task.
- **Out-of-scope changes** — files that don't belong to the PR's stated purpose
  (generated artifacts, lockfiles, unrelated modules). Pin to the first changed
  line of the offending file.
- **API/contract breaks** — changed signatures, response shapes, or defaults
  that callers rely on, without a migration path.

## What to skip (drop list)

Do **not** raise findings for:

- Pure style/formatting a linter or formatter would catch.
- Personal preference with no correctness, security, or clarity impact.
- Pre-existing issues the PR doesn't touch.
- Speculative "you could also…" that isn't a defect.
- Renames/refactors that preserve behavior, unless they introduce a bug.

When in doubt on a low-value nit, drop it. A missed real bug is worse than a
skipped nit, but a wall of nits trains the user to ignore the tool.

## Severity

- **critical** — ships a bug, security hole, or data loss to production.
- **important** — real defect or a genuine missing-test gap; should be fixed
  before merge but isn't an emergency.
- **suggestion** — worthwhile improvement, non-blocking.
- **nit** — tiny, optional.

## Comment voice

Every `suggestion_body` is posted verbatim on GitHub. Write it so a colleague
would want to receive it:

- Start with the severity tag in brackets: `[important]`, `[suggestion]`,
  `[nit]`. Critical uses `[important]`.
- Lead with the concrete problem, not preamble. No "Great work, but…".
- Be specific: name the failure case or input that breaks.
- Prefer a question when you're not certain it's wrong ("is `X` guaranteed
  non-null here?").
- Include a ```suggestion block with the fix when it's a concrete code change.
- Keep it short. No praise padding, no em-dashes.

## Approval

You never approve. Every finding is shown to the user in the UI; they click
Post to send it inline or Skip to drop it. An empty findings array means "clean
after filtering" — the user decides whether to approve.
