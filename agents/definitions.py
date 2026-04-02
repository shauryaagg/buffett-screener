"""
Agent definitions for the Buffett Screener pipeline.
Uses claude-code-sdk to run Claude analysis with structured prompts.
"""
import asyncio
import json
import logging
from typing import Dict, Any

from config.prompts import (
    BUSINESS_ANALYST_PROMPT,
    RISK_ANALYST_PROMPT,
    MDA_ANALYST_PROMPT,
    VALUATION_AGENT_PROMPT,
    CAPITAL_ALLOCATION_PROMPT,
    MDA_SUMMARY_PROMPT,
    BUSINESS_TYPE_CLASSIFIER_PROMPT,
)

logger = logging.getLogger(__name__)


_sdk_patched = False


def _patch_sdk_message_parser():
    """Patch claude-code-sdk to handle rate_limit_event messages.

    The SDK (v0.0.25) crashes with MessageParseError on rate_limit_event
    messages from the API. These are informational stream events sent while
    the API waits for a rate limit to clear — the actual response follows.
    We patch parse_message to return a SystemMessage so the generator
    continues instead of crashing.

    Must patch both the source module AND the client module, because
    client.py does `from .message_parser import parse_message` at import
    time (holding its own reference to the original function).
    """
    global _sdk_patched
    if _sdk_patched:
        return
    try:
        from claude_code_sdk._internal import message_parser, client
        from claude_code_sdk.types import SystemMessage

        _original_parse = message_parser.parse_message

        def _patched_parse(data):
            if isinstance(data, dict):
                msg_type = data.get("type", "")
                if msg_type in ("rate_limit_event",):
                    logger.debug(f"SDK: skipping {msg_type} event")
                    return SystemMessage(subtype=msg_type, data=data)
            return _original_parse(data)

        message_parser.parse_message = _patched_parse
        client.parse_message = _patched_parse
        _sdk_patched = True
    except Exception as e:
        logger.warning(f"Could not patch SDK message parser: {e}")


async def run_agent(prompt: str, user_message: str, model: str = "sonnet") -> Dict[str, Any]:
    """
    Run a Claude agent with a system prompt and user message.
    Returns parsed JSON from the agent's response.

    Args:
        prompt: System prompt defining the agent's role
        user_message: The content to analyze
        model: Model to use ("sonnet", "opus", "haiku")
    """
    # Lazy import to avoid issues when SDK isn't installed
    from claude_code_sdk import query, ClaudeCodeOptions

    _patch_sdk_message_parser()

    try:
        full_message = f"{prompt}\n\n---\n\nHere is the content to analyze:\n\n{user_message}"

        # Map model names to full model IDs
        model_map = {
            "opus": "claude-opus-4-20250514",
            "sonnet": "claude-sonnet-4-20250514",
            "haiku": "claude-haiku-4-20250514",
        }
        model_id = model_map.get(model, f"claude-{model}-4-20250514")

        result_text = ""
        async for message in query(
            prompt=full_message,
            options=ClaudeCodeOptions(
                model=model_id,
                max_turns=1,
            )
        ):
            # Handle different message types robustly
            if hasattr(message, 'content') and message.content:
                for block in message.content:
                    if hasattr(block, 'text'):
                        result_text += block.text

        # Parse JSON from response
        return _extract_json(result_text)

    except Exception as e:
        if _is_real_rate_limit(e):
            retry_delays = [30, 60, 120]
            for attempt, delay in enumerate(retry_delays, 1):
                logger.warning(f"Rate limited — retry {attempt}/{len(retry_delays)} in {delay}s")
                await asyncio.sleep(delay)
                try:
                    result_text = ""
                    async for message in query(
                        prompt=full_message,
                        options=ClaudeCodeOptions(
                            model=model_id,
                            max_turns=1,
                        )
                    ):
                        if hasattr(message, 'content') and message.content:
                            for block in message.content:
                                if hasattr(block, 'text'):
                                    result_text += block.text
                    return _extract_json(result_text)
                except Exception as retry_e:
                    if _is_real_rate_limit(retry_e):
                        if attempt == len(retry_delays):
                            logger.error(f"Rate limit persists after {len(retry_delays)} retries — re-raising")
                            raise
                        continue
                    logger.error(f"Non-rate-limit error on retry: {retry_e}")
                    return {"error": str(retry_e)}
            raise  # Shouldn't reach here, but safety net
        logger.error(f"Agent error: {e}")
        return {"error": str(e)}


