"""
Unified LLM client for all scanners.

Uses the OpenAI Python SDK — works with any OpenAI-compatible endpoint.
All config comes from .env (LLM_BASE_URL, LLM_API_KEY, LLM_MODEL).

Usage:
  from common.llm import call_llm
  response = call_llm(messages, max_tokens=800)
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _get_client():
    """Create a fresh OpenAI client from .env config."""
    from openai import OpenAI

    base_url = os.environ.get("LLM_BASE_URL")
    api_key = os.environ.get("LLM_API_KEY")

    if not base_url or not api_key:
        return None

    return OpenAI(base_url=base_url, api_key=api_key)


# ── Public API ───────────────────────────────────────────────────────────

def call_llm(messages, max_tokens=8192, temperature=0.3, max_retries=2):
    """Call the configured LLM endpoint.

    Args:
        messages: list of {"role": ..., "content": ...} dicts
        max_tokens: max response length
        temperature: sampling temperature
        max_retries: retry count if model returns empty content

    Returns:
        Response text string, or None if unavailable.
    """
    model = os.environ.get("LLM_MODEL")
    if not model:
        return None

    try:
        client = _get_client()
        if client is None:
            return None

        for attempt in range(max_retries + 1):
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content
            if content:
                return content.strip()

        return "[AI Error: model returned empty response]"
    except Exception as e:
        return f"[AI Error: {e}]"


def get_provider_info():
    """Return current LLM config for display purposes."""
    return {
        "base_url": os.environ.get("LLM_BASE_URL", "not set"),
        "model": os.environ.get("LLM_MODEL", "not set"),
    }
