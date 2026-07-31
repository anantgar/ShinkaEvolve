"""Small asynchronous I/O helpers used by repository evolution."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


async def write_text_async(path: str | Path, content: str) -> bool:
    """Write UTF-8 text without blocking the event loop."""
    target = Path(path)
    try:
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_text, content, encoding="utf-8")
        return True
    except Exception as exc:
        logger.error("Error writing %s: %s", target, exc)
        return False


async def get_text_embedding_async(
    text: str, embedding_client, max_chars: int = 10000
) -> Tuple[Optional[list], float]:
    """Generate an embedding for bounded text with an async or sync client."""
    bounded_text = text[:max_chars]
    try:
        if hasattr(embedding_client, "embed_async"):
            return await embedding_client.embed_async(bounded_text)
        return await asyncio.to_thread(
            embedding_client.get_embedding,
            bounded_text,
        )
    except Exception as exc:
        logger.error("Error generating text embedding: %s", exc)
        return None, 0.0
