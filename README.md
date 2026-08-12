# Z.AI Papers

This repository maintains a public, automatically updated list of arXiv papers
related to Z.AI products and technologies.

Public website:

https://ellensong77.github.io/ZAI-Paper/

---

## Bilingual abstracts (EN + ZH)

Each paper row carries two optional translation fields alongside the English
original:

- `translated_title` — Chinese title (already present in the schema).
- `translated_abstract` — Chinese abstract.

### Where the translations come from

- **New papers**: `main.py` extends the GLM review prompt to also produce
  `translated_abstract` (same model call as `translated_title`, so no extra
  API round-trip). The prompt enforces a third-person, neutral translation
  that preserves terminology consistency between the title and abstract.
- **Historical papers**: a one-shot `backfill_translated_abstracts.py` script
  fills in `translated_abstract` for rows that already shipped with only a
  `translated_title`. Already-translated rows are skipped, so the script is
  safe to re-run. This PR ships the backfilled JSON for all current rows.

### Frontend display

`public/index.html` adds a pill-shaped EN/ZH toggle button next to the
「论文概览」 heading in the paper preview modal. The abstract defaults to
English; clicking 「查看中文」 swaps the paragraph to the Chinese translation
in place (the button relabels to 「查看英文」), and clicking again swaps back.
Rows without a `translated_abstract` hide the button entirely. Long Chinese
abstracts scroll inside the modal (`.preview-content` is scrollable) instead
of being cut off.

### Backfilling locally

```bash
# Requires ZHIPU_API_KEY (load it from your .env first)
python backfill_translated_abstracts.py            # all pending rows
BACKFILL_LIMIT=15 python backfill_translated_abstracts.py   # pilot run
```

Failures are logged but never roll back already-translated rows; re-running
picks up where the previous run stopped.

---

## Feishu paper notifications

The `notifications` package is an **additive** module that delivers new arXiv
papers to configured Feishu targets (groups or individuals). It reads the
final `public/data/zhipu_papers.json` produced by `main.py` and never modifies
the papers schema.

The pending-paper source is **per-run**: during an incremental sync `main.py`
writes a small `.notification-state/pending_push.json` cache containing only
the arXiv IDs that were *new* this round. `send` pops that cache, pushes one
card per round, then deletes the cache on full success:

```
incremental sync: main.py writes pending_push.json = {new ids this round}
                                          │
                                          ▼
send:  take pending_push.json as authoritative  ──► push one card  ──► delete cache
       (if absent, fall back to full-corpus minus recently-delivered)
```

The on-disk state (`delivered` map per target) is just a **rolling window**
capped at `DELIVERED_RETENTION` entries — a safety net against duplicate
pushes within a short window, not a full delivery history that needs long-
term maintenance. Data sync can therefore succeed while a notification
fails, and the next run still catches up via the pending cache (or the
fallback diff on a freshly bootstrapped target).

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
- The per-run cache `pending_push.json` lives next to the state file (also
  outside `public`) and is **not** committed — it is regenerated every
  incremental run and consumed/deleted by `send`.
- An **un-bootstrapped** target is *fail-closed*: `send` will never mass-mail
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
python -m notifications bootstrap      # set current papers as historical baseline for un-bootstrapped targets
python -m notifications bootstrap --target <id> --replace-target   # reset a target whose receive_id changed
python -m notifications send           # deliver pending papers to bootstrapped, fingerprint-matching targets
```

Each `send` round builds a single card containing every pending paper (sorted
by `(published, arxiv_id)`) and sends it in one Feishu call. If the call
succeeds, every pending arXiv id is recorded as delivered with that
`message_id`; if it fails, none are recorded and the next run retries the
whole batch (cache-driven runs keep the cache on partial failure).

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
   target (un-bootstrapped) will be initialized — existing targets are left
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

- The pending-push cache is only deleted on **full** success; a partially
  failed round keeps the cache so the operator can retry the same batch.
- Already-delivered papers (within the rolling window) are deduplicated.
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
