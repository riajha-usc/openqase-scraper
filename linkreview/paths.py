"""Where the review tool reads and writes. Everything human-facing lives in docs/ so it can be committed."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
AGENT_TSV = DOCS / "test-links-agent-verdicts.tsv"
META_JSON = DOCS / "test-links-meta.json"            # prefetched metadata, one object per link
VERDICTS_TSV = DOCS / "test-links-human-verdicts.tsv"  # the reviewer's marks
LEADS_TSV = DOCS / "test-links-leads.tsv"            # references found behind lead sources
CACHE_DIR = ROOT / "data" / "linkreview"             # fetched PDFs and page text (gitignored)
