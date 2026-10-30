"""Fast human review of a list of candidate links (the maintainers' test links).

`prefetch.py` gathers metadata and text for every link once; `server.py` serves the single-page UI in
`static/index.html` and records verdicts (yes / no / lead source) and the leads extracted from lead sources.
"""
