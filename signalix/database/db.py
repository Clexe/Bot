import asyncio
from contextlib import contextmanager
from psycopg2.pool import SimpleConnectionPool


class Database:
    """Async-friendly wrapper around psycopg2 SimpleConnectionPool."""

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool = None

    async def connect(self) -> None:
        """Initialize connection pool with min=2 max=10."""
        def _connect():
            self._pool = SimpleConnectionPool(2, 10, self._dsn)
        await asyncio.to_thread(_connect)

    async def reconnect(self) -> None:
        """Close and rebuild the pool after a failure."""
        await self.close()
        await self.connect()

    async def close(self) -> None:
        """Close all pooled connections."""
        if self._pool:
            await asyncio.to_thread(self._pool.closeall)
            self._pool = None

    @contextmanager
    def _conn(self):
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            self._pool.putconn(conn)

    async def execute(self, sql: str, params=None):
        """Execute write statement and commit."""
        def _run():
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                conn.commit()
        return await asyncio.to_thread(_run)

    async def fetch(self, sql: str, params=None):
        """Fetch all rows as tuples for read queries."""
        def _run():
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchall(), [d[0] for d in cur.description] if cur.description else []
        rows, cols = await asyncio.to_thread(_run)
        return [dict(zip(cols, row)) for row in rows]

    async def fetchrow(self, sql: str, params=None):
        """Fetch a single row as dictionary."""
        rows = await self.fetch(sql, params)
        return rows[0] if rows else None
