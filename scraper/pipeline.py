"""Run the per-candidate graph over ad-hoc URLs or over the configured sources."""
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

import anthropic

from . import db
from .graph import build_graph
from .openqase_api import fetch_published
from .sources import Seed, discover_source, load_sources


class Runner:
    def __init__(self, db_path: Optional[Path] = None, published: Optional[list[dict]] = None, quiet: bool = False):
        self.conn: sqlite3.Connection = db.connect(db_path)
        self.published = published if published is not None else fetch_published()
        self.graph = build_graph(self.conn, self.published)
        self.quiet = quiet

    def process(self, seed: Seed, rerun: bool = False) -> dict:
        state = {
            "url": seed.url,
            "source_name": seed.source_name,
            "provenance_override": seed.provenance,
            "prefetched_title": seed.title,
            "prefetched_text": seed.text,
            "rerun": rerun,
        }
        try:
            final = self.graph.invoke(state)
        except anthropic.AuthenticationError as e:
            raise SystemExit(f"Anthropic API rejected the key: {e.message}") from e
        except anthropic.BadRequestError as e:
            if "credit balance" in str(e).lower():
                raise SystemExit("Anthropic API: the account has no credits. Add credits at console.anthropic.com, then rerun.") from e
            final = {"url": seed.url, "status": "llm_error", "reason": e.message}
        except anthropic.APIError as e:  # rate limits, 5xx: skip this page, keep the run going; it is not stored, so a rerun retries it
            final = {"url": seed.url, "status": "llm_error", "reason": f"{type(e).__name__}: {e}"}
        except RuntimeError as e:  # provider limits on the OpenAI-compatible path (request too large, long cooldown)
            final = {"url": seed.url, "status": "llm_error", "reason": str(e)}
        except Exception as e:  # noqa: BLE001 - any other provider or parsing failure: skip this page, keep the run going
            if type(e).__module__.split(".")[0] not in ("openai", "httpx", "httpx2", "json", "pydantic"):
                raise
            final = {"url": seed.url, "status": "llm_error", "reason": f"{type(e).__name__}: {str(e)[:200]}"}
        if not self.quiet:
            status = "already seen" if final.get("already_seen") else final.get("status")
            detail = ""
            if final.get("status") == "queued":
                detail = f" | {final['extraction'].get('title')}"
            elif final.get("reason"):
                detail = f" | {final['reason'][:140]}"
            print(f"[{status}] {seed.url}{detail}")
        return final

    def run_urls(self, urls: Iterable[str], source_name: str = "manual", provenance: Optional[int] = None,
                 rerun: bool = False) -> list[dict]:
        return [self.process(Seed(url=url, source_name=source_name, provenance=provenance), rerun=rerun) for url in urls]

    def rejudge(self, source_name: str) -> list[dict]:
        """Re-run triage and extraction on stored page text: no fetching, so prompt changes are cheap to test."""
        rows = self.conn.execute("SELECT url, title, provenance, page_text FROM candidates "
                                 "WHERE source_name = ? AND page_text IS NOT NULL", (source_name,)).fetchall()
        results = []
        for row in rows:
            results.append(self.process(Seed(url=row["url"], source_name=source_name, title=row["title"],
                                             text=row["page_text"]), rerun=True))
        return results

    def run_sources(self, names: Optional[list[str]] = None, limit: int = 20) -> list[dict]:
        results = []
        for source in load_sources():
            if names and source["name"] not in names:
                continue
            print(f"== {source['name']}")
            try:
                seeds = discover_source(source, limit)
            except Exception as e:  # one broken source must not stop the run
                print(f"   discovery failed: {e}")
                continue
            print(f"   {len(seeds)} links found")
            results.extend(self.process(seed) for seed in seeds)
        return results
