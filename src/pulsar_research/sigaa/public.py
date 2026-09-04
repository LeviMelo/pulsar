from __future__ import annotations

from pathlib import Path

from ..config import AppConfig
from ..db import Database
from ..ingest.public_dataset import import_public_dataset
from . import public_scraper


def sync_professors(
    config: AppConfig,
    *,
    input_csv: Path | None = None,
    refresh: bool = False,
    detail_depth: int | None = None,
    max_detail_pages: int | None = None,
    delay: float | None = None,
) -> dict[str, object]:
    input_path = (input_csv or config.paths.professors_csv).resolve()
    output_dir = config.paths.public_dataset_dir.resolve()
    sigaa_cfg = config.sigaa
    argv = [
        "--input", str(input_path),
        "--output", str(output_dir),
        "--delay", str(delay if delay is not None else sigaa_cfg.get("public_delay_seconds", 0.85)),
        "--detail-depth", str(detail_depth if detail_depth is not None else sigaa_cfg.get("public_detail_depth", 1)),
        "--max-detail-pages", str(max_detail_pages if max_detail_pages is not None else sigaa_cfg.get("public_max_detail_pages", 200)),
    ]
    if refresh:
        argv.append("--refresh")
    rc = public_scraper.main(argv)
    if rc not in (0, 130):
        raise RuntimeError(f"Public professor scraper exited with code {rc}")
    db = Database(config.paths.database)
    db.initialize()
    imported = import_public_dataset(db, output_dir)
    return {"return_code": rc, "dataset_dir": str(output_dir), "imported": imported}
