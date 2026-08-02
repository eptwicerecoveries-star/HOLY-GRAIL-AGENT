from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = PROJECT_ROOT / "data" / "test_pdfs"
EXPECTED_DIR = CORPUS_DIR / "expected"


def corpus_pdfs() -> list[Path]:
    """Every PDF in the corpus, discovered at collection time.

    Discovery is deliberate: dropping a new county PDF into the folder adds it to every
    corpus-wide test without touching any code, so coverage grows as counties are added.
    """
    return sorted(CORPUS_DIR.glob("*.pdf"))


def corpus_ids() -> list[str]:
    return [p.stem for p in corpus_pdfs()]


def expectations_for(pdf_path: Path) -> dict[str, Any] | None:
    """Load the golden file for a PDF, if one has been recorded yet."""
    candidate = EXPECTED_DIR / f"{pdf_path.stem}.json"
    if not candidate.is_file():
        return None
    return json.loads(candidate.read_text())


def pdfs_with_expectations() -> list[Path]:
    return [p for p in corpus_pdfs() if expectations_for(p) is not None]
