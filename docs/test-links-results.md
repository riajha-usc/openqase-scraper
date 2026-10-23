# Agent run over the maintainers' test links

Models: gpt-oss-120b via Groq for 67 links; the last 4 were judged by gpt-oss-20b after the 120b daily quota ran out (model shown per row). Links: 71 pages (2 journal home pages and 1 search page in the sheet were skipped).

| Outcome | Count |
| --- | --- |
| not judged yet | 26 |
| dropped at triage | 21 |
| queued | 14 |
| dropped at extraction | 6 |
| unreachable | 3 |
| duplicate of a published case study | 1 |

Precision and recall are reported only once the maintainers label the links; the verdicts are the agent's output.
Per-link verdicts and evidence: [test-links-agent-verdicts.tsv](test-links-agent-verdicts.tsv) (same columns as in the shared sheet).
Regenerate with `python data/spreadsheet/report.py` after a run.
