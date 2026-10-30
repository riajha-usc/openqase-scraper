"""Runtime configuration. Every value can be overridden with an environment variable."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


def _load_dotenv() -> None:
    """Minimal .env loader so the PoC has no python-dotenv dependency."""
    for name in (".env.local", ".env"):
        env_file = ROOT / name
        if not env_file.exists():
            continue
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

DB_PATH = Path(os.environ.get("OQ_DB", DATA_DIR / "candidates.db"))
MODEL = os.environ.get("OQ_MODEL", "claude-opus-5-5")
OPENQASE_API = os.environ.get("OQ_API", "https://www.openqase.com/api/case-studies")
USER_AGENT = "OpenQaseScraperPoC/0.1 (+https://openqase.com)"
FETCH_TIMEOUT = 30.0
MAX_PAGE_CHARS = int(os.environ.get("OQ_MAX_PAGE_CHARS", 40_000))  # cap on page text sent to the model
TRIAGE_CHARS = int(os.environ.get("OQ_TRIAGE_CHARS", 6_000))  # triage sees the abstract and introduction only
