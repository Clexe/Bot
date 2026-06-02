import asyncpg
from utils.logger import get_logger

logger = get_logger(__name__)


class ServerlessDB:
    """Thin wrapper over a single asyncpg connection.

    Provides the same execute/fetch/fetchrow/fetchval interface so all
    existing code works unchanged in a serverless context.
    """

    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def execute(self, query: str, *args):
        return await self.conn.execute(query, *args)

    async def fetch(self, query: str, *args):
        return await self.conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args):
        return await self.conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args):
        return await self.conn.fetchval(query, *args)
