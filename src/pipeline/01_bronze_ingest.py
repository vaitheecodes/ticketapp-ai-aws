"""Bronze stage: land the raw CSV as-is, plus ingestion metadata.

No business logic lives here. The only additions to the raw rows are
lineage/audit columns (`row_id`, `source_file`, `ingested_at`) that any real
ingestion job would attach so downstream stages and debugging can trace a
row back to its origin batch. Output is partitioned by ingestion date, the
way an immutable raw landing zone typically is (`bronze/ingest_date=.../`).
"""

import hashlib
import json
import sys
from datetime import datetime, timezone

import pandas as pd

import config


def main() -> None:
    raw_path = config.RAW_DIR / config.RAW_FILENAME
    if not raw_path.exists():
        sys.exit(f"Raw file not found: {raw_path}")

    ingested_at = datetime.now(timezone.utc)
    ingest_date = ingested_at.strftime("%Y-%m-%d")

    df = pd.read_csv(raw_path, dtype=str, keep_default_na=False)

    df.insert(0, "row_id", range(len(df)))
    df["source_file"] = config.RAW_FILENAME
    df["ingested_at"] = ingested_at.isoformat()

    batch_dir = config.BRONZE_DIR / f"ingest_date={ingest_date}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    out_path = batch_dir / "raw.parquet"
    df.to_parquet(out_path, index=False)

    checksum = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    manifest = {
        "source_file": config.RAW_FILENAME,
        "source_sha256": checksum,
        "ingested_at": ingested_at.isoformat(),
        "row_count": len(df),
        "column_count": df.shape[1],
        "columns": df.columns.tolist(),
        "output_path": str(out_path.relative_to(config.PROJECT_ROOT)),
    }
    manifest_path = batch_dir / "_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print(f"[bronze] ingested {len(df):,} rows -> {out_path}")
    print(f"[bronze] manifest -> {manifest_path}")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
