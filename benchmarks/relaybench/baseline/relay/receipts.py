import sqlite3
from contextlib import closing


class ReceiptStore:
    def __init__(self, path):
        self.path = str(path)
        with closing(sqlite3.connect(self.path, timeout=0.2)) as connection:
            with connection:
                connection.execute("CREATE TABLE IF NOT EXISTS receipts (ticket INTEGER PRIMARY KEY, body TEXT NOT NULL)")

    def save(self, ticket, body):
        connection = sqlite3.connect(self.path, timeout=0.2)
        try:
            connection.execute("INSERT INTO receipts(ticket, body) VALUES (?, ?)", (ticket, body))
            connection.commit()
        finally:
            connection.close()

    def get(self, ticket):
        with closing(sqlite3.connect(self.path, timeout=0.2)) as connection:
            row = connection.execute("SELECT body FROM receipts WHERE ticket = ?", (ticket,)).fetchone()
            return row[0] if row else None

    def recent(self, limit=20):
        with closing(sqlite3.connect(self.path, timeout=0.2)) as connection:
            return connection.execute("SELECT ticket, body FROM receipts ORDER BY ticket DESC LIMIT ?", (limit,)).fetchall()
