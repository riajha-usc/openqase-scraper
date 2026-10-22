# OpenQase MCP server

A small [Model Context Protocol](https://modelcontextprotocol.io) server so AI agents can query the OpenQase
catalogue directly. It wraps the public API at www.openqase.com, is read-only, and needs no credentials.

## Tools

| Tool | What it does |
| --- | --- |
| `search_openqase(query, type?, limit?)` | Keyword search across case studies, algorithms, industries, hardware, software, companies, personas |
| `get_case_study(slug)` | One case study in full: content, companies, algorithms, hardware, software, year, references |
| `list_case_studies(page?, page_size?)` | Compact list of published case studies with page URLs |
| `list_items(type, page?, page_size?)` | Compact list of any other content type |
| `get_item(type, slug)` | One entry of any type in full |
| `content_types()` | The types available and their public page prefixes |

Types: `case-studies`, `algorithms`, `industries`, `personas`, `quantum-hardware`, `quantum-software`,
`quantum-companies`, `partner-companies`, `blog-posts` (aliases like `hardware` or `case_study` also work).

## Run it

```bash
python -m mcp_server            # stdio: what Claude Desktop, Claude Code and Cursor expect
python -m mcp_server --http     # Streamable HTTP on http://127.0.0.1:8765/mcp
```

## Connect a client

Claude Code:

```bash
claude mcp add openqase -- /path/to/openqase-scraper/.venv/bin/python -m mcp_server
```

Claude Desktop (`claude_desktop_config.json`) or Cursor (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "openqase": {
      "command": "/path/to/openqase-scraper/.venv/bin/python",
      "args": ["-m", "mcp_server"],
      "cwd": "/path/to/openqase-scraper"
    }
  }
}
```

Then ask the agent things like "What has quantum computing done for banks?" or "Which case studies use
QAOA on D-Wave hardware?"; it will call `search_openqase` and `get_case_study` to answer from the catalogue.

## Notes

- Responses are cached in memory for 15 minutes to keep load on openqase.com low.
- Search runs over the site's own search index (`/api/search-data`): title matches rank highest, then company
  names, then descriptions.
- Tests: `pytest tests/test_mcp.py` (no network). Live check: see the protocol test in the PR description.
