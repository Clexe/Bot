import asyncio
from aiohttp import web
from config import settings
from database.db import Database
from database.schema import initialize_schema
from delivery.scheduler import make_scheduler, process_delivery_queue
from delivery.telegram_bot import TelegramDelivery
from api.stats_server import make_app
from utils.logger import get_logger

logger = get_logger(__name__)

async def alive_job():
    """Health heartbeat for Railway logs to avoid cold starts."""
    logger.info("SIGNALIX ALIVE")

async def start():
    """Start database, schema, scheduler jobs, and aiohttp API server."""
    db = Database(settings.database_url)
    await db.connect()
    await initialize_schema(db)
    telegram = TelegramDelivery(settings.telegram_bot_token)
    scheduler = make_scheduler()
    scheduler.add_job(process_delivery_queue, 'interval', seconds=60, args=[db, telegram])
    scheduler.add_job(alive_job, 'interval', minutes=10)
    scheduler.start()

    app = make_app(db)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logger.info('Signalix started')
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(start())
