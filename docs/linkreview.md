# Link review tool

A small local web UI used to hand-label the 71 maintainers' test links in
`docs/test-links-agent-verdicts.tsv`. Its output is the two files on `main`:

| file | what it holds |
|---|---|
| `docs/test-links-human-verdicts.tsv` | one row per link: `n`, `url`, `label`, `note`, `decided_at` |
| `docs/test-links-leads.tsv` | one row per reference or link found behind a `yes` or `lead` source |

This branch exists so the labelling process is visible and repeatable. It is not
part of the scraper and does not need to be merged.

## Labels and the criteria behind them

**yes**: a case study. The quantum work was applied to a real problem, on real
domain data, with a result stated in the domain's own terms. Two tiers both
count, and the note says which when it is not obvious:

1. *Operational*: a named organisation uses it in its actual workflow
   (vendor case studies, press releases with substance, deployed systems).
2. *Applied research*: a paper with real domain data and a domain outcome,
   even if it calls itself exploratory. Example: test link 28, IBM hardware
   on renal-cell-carcinoma patient gene-expression data, predicting
   immunotherapy response, with Cleveland Clinic. The authors' own lab counts
   as the user; a separate "customer" is not required.

**no**: not a case study. The notes record four kinds, because a scraper has
to learn to reject each:

- *not quantum*: crawl noise (two MRI papers from HAL).
- *quantum cognition*: Hilbert-space formalism used as a model of human
  cognition; no quantum computing involved. Dense with the right vocabulary,
  so the trickiest false positives.
- *theory only*: capacity bounds, game-theory thought experiments; no
  problem instance, data or hardware.
- *methods paper*: a real quantum algorithm or model, benchmarked on
  synthetic or toy data, with the application domain as motivation. A quick
  tell: the abstract says "potential" or "could be applied to" about the
  domain. Example: test link 49.

**lead**: not a case study itself, but it cites or links to some. Surveys,
literature reviews, and marketing pages that confirm something happened
without saying what (test link 9, whose published work is test link 10).
Its references go into the leads TSV.

**maybe**: come back to it. Treated as labelled by the progress bar, filterable.

A useful three-question test for borderline papers: Is there a domain problem
a non-quantum person actually has? Did they run on that domain's real data?
Is the outcome stated in the domain's terms? Three yeses is a `yes`.

## Leads

Labelling a link `yes` or `lead` starts a background job that collects:

- the paper's reference list from OpenAlex, then Semantic Scholar if OpenAlex
  has nothing (set `S2_API_KEY` in `.env` for a private 1 request/second
  budget; the shared pool is usually rate-limited);
- DOIs, arXiv ids and URLs found in the paper text;
- for web pages, outgoing links, minus navigation and social junk.

`docs/test-links-leads.json` is the store; the TSV is regenerated from it
whenever a lead set or a label changes, and carries `source_label` so rows
from a confirmed case study can be told apart from rows from a survey.
**Keep the JSON next to the TSV**: without it, the first lead save would
rewrite the TSV from an empty store.

## Running it

```bash
pip install -r requirements.txt
.venv/bin/python -m linkreview.content          # one-off: fetch metadata, PDFs and text for all links
.venv/bin/python -m linkreview.server           # http://localhost:8010
```

`docs/test-links-meta.json` (titles, abstracts, dates, venues, DOIs) is
committed, so the UI works without the prefetch; the prefetch only adds the
PDF and text tabs. PDFs and text go in `data/linkreview/`, which is
gitignored. Publishers that block scripted fetches (HAL, AISeL, ACS, BCG)
show an error in place of the text; download the PDF in a browser and save it
as `data/linkreview/pdf/NNN.pdf` with the three-digit item number, and the
tool picks it up on the next request.

## Keys

| key | action |
|---|---|
| `k` / `j` | next / previous link (also arrow keys) |
| `Enter` / `Esc` | open a link / back to the list |
| `y` `n` `l` `m` | label yes / no / lead / maybe, then advance to the next unlabelled |
| `u` | clear the label |
| `c` | edit the note |
| `1`–`4` | PDF, text, page and leads tabs |
| `x` | re-extract leads |
| `f` / `s` | cycle the label filter / the sort order |
| `/` | search; `?` shows everything else |

Reloading keeps the open link (the URL hash carries it). The list can be
filtered by your label, the agent's verdict, source kind, year and arXiv
topic, and sorted by date, title, agent verdict or label.

## Layout

| file | role |
|---|---|
| `linkreview/content.py` | metadata from arXiv, HAL, Crossref and page meta tags; PDF and text cache |
| `linkreview/leads.py` | reference extraction (OpenAlex, Semantic Scholar, text and page scans) |
| `linkreview/store.py` | the two TSVs and the leads JSON; atomic writes |
| `linkreview/server.py` | FastAPI app, background lead jobs |
| `linkreview/static/index.html` | the single-page UI, no build step |
