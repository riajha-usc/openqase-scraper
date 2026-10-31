# OpenQase case study scraper (PoC)

An agentic pipeline that finds candidate quantum computing case studies on the web, applies the
triage test inline, extracts the four required fields, and queues what passes for a human to
approve or reject. Nothing is published without a person saying yes.

It implements the [revised design](https://docs.google.com/document/d/1T8L77Jr2cqlJVsqiBb9aFg3iW2TFGcDWM0tM2611zgc/edit):

| Design stage | Code | What happens |
| --- | --- | --- |
| 1. Discover | `scraper/sources.py`, `scraper/triage.py` | Crawl each source in `sources.yaml`, read each page, apply the three-part triage test on the spot. Failures are logged and dropped. Provenance scored 1 to 3. URL already seen or already cited by a published case study is skipped. |
| 2. Extract | `scraper/extract.py` | Fill Who / What / How / Outcome. A record missing any field is dropped before the queue. |
| 3. Approve | `scraper/review/` | One card at a time: Approve, Reject (reason required), Skip. |

The per-candidate flow is a LangGraph state graph (`scraper/graph.py`): `check_url -> fetch -> triage -> extract -> store`.
Every LLM call uses a structured output schema (`scraper/schemas.py`), so the agent can only answer in the record shape.

## Setup

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then put your ANTHROPIC_API_KEY in .env
```

## Commands

```bash
python cli.py run URL [URL ...]        # run the agent over specific pages (e.g. the maintainers' test links)
python cli.py discover --limit 10      # crawl every source in sources.yaml
python cli.py discover --source "IonQ resources"
python cli.py review                   # approval UI at http://localhost:8000
python cli.py stats                    # counts by status
python cli.py list --status queued
python cli.py show 12                  # one record as JSON, including triage reasoning
python cli.py reset-skipped            # start a new review session

python cli.py synth --n 5              # synthetic labelled articles from published case studies
python cli.py eval                     # precision / recall of the agent on the synthetic set
pytest                                 # unit tests (no network, no API key)
```

## Full text for papers

For scholarly landing pages (arXiv, AISeL, most publishers) the agent follows the `citation_pdf_url`
tag and judges the full paper, not the abstract. Some publishers block automated PDF downloads
(AISeL returns 403); for those, download the PDF yourself and run the agent on the file:
`python cli.py run /path/to/paper.pdf`. Sources that need a login or forbid automation are out of
scope by design.

## Statuses

`queued`, `approved`, `rejected`, `duplicate`, `skipped` are the review states from the design.
The agent also logs what it dropped: `triaged_out` (failed the three-part test), `extract_failed`
(a required field could not be filled), `fetch_failed`. Rejected URLs are never surfaced again.

## Synthetic data and evaluation

`synth` takes published OpenQase case studies and writes six articles for each: two that should be
queued (first-party page, specialist press), and four that should be stopped (investment news,
roadmap, anonymous organisation, result with no method named). `eval` runs the agent over them
in a separate database and reports precision and recall against the labels. This is the seed of
the eval framework for the AI agent training track.

## MCP server for AI agents (parked)

`python -m mcp_server` starts a Model Context Protocol server exposing the public catalogue as tools
(`search_openqase`, `get_case_study`, `list_case_studies`, `list_items`, `get_item`, `content_types`).
Read-only, no credentials. Client setup and the tool reference are in [docs/mcp.md](docs/mcp.md).
Parked until the mid-program demo on 3 Nov 2026 to keep the focus on the scraper; it works and is
kept for later.

## Storage

SQLite at `data/candidates.db` (one row per URL the agent looked at). Swapping in the OpenQase
Supabase database is a change to `scraper/db.py` only; the record fields already mirror the
case study table (`title`, `partner_companies`, `quantum_companies`, `algorithms`, `quantum_hardware`, `year`).

## Not in this PoC

Writing approved records into OpenQase, email notifications, and an edit-and-requeue loop
(the revised design drops it on purpose: reject with a reason instead).
