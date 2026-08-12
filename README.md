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

`public/index.html` adds a collapsible **「中文摘要翻译」** panel under the
English abstract in the paper preview modal. When `translated_abstract` is
present the panel is shown (collapsed by default); when it is empty the
panel is hidden so the layout degrades gracefully. No regression to existing
UI; the panel is additive.

### Backfilling locally

```bash
# Requires ZHIPU_API_KEY (load it from your .env first)
python backfill_translated_abstracts.py            # all pending rows
BACKFILL_LIMIT=15 python backfill_translated_abstracts.py   # pilot run
```

Failures are logged but never roll back already-translated rows; re-running
picks up where the previous run stopped.
