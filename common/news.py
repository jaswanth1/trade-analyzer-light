"""
News & sentiment layer for intraday scanner.

Two data sources:
1. yfinance .news property — free headlines for each ticker
2. LLM-as-search — ask LLM for macro market context

Sentiment is scored by LLM in a single batched call to save API costs.
"""

import json
from datetime import datetime, timezone

import yfinance as yf


def fetch_stock_news(symbols: list[str]) -> dict[str, list[dict]]:
    """Fetch news from yfinance for all symbols.

    Returns {symbol: [{title, publisher, age_hours}]}.
    """
    result = {}
    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            raw_news = ticker.news or []
            headlines = []
            now_ts = datetime.now(timezone.utc).timestamp()
            for item in raw_news[:10]:  # limit to 10 most recent
                content = item.get("content", {}) if isinstance(item, dict) else {}
                title = (
                    content.get("title")
                    or item.get("title", "")
                )
                pub_ts = content.get("pubDate") or item.get("providerPublishTime")
                if pub_ts and isinstance(pub_ts, str):
                    try:
                        pub_ts = datetime.fromisoformat(pub_ts.replace("Z", "+00:00")).timestamp()
                    except (ValueError, TypeError):
                        pub_ts = None
                age_hours = (now_ts - pub_ts) / 3600 if pub_ts else None
                # Only include headlines from last 48 hours
                if age_hours is not None and age_hours > 48:
                    continue
                if title:
                    headlines.append({
                        "title": title,
                        "publisher": (
                            content.get("provider", {}).get("displayName")
                            or item.get("publisher", "")
                        ),
                        "age_hours": round(age_hours, 1) if age_hours else None,
                    })
            result[sym] = headlines
        except Exception:
            result[sym] = []
    return result


def score_news_sentiment(headlines_by_symbol: dict[str, list[dict]]) -> dict[str, dict]:
    """Use LLM to score headlines for all symbols in one batched call.

    Returns {symbol: {sentiment: -1 to +1, has_material_event: bool, summary: str}}.
    """
    from common.llm import call_llm

    # Build batched prompt
    lines = []
    symbols_with_news = []
    for sym, headlines in headlines_by_symbol.items():
        if headlines:
            titles = "; ".join(h["title"] for h in headlines[:5])
            lines.append(f"{sym}: {titles}")
            symbols_with_news.append(sym)
        else:
            lines.append(f"{sym}: No recent news")

    if not lines:
        return {}

    prompt = (
        "Score the sentiment of these Indian stock headlines for intraday trading.\n"
        "For each stock, return:\n"
        "- sentiment: -1.0 (very bearish) to +1.0 (very bullish), 0 if neutral/no news\n"
        "- material: true if earnings, M&A, regulatory action, block deal, rating change\n"
        "- summary: 1 line\n\n"
        "Headlines:\n" + "\n".join(lines) + "\n\n"
        "Return ONLY valid JSON: {\"SYMBOL.NS\": {\"sentiment\": 0.0, \"material\": false, \"summary\": \"...\"}, ...}"
    )

    response = call_llm(
        [{"role": "user", "content": prompt}],
        max_tokens=600,
        temperature=0.1,
    )

    # Parse LLM response
    result = {}
    if response and not response.startswith("[AI Error"):
        # Try to extract JSON from response
        try:
            # Handle markdown code blocks
            text = response.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            parsed = json.loads(text)
            for sym, data in parsed.items():
                result[sym] = {
                    "sentiment": float(data.get("sentiment", 0)),
                    "has_material_event": bool(data.get("material", False)),
                    "summary": str(data.get("summary", "")),
                }
        except (json.JSONDecodeError, ValueError, AttributeError):
            pass

    # Fill in defaults for symbols not in LLM response
    for sym in headlines_by_symbol:
        if sym not in result:
            result[sym] = {"sentiment": 0.0, "has_material_event": False, "summary": ""}

    return result


def fetch_market_context() -> str:
    """Ask LLM for today's macro context for Indian markets."""
    from common.llm import call_llm

    prompt = (
        "What are the top 3 market-moving events for Indian equities (NSE) today? "
        "Include: global cues (US markets, Asia), RBI/policy actions, FII/DII flow "
        "direction, any sector-specific news. Be concise — 3-4 bullet points max."
    )

    response = call_llm(
        [{"role": "user", "content": prompt}],
        max_tokens=300,
        temperature=0.3,
    )

    if response and not response.startswith("[AI Error"):
        return response
    return ""


def get_news_and_sentiment(symbols: list[str]) -> dict:
    """Main entry point. Fetch news + score sentiment for all symbols.

    Returns:
        {
            symbol: {sentiment, has_material_event, summary},
            "_market": market_context_str,
        }
    """
    # Fetch headlines
    headlines = fetch_stock_news(symbols)

    # Score sentiment in one LLM call
    sentiment = score_news_sentiment(headlines)

    # Fetch market context
    market_ctx = fetch_market_context()

    result = dict(sentiment)
    result["_market"] = market_ctx
    return result
