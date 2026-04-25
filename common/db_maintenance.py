"""
Database maintenance: retention cleanup for all Supabase tables.

Deletes stale rows from regular PostgreSQL tables.
ohlcv_cache retention is handled by TimescaleDB's add_retention_policy().

Usage:
    python -m common.db_maintenance
    python -m common.db_maintenance --dry-run
"""

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Retention policies: (table, time_column, interval, description)
RETENTION_POLICIES = [
    ("analysis_cache", "computed_at", "7 days", "Stale analysis metrics"),
    ("scan_runs", "run_time", "90 days", "Old scan audit logs"),
    ("trades", "signal_time", "365 days", "Old trade records"),
    ("daily_performance", "date", "365 days", "Old daily stats"),
    ("config_snapshots", "created", "90 days", "Old config snapshots"),
]


def run_maintenance(dry_run: bool = False):
    """Run retention DELETEs on all managed tables."""
    from common.db import _get_cursor

    cur = _get_cursor()
    total_deleted = 0

    for table, col, interval, desc in RETENTION_POLICIES:
        try:
            # Check how many rows would be deleted
            cur.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {col} < NOW() - INTERVAL %s",
                [interval],
            )
            count = cur.fetchone()[0]

            if count == 0:
                log.info("%-20s — nothing to delete", table)
                continue

            if dry_run:
                log.info("%-20s — would delete %d rows (%s)", table, count, desc)
            else:
                cur.execute(
                    f"DELETE FROM {table} WHERE {col} < NOW() - INTERVAL %s",
                    [interval],
                )
                log.info("%-20s — deleted %d rows (%s)", table, count, desc)
                total_deleted += count

        except Exception as e:
            log.warning("%-20s — skipped: %s", table, e)

    # upstox_tokens: keep only the latest
    try:
        cur.execute(
            "SELECT COUNT(*) FROM upstox_tokens WHERE id NOT IN "
            "(SELECT id FROM upstox_tokens ORDER BY created_at DESC LIMIT 1)"
        )
        count = cur.fetchone()[0]
        if count > 0:
            if dry_run:
                log.info("%-20s — would delete %d old tokens", "upstox_tokens", count)
            else:
                cur.execute(
                    "DELETE FROM upstox_tokens WHERE id NOT IN "
                    "(SELECT id FROM upstox_tokens ORDER BY created_at DESC LIMIT 1)"
                )
                log.info("%-20s — deleted %d old tokens", "upstox_tokens", count)
                total_deleted += count
        else:
            log.info("%-20s — nothing to delete", "upstox_tokens")
    except Exception as e:
        log.warning("%-20s — skipped: %s", "upstox_tokens", e)

    if dry_run:
        log.info("Dry run — no rows were actually deleted")
    else:
        log.info("Total deleted: %d rows", total_deleted)


def main():
    parser = argparse.ArgumentParser(description="Run database retention maintenance")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting")
    args = parser.parse_args()

    run_maintenance(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
