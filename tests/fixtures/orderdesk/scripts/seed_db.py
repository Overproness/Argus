"""Seed an on-disk OrderDesk database. Usage: ORDERDESK_DB=orderdesk.db python scripts/seed_db.py"""

import sqlite3
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 2)[0])


def main() -> None:
    for attempt in range(5):
        try:
            from shop import db

            db.seed()
            print("seeded")
            return
        except sqlite3.OperationalError as exc:
            print(f"database busy ({exc}), retrying", file=sys.stderr)
            time.sleep(0.5 * (attempt + 1))
    sys.exit("could not seed database")


if __name__ == "__main__":
    main()
