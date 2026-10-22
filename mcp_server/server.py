"""OpenQase MCP server: tools an AI agent can call to query the public catalogue.

Run with `python -m mcp_server` (stdio, for Claude Desktop / Claude Code / Cursor) or
`python -m mcp_server --http` (Streamable HTTP on port 8765).
"""
from typing import Optional

from mcp.server.mcpserver import MCPServer

from .client import TYPES, OpenQaseClient, normalise_type

INSTRUCTIONS = (
    "OpenQase is an open catalogue of real-world quantum computing case studies, indexed by industry, algorithm, "
    "hardware, software and company. Use search_openqase to find things, get_case_study for a full write-up, "
    "and list_items / get_item for the other content types. All data is public and read-only."
)

server = MCPServer(name="openqase", title="OpenQase", instructions=INSTRUCTIONS, version="0.1.0")
_client: Optional[OpenQaseClient] = None


def client() -> OpenQaseClient:
    global _client
    if _client is None:
        _client = OpenQaseClient()
    return _client


@server.tool()
def search_openqase(query: str, type: Optional[str] = None, limit: int = 10) -> list[dict]:
    """Search the OpenQase catalogue by keyword. Returns matching case studies, algorithms, industries, hardware,
    software, companies and personas with slug, description and page URL.

    Args:
        query: Free-text keywords, e.g. "portfolio optimisation bank" or "D-Wave logistics".
        type: Optional filter: case-studies, algorithms, industries, personas, quantum-hardware, quantum-software,
            quantum-companies, partner-companies, blog-posts.
        limit: Maximum results (1-50).
    """
    api_type = normalise_type(type) if type else None
    return client().search(query, api_type, limit)


@server.tool()
def get_case_study(slug: str) -> dict:
    """Fetch one case study in full by slug: title, description, main content, partner and quantum companies,
    algorithms, hardware, software, year, academic references and resource links."""
    record = client().get_item("case-studies", slug)
    if record is None:
        return {"error": f"no case study with slug {slug!r}; use search_openqase to find the right slug"}
    return record


@server.tool()
def list_case_studies(page: int = 1, page_size: int = 20) -> dict:
    """List published case studies, newest first, with compact fields (title, slug, companies, algorithms,
    hardware, year, URL). Paginated; page_size up to 100."""
    return client().list_items("case-studies", page, page_size)


@server.tool()
def list_items(type: str, page: int = 1, page_size: int = 50) -> dict:
    """List entries of one content type: algorithms, industries, personas, quantum-hardware, quantum-software,
    quantum-companies, partner-companies or blog-posts. Compact fields plus the page URL."""
    return client().list_items(normalise_type(type), page, page_size)


@server.tool()
def get_item(type: str, slug: str) -> dict:
    """Fetch one entry of any content type in full by slug, e.g. type="algorithms", slug="qaoa"."""
    api_type = normalise_type(type)
    record = client().get_item(api_type, slug)
    if record is None:
        return {"error": f"no {api_type} entry with slug {slug!r}; use search_openqase or list_items to find it"}
    return record


@server.tool()
def content_types() -> dict:
    """The content types available on OpenQase and the public page prefix for each."""
    return {api: {"page_prefix": prefix} for api, (_, prefix) in TYPES.items()}


def main(argv: Optional[list[str]] = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(description="OpenQase MCP server")
    parser.add_argument("--http", action="store_true", help="serve over Streamable HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if args.http:
        server.run(transport="streamable-http", host="127.0.0.1", port=args.port)
    else:
        server.run()
