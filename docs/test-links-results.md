# Agent run over the maintainers' test links

71 pages, scored against the maintainers' labels in [test-links-human-verdicts.tsv](test-links-human-verdicts.tsv). A real case counts as caught when the agent queues it. Two journal home pages and one search page in the sheet were skipped.

| | Before (baseline prompt) | After (revised prompt) |
| --- | --- | --- |
| Precision | 0.86 (18 of 21 queued) | 0.96 (27 of 28 queued) |
| Recall | 0.36 (18 of 50) | 0.54 (27 of 50) |

The revised triage prompt counts the authors' own institution when research applies quantum methods to a real problem, and asks for real domain data with a result. Triage reads the first 6,000 characters; extraction reads the full text. All 71 links in the after run used gpt-oss-120b via Groq (the baseline used gpt-oss-20b for 4 links).

## After, by label

| Maintainers' label | Queued | Dropped at triage | Dropped at extraction | Other |
| --- | ---: | ---: | ---: | --- |
| yes (50) | 27 | 19 | 3 | 1 duplicate of a published case study |
| no (15) | 0 | 11 | 2 | 2 unreachable |
| lead (6) | 1 | 4 | 0 | 1 unreachable |

## Before, by label

| Maintainers' label | Queued | Dropped at triage | Dropped at extraction | Other |
| --- | ---: | ---: | ---: | --- |
| yes (50) | 18 | 24 | 7 | 1 duplicate of a published case study |
| no (15) | 1 | 11 | 1 | 2 unreachable |
| lead (6) | 2 | 3 | 0 | 1 unreachable |

## Where the missed real cases are

- 19 dropped at triage: 10 arXiv papers fail the work-done test (real domain data with a result), 8 HAL papers fail the named-organisation test because HAL gives the agent only the abstract, without affiliations, and 1 Nature paper.
- 3 dropped at extraction with a field missing, and 1 already published on OpenQase.

The prompt change was informed by error analysis on these same links, so a fresh labelled set is needed to confirm the gain.

Per-link verdicts and evidence: [before](test-links-agent-verdicts.tsv), [after](test-links-agent-verdicts-after.tsv). Regenerate with `python data/spreadsheet/report.py` after a run.
