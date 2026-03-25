import asyncio
import os
import requests
from utils.logger import get_logger

logger = get_logger(__name__)


async def generate_precision_rationale(signal_data: dict) -> str:
    """Generate 3-sentence Precision rationale referencing COT + Wyckoff + SMC confluence.

    Temperature: 0.4, max_tokens: 150.
    Fallback: empty string on failure — signal still sends without rationale.
    """
    prompt = (
        "You are a professional institutional forex analyst using Smart Money Concepts, "
        "Wyckoff methodology, and COT data analysis. Write exactly 3 sentences of trade rationale "
        "for this Precision signal. Reference the COT positioning, Wyckoff phase, and SMC confluence "
        "factors. Be specific about the institutional narrative.\n\n"
        f"Signal data: {signal_data}"
    )
    return await _call_deepseek(prompt, max_tokens=150)


async def generate_flow_rationale(signal_data: dict) -> str:
    """Generate 2-sentence Flow rationale referencing intraday structure only.

    Temperature: 0.4, max_tokens: 100.
    Fallback: empty string on failure.
    """
    prompt = (
        "You are a professional intraday forex analyst using Smart Money Concepts. "
        "Write exactly 2 sentences of trade rationale for this Flow signal. "
        "Reference only intraday structure, CHoCH, FVG, and session bias. "
        "Do not mention COT or Wyckoff.\n\n"
        f"Signal data: {signal_data}"
    )
    return await _call_deepseek(prompt, max_tokens=100)


async def _call_deepseek(prompt: str, max_tokens: int) -> str:
    """Call DeepSeek API with safe fallback."""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        logger.warning("DEEPSEEK_API_KEY not set, skipping rationale")
        return ""

    try:
        response = await asyncio.to_thread(
            requests.post,
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.4,
            },
            timeout=20,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error("DeepSeek API failed: %s", e)
        return ""
