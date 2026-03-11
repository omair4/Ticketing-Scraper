"""
Export helpers for Tixr scrape results.

These mirror the signatures used by the other scrapers so downstream tooling
can treat all platforms uniformly.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from ..models import ScrapeResult


def _timestamp_slug() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def _safe_filename(name: str) -> str:
    # Very simple sanitisation to keep filenames filesystem-friendly
    return "".join(c for c in name if c.isalnum() or c in (" ", "_", "-", ".")).strip()


def export_to_json(result: ScrapeResult, output_dir: str) -> str:
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    event_name_slug = _safe_filename(result.event.event_name or "tixr_event")
    filename = f"{event_name_slug}_{_timestamp_slug()}.json"
    filepath = Path(output_dir) / filename

    with filepath.open("w", encoding="utf-8") as f:
        json.dump(json.loads(result.model_dump_json()), f, ensure_ascii=False, indent=2)

    return str(filepath)


def export_results(
    result: ScrapeResult, output_dir: str, formats: List[str]
) -> Dict[str, str]:
    """
    Export results in the requested formats.

    For now we only implement JSON; CSV can be added when we have real
    seat/category data from Tixr.
    """
    exported: Dict[str, str] = {}

    if "json" in formats:
        exported["json"] = export_to_json(result, output_dir)

    # Placeholder for future CSV support
    # if "csv" in formats:
    #     exported["csv"] = export_to_csv(result, output_dir)

    return exported

