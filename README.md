# Z.AI Papers

This repository maintains a public, automatically updated list of arXiv papers
related to Z.AI products and technologies.

Public website:

https://ellensong77.github.io/ZAI-Paper/

---

## Table of contents

- [Daily sync](#daily-sync)
- [Feishu paper notifications](#feishu-paper-notifications)
  - [Architecture](#architecture)
  - [Feishu application prerequisites](#feishu-application-prerequisites)
  - [GitHub Secrets and Variables](#github-secrets-and-variables)
  - [Target configuration format](#target-configuration-format)
  - [CLI commands](#cli-commands)
  - [First-time go-live checklist](#first-time-go-live-checklist)
  - [Adding a new target later](#adding-a-new-target-later)
  - [Recovering from a failed send](#recovering-from-a-failed-send)
  - [Local testing](#local-testing)
  - [Notes for fork pull requests](#notes-for-fork-pull-requests)
- [Security](#security)

---

## Daily sync

`python main.py` is the synchronization entry point. It reads the historical
papers JSON, queries arXiv incrementally, applies rules, runs GLM review and
translation, enriches rows with Semantic Scholar metadata, merges the result
with history, and writes `public/data/zhipu_papers.json`. The schema of that
file is **not** modified by the notification module.

---

## Feishu paper notifications

The `notifications` package is an **additive** module: it reads the final
`public/data/zhipu_papers.json` produced by `main.py` and delivers new papers
to configured Feishu targets. It never modifies `main.py` or the papers schema.

Pending papers per target are computed as:

```
pending = (all arXiv IDs in the current JSON) - (IDs in that target's delivered map)
```

This means data sync can succeed while a notification fails, and the next run
will still catch up — it does not rely on a one-shot diff captured during sync.

### Architecture

```
notifications/
├── __init__.py     # package marker
├── __main__.py     # argparse CLI: python -m notifications <command>
├── config.py       # env parsing, strict target validation, fingerprints
├── client.py       # tenant token + send-message API with bounded retry
├── cards.py        # Feishu interactive-card builders
├── state.py        # per-target, atomic, fingerprint-guarded delivery state
└── service.py      # dry-run / smoke-test / bootstrap / send orchestration
```

Design rules the module enforces:

- Each Feishu target is identified by a stable, unique `id`. The real
  `receive_id` is **never** written to the state file or logs — only a SHA-256
  fingerprint is stored, and logs show a redacted preview.
- State lives in `.notification-state/feishu.json` (outside `public`, so it is
  never published to Pages). It is committed to Git so retries survive across
  runs; only the `*.tmp` atomic-write scratch files are git-ignored.
- An **un-bootstrap**ed target is *fail-closed*: `send` will never mass-mail
  history. You must explicitly `bootstrap` first.
- If a target's `receive_id`/type changes, the fingerprint no longer matches
  and `send` is refused until you re-bootstrap with `--replace-target`.
- Per-target states are isolated: one target failing never blocks another, and
  every successfully sent message is flushed to disk immediately so the next
  run only retries what actually failed.

### Feishu application prerequisites

1. Create a **Self-built enterprise application** in the Feishu admin console.
2. Enable the **Bot** capability for the application.
3. Create a version and **publish/release** the application.
4. Under *Permissions & Scopes*, grant the message-sending permission
   (`im:message:send_as_bot` — "send messages to chats as the bot").
   - For `receive_id_type: chat_id`, the bot must first be **added to the
     target group** as a member.
   - For `receive_id_type: open_id`/`user_id`/`union_id`, the bot must be
     available to that user (typically via being in a shared chat or through
     the app's availability scope).
5. Note the application's **App ID** and **App Secret**.

### GitHub Secrets and Variables

Configure these in **Settings → Secrets and variables → Actions**:

**Repository Secrets** (required before any notification runs):

| Name | Purpose |
| --- | --- |
| `FEISHU_APP_ID` | Self-built app App ID (e.g. `cli_...`). |
| `FEISHU_APP_SECRET` | Self-built app App Secret. |
| `FEISHU_TARGETS_JSON` | JSON array of targets — see format below. |

**Repository Variables** (controls the scheduled send gate; default treated as
`false`, so merging code alone never triggers mass notifications):

| Name | Value | Purpose |
| --- | --- | --- |
| `FEISHU_NOTIFICATIONS_ENABLED` | `true` | When `true`, the daily `schedule` run executes `send`. Absent or any other value ⇒ no send. |

Do **not** store real values for any of the above in code, `.env`, or
documentation.

### Target configuration format

`FEISHU_TARGETS_JSON` must be a JSON array of objects. Each object needs:

- `id` — unique, stable, non-empty identifier (used as the state key).
- `name` — human-readable label.
- `receive_id_type` — one of `chat_id`, `open_id`, `user_id`, `union_id`,
  `email`.
- `receive_id` — non-empty target identifier.

Example:

```json
[
  {
    "id": "paper-research-group",
    "name": "论文研究群",
    "receive_id_type": "chat_id",
    "receive_id": "oc_replace_with_real_chat_id"
  },
  {
    "id": "ellen",
    "name": "Ellen",
    "receive_id_type": "open_id",
    "receive_id": "ou_replace_with_real_open_id"
  }
]
```

### CLI commands

All commands support `--help` and return a non-zero exit code on failure.

```bash
python -m notifications dry-run        # report per-target totals/pending; no network, no state writes
python -m notifications smoke-test     # send one fixed test card to every target; never writes delivery state
python -m notifications bootstrap      # set current papers as historical baseline for un-bootstrap targets
python -m notifications bootstrap --target <id> --replace-target   # reset a target whose receive_id changed
python -m notifications send           # deliver pending papers to bootstrapped, fingerprint-matching targets
```

Cards: every pending paper is sent as its own rich card (title, authors,
fields, abstract, arXiv/PDF/ZAI-Paper buttons), regardless of how many are
pending. Cards are sent in `(published, arxiv_id)` order so delivery order is
stable across runs.

### First-time go-live checklist

Run these via the GitHub Actions UI **(Actions → Update and deploy Pages → Run
workflow → `notification_mode`)**, in order:

1. Configure a small **test target** first (e.g. your own open_id or a test
   chat) in `FEISHU_TARGETS_JSON`.
2. `dry-run` — confirms config parses and shows pending counts.
3. `smoke-test` — confirms the bot can actually deliver a card to each target.
4. Check the delivered test card(s).
5. `bootstrap` — marks the current paper set as the historical baseline so
   `send` will not mass-mail history.
6. Verify `.notification-state/feishu.json` was committed.
7. Set repository Variable `FEISHU_NOTIFICATIONS_ENABLED=true`.
8. `send` — delivers only papers that appear **after** the bootstrap baseline.

Production `send` on the `schedule` trigger is blocked until step 7 completes.

### Adding a new target later

1. Add the new object to `FEISHU_TARGETS_JSON`.
2. Run `workflow_dispatch` with `notification_mode = dry-run` to confirm it is
   parsed.
3. Run `workflow_dispatch` with `notification_mode = bootstrap`; only the new
   target (un-bootstrap) will be initialized — existing targets are left
   unchanged when their fingerprints match.
4. From the next `send`, the new target receives only papers added after its
   own baseline.

If a target's `receive_id`/type changes instead, the fingerprint no longer
matches and `send` is refused. Re-initialize with:

```bash
python -m notifications bootstrap --target <that-id> --replace-target
```

### Recovering from a failed send

Safe to just re-run:

- Already-delivered papers are deduplicated via the state file.
- Any target that failed keeps its previous baseline; only the failed papers
  remain pending.
- A target failure never rolls back the state of a target that succeeded.
- On scheduled runs, transient Feishu/HTTP errors are retried with bounded
  exponential backoff (429/5xx/network); 4xx permission/argument errors are
  *not* blindly retried.

### Local testing

You can exercise the module locally without a real Feishu app by exporting
placeholder credentials and pointing at the local papers JSON:

```bash
cp .env.example .env            # then edit .env (never commit it)
set -a; . .env; set +a          # or use your platform's env loader
python -m notifications dry-run
python -m unittest discover -s tests -v
```

`.env` is git-ignored. Never put real credentials in it and never commit it.

### Notes for fork pull requests

Two distinct cases, by design:

- A fork's **own** Actions (push, `workflow_dispatch`, `schedule` on the fork)
  can read the Secrets configured **in the fork** — i.e. you can set up your
  own Feishu app in your fork for isolated testing.
- A PR opened **from a fork to this repository** runs against the upstream
  base branch but, by GitHub's security model, the default event
  (`pull_request`) **cannot read this repository's Secrets**
  (`FEISHU_APP_ID`/`FEISHU_APP_SECRET`/`FEISHU_TARGETS_JSON`). The notify step
  detects missing Secrets and fails fast with a clear message without printing
  their values.

This repository intentionally does **not** use `pull_request_target`, so fork
PRs never gain access to production Secrets.

---

## Security

- Real App ID, App Secret, `receive_id` (chat_id/open_id/...), tenant tokens,
  and Authorization headers are **never** written to code, tests, docs, logs,
  or the state file. Logs show only target `id`/`name` and a redacted preview.
- The committed `.notification-state/feishu.json` stores only a SHA-256
  fingerprint per target plus delivered arXiv IDs/timestamps/message IDs.
- Notification state is never published to GitHub Pages (it lives outside
  `public/`).
- Commit messages that the bots produce carry `[skip ci]`, and `push` ignores
  `.notification-state/**` as belt-and-suspenders against dispatch loops.
- Do not paste real credentials into issues, PRs, or commits.
