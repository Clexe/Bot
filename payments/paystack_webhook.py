import hashlib
import hmac
import os
from aiohttp import web
from utils.logger import get_logger

logger = get_logger(__name__)


async def handle_paystack_webhook(request):
    """Handle Paystack payment webhooks and automatic tier upgrades."""
    db = request.app["db"]

    # Verify webhook signature
    secret = os.environ.get("PAYSTACK_SECRET_KEY", "")
    if secret:
        body = await request.read()
        signature = request.headers.get("x-paystack-signature", "")
        expected = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
        if signature != expected:
            logger.warning("Invalid Paystack webhook signature")
            return web.json_response({"ok": False, "error": "Invalid signature"}, status=400)
        try:
            import json
            payload = json.loads(body)
        except Exception:
            return web.json_response({"ok": False}, status=400)
    else:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False}, status=400)

    event = payload.get("event")

    if event == "charge.success":
        try:
            metadata = payload["data"]["metadata"]
            chat_id = int(metadata["telegram_chat_id"])
            tier = metadata.get("tier", "basic")

            # Check if user exists, create if not
            existing = await db.fetchrow(
                "SELECT id FROM users WHERE telegram_chat_id=%s", (chat_id,)
            )
            if existing:
                await db.execute(
                    "UPDATE users SET tier=%s, is_active=true, paystack_customer_id=%s WHERE telegram_chat_id=%s",
                    (tier, payload["data"].get("customer", {}).get("customer_code"), chat_id),
                )
            else:
                await db.execute(
                    "INSERT INTO users (telegram_chat_id, tier, is_active, paystack_customer_id) VALUES (%s, %s, true, %s)",
                    (chat_id, tier, payload["data"].get("customer", {}).get("customer_code")),
                )
            logger.info("Tier upgrade: chat_id=%s tier=%s", chat_id, tier)
        except Exception as e:
            logger.error("Paystack webhook processing failed: %s", e)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    elif event == "subscription.disable":
        try:
            metadata = payload["data"].get("metadata", {})
            chat_id = int(metadata.get("telegram_chat_id", 0))
            if chat_id:
                await db.execute(
                    "UPDATE users SET tier='free', is_active=true WHERE telegram_chat_id=%s",
                    (chat_id,),
                )
                logger.info("Subscription cancelled: chat_id=%s downgraded to free", chat_id)
        except Exception as e:
            logger.error("Subscription disable webhook failed: %s", e)

    return web.json_response({"ok": True})
