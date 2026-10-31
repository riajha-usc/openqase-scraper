#!/usr/bin/env python
"""Command-line entry point for the scraper PoC. Run `python cli.py --help`."""
import json
from typing import Optional

import typer

app = typer.Typer(no_args_is_help=True, help="OpenQase case study scraper PoC")


@app.command()
def discover(source: Optional[list[str]] = typer.Option(None, help="Source name(s) from sources.yaml; default all"),
             limit: int = typer.Option(10, help="Max links per source")):
    """Crawl the configured sources, triage inline, extract, and queue what passes."""
    from scraper.pipeline import Runner
    Runner().run_sources(source or None, limit=limit)
    stats()


@app.command()
def run(urls: list[str] = typer.Argument(..., help="One or more page URLs (or local HTML files)"),
        source_name: str = typer.Option("manual"),
        provenance: Optional[int] = typer.Option(None, min=1, max=3, help="Override the provenance score"),
        rerun: bool = typer.Option(False, help="Forget earlier results for these URLs and process them again")):
    """Run the agent over specific URLs, e.g. the maintainers' test links."""
    from scraper.pipeline import Runner
    Runner().run_urls(urls, source_name=source_name, provenance=provenance, rerun=rerun)
    stats()


@app.command()
def review(port: int = 8000, include_skipped: bool = typer.Option(False, help="Also show skipped records")):
    """Start the approval UI at http://localhost:PORT."""
    import os
    import uvicorn
    if include_skipped:
        os.environ["OQ_REVIEW_INCLUDE_SKIPPED"] = "1"
    uvicorn.run("scraper.review.app:app", host="127.0.0.1", port=port, reload=False)


@app.command()
def synth(n: int = typer.Option(5, help="Number of published case studies to base articles on"),
          seed: int = 0,
          slug: Optional[list[str]] = typer.Option(None, help="Specific case study slug(s) instead of a random sample"),
          force: bool = typer.Option(False, help="Regenerate case studies that already have articles")):
    """Generate labelled synthetic articles from published OpenQase case studies."""
    from scraper.synth import generate
    generate(n=n, seed=seed, slugs=slug or None, force=force)


@app.command("eval")
def eval_cmd():
    """Run the agent over the synthetic set and report precision/recall."""
    from scraper.evaluate import evaluate
    evaluate()


@app.command()
def rejudge(source_name: str = typer.Option("spreadsheet", help="Re-judge every stored page from this source")):
    """Re-run triage and extraction on the stored page text, without fetching anything."""
    from scraper.pipeline import Runner
    Runner().rejudge(source_name)
    stats()


@app.command()
def stats():
    """Counts by status."""
    from scraper import db
    conn = db.connect()
    for status, n in db.counts(conn).items():
        print(f"{status:<15}{n}")


@app.command("list")
def list_cmd(status: str = "queued", limit: int = 50):
    """List candidates in a status."""
    from scraper import db
    conn = db.connect()
    for row in db.list_status(conn, status, limit):
        print(f"#{row['id']:<4} p{row['provenance'] or '-'} {row['organisation'] or '-'} | {row['title'] or '-'}\n      {row['url']}")


@app.command()
def show(candidate_id: int):
    """Print one candidate as JSON."""
    from scraper import db
    row = db.get(db.connect(), candidate_id)
    if not row:
        raise typer.Exit(code=1)
    record = dict(row)
    for key in ("triage_json", "extraction_json"):
        if record.get(key):
            record[key] = json.loads(record[key])
    print(json.dumps(record, indent=2, ensure_ascii=False))


@app.command()
def reset_skipped():
    """Return skipped records to the queue (the start of a new review session)."""
    from scraper import db
    print(f"{db.reset_skipped(db.connect())} records back in the queue")


if __name__ == "__main__":
    app()
