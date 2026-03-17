from aiohttp import web

async def handle_paystack_webhook(request):
    """Upgrade user tier after successful Paystack payment event."""
    db = request.app['db']
    payload = await request.json()
    event = payload.get('event')
    if event == 'charge.success':
        chat_id = int(payload['data']['metadata']['telegram_chat_id'])
        tier = payload['data']['metadata'].get('tier', 'basic')
        await db.execute("UPDATE users SET tier=%s, is_active=true WHERE telegram_chat_id=%s", (tier, chat_id))
    return web.json_response({'ok': True})
