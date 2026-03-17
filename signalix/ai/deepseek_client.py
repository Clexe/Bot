import asyncio, os, requests
from signalix.utils.logger import get_logger
logger = get_logger(__name__)

async def generate_rationale(signal_data: dict) -> str:
    """Generate a 3-sentence SMC rationale from DeepSeek with safe fallback."""
    prompt = f"""You are a professional forex analyst using Smart Money Concepts and Malaysian Support & Resistance. Write exactly 3 sentences of trade rationale.
Signal data: {signal_data}"""
    try:
        response = await asyncio.to_thread(requests.post, "https://api.deepseek.com/chat/completions", headers={"Authorization": f"Bearer {os.environ['DEEPSEEK_API_KEY']}"}, json={"model":"deepseek-chat","messages":[{"role":"user","content":prompt}],"max_tokens":150,"temperature":0.4}, timeout=20)
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content']
    except Exception as e:
        logger.error("DeepSeek API failed: %s", e)
        return ""
