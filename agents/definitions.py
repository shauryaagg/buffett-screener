"""
Agent definitions for the Buffett Screener pipeline.
Uses claude-code-sdk to run Claude analysis with structured prompts.
"""
import json
import logging
from typing import Optional, Dict, Any
from claude_code_sdk import query, ClaudeCodeOptions, Message

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


async def run_agent(prompt: str, user_message: str, model: str = "sonnet") -> Dict[str, Any]:
    """
    Run a Claude agent with a system prompt and user message.
    Returns parsed JSON from the agent's response.

    Args:
        prompt: System prompt defining the agent's role
        user_message: The content to analyze
        model: Model to use ("sonnet", "opus", "haiku")
    """
    try:
        full_message = f"{prompt}\n\n---\n\nHere is the content to analyze:\n\n{user_message}"

        result_text = ""
        async for message in query(
            prompt=full_message,
            options=ClaudeCodeOptions(
                model=f"claude-{model}-4-20250514" if model in ("sonnet", "opus") else f"claude-3-5-{model}-20241022",
                max_turns=1,
            )
        ):
            if isinstance(message, Message) and message.content:
                for block in message.content:
                    if hasattr(block, 'text'):
                        result_text += block.text

        # Parse JSON from response
        return _extract_json(result_text)

    except Exception as e:
        error_str = str(e).lower()
        if "rate_limit" in error_str or "token" in error_str or "overloaded" in error_str:
            raise  # Let the pipeline handle rate limits
        logger.error(f"Agent error: {e}")
        return {"error": str(e)}


def _extract_json(text: str) -> Dict[str, Any]:
    """Extract JSON from agent response text. Handles markdown code blocks."""
    # Try direct parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        return json.loads(text[start:end].strip())

    if "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        candidate = text[start:end].strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Try finding JSON object in text
    brace_start = text.find("{")
    brace_end = text.rfind("}") + 1
    if brace_start >= 0 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start:brace_end])
        except json.JSONDecodeError:
            pass

    return {"error": "Could not parse JSON from response", "raw_text": text[:500]}


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
    if "error" in result:
        return f"[Error summarizing {year} MD&A]"
    return result.get("summary", result.get("reasoning", json.dumps(result)))


async def classify_business_type(company_name: str, sic_code: int, description: str = "") -> Dict[str, Any]:
    """Classify whether a company is a product business or commodity business."""
    msg = f"Company: {company_name}\nSIC Code: {sic_code}\nDescription: {description}"
    return await run_agent(BUSINESS_TYPE_CLASSIFIER_PROMPT, msg, model="haiku")