def _is_real_rate_limit(exc: Exception) -> bool:
    """Check if an exception is an actual rate limit block (not an SDK parse error).

    The claude-code-sdk sends rate_limit_event messages with status "allowed"
    after successful calls. Our monkey-patch handles these, but if the patch
    fails, the SDK throws MessageParseError containing "rate_limit_event" —
    that's NOT a real rate limit. Real rate limits come as HTTP 429 or explicit
    rate limit errors from the API, not as parse failures.
    """
    etype = type(exc).__name__
    # SDK parse errors are never real rate limits
    if "ParseError" in etype:
        return False
    error_str = str(exc).lower()
    return any(term in error_str for term in ("429", "overloaded", "rate limit exceeded", "too many requests"))


def _extract_json(text: str) -> Dict[str, Any]:
    """Extract JSON from agent response text. Handles markdown code blocks."""
    if not text or not text.strip():
        return {"error": "Empty response from agent", "raw_text": ""}

    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    if "```json" in text:
        try:
            start = text.index("```json") + 7
            end = text.index("```", start)
            return json.loads(text[start:end].strip())
        except (json.JSONDecodeError, ValueError):
            pass

    if "```" in text:
        try:
            start = text.index("```") + 3
            end = text.index("```", start)
            candidate = text[start:end].strip()
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            pass

    # Try finding JSON object in text
    brace_start = text.find("{")
    brace_end = text.rfind("}") + 1
    if brace_start >= 0 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start:brace_end])
        except json.JSONDecodeError:
            pass

    return {"error": "Could not parse JSON from response", "raw_text": text[:2000]}


async def analyze_business_description(item1_text: str) -> Dict[str, Any]:
    """Analyze Item 1 (Business Description) of a 10-K."""
    return await run_agent(BUSINESS_ANALYST_PROMPT, item1_text, model="sonnet")


async def analyze_risk_factors(item1a_text: str) -> Dict[str, Any]:
    """Analyze Item 1A (Risk Factors) of a 10-K."""
    return await run_agent(RISK_ANALYST_PROMPT, item1a_text, model="sonnet")


async def analyze_mda(item7_text: str) -> Dict[str, Any]:
    """Analyze Item 7 (MD&A) of a 10-K. Uses Opus for the hardest qualitative judgment."""
    return await run_agent(MDA_ANALYST_PROMPT, item7_text, model="opus")


async def run_valuation_analysis(financial_summary: str, business_context: str) -> Dict[str, Any]:
    """Run the valuation agent. Uses Opus."""
    combined = f"FINANCIAL SUMMARY:\n{financial_summary}\n\nBUSINESS CONTEXT:\n{business_context}"
    return await run_agent(VALUATION_AGENT_PROMPT, combined, model="opus")


async def run_capital_allocation_analysis(mda_summaries: str, financial_trends: str) -> Dict[str, Any]:
    """Run the capital allocation synthesis agent. Uses Opus."""
    combined = f"10-YEAR MD&A SUMMARIES:\n{mda_summaries}\n\nFINANCIAL TRENDS:\n{financial_trends}"
    return await run_agent(CAPITAL_ALLOCATION_PROMPT, combined, model="opus")


async def summarize_mda_for_capital(mda_text: str, year: str) -> str:
    """Extract capital allocation commentary from a single year's MD&A. Uses Sonnet."""
    result = await run_agent(MDA_SUMMARY_PROMPT, f"Year: {year}\n\n{mda_text}", model="sonnet")
    if "error" in result and "raw_text" in result:
        # Agent returned plain text (not JSON) — that's fine for summaries
        return result["raw_text"]
    if "error" in result:
        return f"[Error summarizing {year} MD&A]"
    return result.get("summary", result.get("reasoning", json.dumps(result)))


async def classify_business_type(company_name: str, sic_code: int, description: str = "") -> Dict[str, Any]:
    """Classify whether a company is a product business or commodity business."""
    msg = f"Company: {company_name}\nSIC Code: {sic_code}\nDescription: {description}"
    return await run_agent(BUSINESS_TYPE_CLASSIFIER_PROMPT, msg, model="haiku")
