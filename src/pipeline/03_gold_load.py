"""Gold stage: load silver tables into a local DuckDB warehouse, build
aggregate views for a CX dashboard, and re-emit the silver tables as
Parquet under data/gold/ in a partitioned layout so they're ready to
sync to S3 + Athena without rework (see README for the suggested
bucket/prefix layout).
"""

import shutil
import sys

import duckdb
import pandas as pd

import config


def main() -> None:
    tickets_path = config.SILVER_DIR / "tickets.parquet"
    resolutions_path = config.SILVER_DIR / "resolutions.parquet"
    if not tickets_path.exists() or not resolutions_path.exists():
        sys.exit("Silver tables not found. Run 02_silver_transform.py first.")

    tickets = pd.read_parquet(tickets_path)
    resolutions = pd.read_parquet(resolutions_path)

    config.GOLD_DIR.mkdir(parents=True, exist_ok=True)
    config.DUCKDB_PATH.unlink(missing_ok=True)
    con = duckdb.connect(str(config.DUCKDB_PATH))

    con.register("tickets_df", tickets)
    con.register("resolutions_df", resolutions)
    con.execute("CREATE TABLE tickets AS SELECT * FROM tickets_df")
    con.execute("CREATE TABLE resolutions AS SELECT * FROM resolutions_df")
    print(f"[gold] loaded tickets ({len(tickets):,} rows) and resolutions "
          f"({len(resolutions):,} rows) into {config.DUCKDB_PATH}")

    # -- view 1: daily ticket volume by channel --------------------------
    con.execute("""
        CREATE OR REPLACE VIEW v_daily_volume_by_channel AS
        SELECT
            CAST(created_at AS DATE) AS ticket_date,
            channel,
            COUNT(*) AS ticket_count
        FROM tickets
        GROUP BY 1, 2
        ORDER BY 1, 2
    """)

    # -- view 2: AI deflection rate by intent -----------------------------
    con.execute("""
        CREATE OR REPLACE VIEW v_deflection_rate_by_intent AS
        SELECT
            t.intent,
            COUNT(*) AS total_tickets,
            SUM(CASE WHEN r.resolved_by = 'ai' THEN 1 ELSE 0 END) AS ai_resolved_tickets,
            ROUND(
                SUM(CASE WHEN r.resolved_by = 'ai' THEN 1 ELSE 0 END)::DOUBLE / COUNT(*),
                4
            ) AS deflection_rate
        FROM tickets t
        JOIN resolutions r USING (ticket_id)
        GROUP BY 1
        ORDER BY deflection_rate DESC
    """)

    # -- view 3: avg handle time + CSAT by category -----------------------
    con.execute("""
        CREATE OR REPLACE VIEW v_handle_time_csat_by_category AS
        SELECT
            t.category,
            COUNT(*) AS total_tickets,
            ROUND(AVG(r.handle_time_seconds), 1) AS avg_handle_time_seconds,
            ROUND(AVG(r.csat_score), 2) AS avg_csat_score,
            SUM(CASE WHEN r.csat_score IS NOT NULL THEN 1 ELSE 0 END) AS csat_responses
        FROM tickets t
        JOIN resolutions r USING (ticket_id)
        GROUP BY 1
        ORDER BY 1
    """)

    print("[gold] created views: v_daily_volume_by_channel, "
          "v_deflection_rate_by_intent, v_handle_time_csat_by_category")

    print("\n[gold] sample: deflection rate by intent")
    print(con.execute("SELECT * FROM v_deflection_rate_by_intent").df().to_string(index=False))

    print("\n[gold] sample: handle time + CSAT by category")
    print(con.execute("SELECT * FROM v_handle_time_csat_by_category").df().to_string(index=False))

    con.close()

    # -- Parquet re-emit, partitioned for future S3/Athena sync ----------
    # Cleared first: partition_cols writes only touch the subdirectories
    # the current data needs, so a stale partition value from a prior
    # run (e.g. a category that no longer exists after a source swap)
    # would otherwise linger on disk indefinitely.
    parquet_dir = config.GOLD_DIR / "parquet"
    shutil.rmtree(parquet_dir, ignore_errors=True)
    tickets_out = tickets.copy()
    tickets_out["ticket_month"] = pd.to_datetime(tickets_out["created_at"]).dt.strftime("%Y-%m")
    tickets_out.to_parquet(
        parquet_dir / "tickets",
        partition_cols=["ticket_month"],
        index=False,
    )

    resolutions_out = resolutions.merge(
        tickets[["ticket_id", "category"]], on="ticket_id", how="left"
    )
    resolutions_out.to_parquet(
        parquet_dir / "resolutions",
        partition_cols=["category"],
        index=False,
    )
    print(f"\n[gold] wrote partitioned Parquet -> {parquet_dir}")


if __name__ == "__main__":
    main()
