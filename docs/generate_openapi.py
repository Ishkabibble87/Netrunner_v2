"""Export the running FastAPI application's OpenAPI schema to this directory."""

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from api.app import app  # noqa: E402


OUTPUT = PROJECT_ROOT / "docs" / "openapi.json"
OUTPUT.write_text(
    json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
print(f"Wrote {OUTPUT.relative_to(PROJECT_ROOT)}")
