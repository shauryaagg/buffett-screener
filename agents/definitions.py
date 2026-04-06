"""
Agent definitions for the Buffett Screener pipeline.
Calls the `claude` CLI directly in --print mode — no SDK needed.
"""
import asyncio
import json
import logging
import shutil
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

# Model aliases — Haiku isn't available via Claude Code on Max subscriptions
MODEL_MAP = {
    "opus": "opus",
    "sonnet": "sonnet",
}


def _find_claude_cli() -> str:
    """Find the claude CLI binary."""
    path = shutil.which("claude")
    if path:
        return path
    raise FileNotFoundError(
        "Claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
    )


def _is_transient_error(error_msg: str) -> bool:
    """Check if an error is transient and worth retrying."""
    lower = error_msg.lower()
    return any(t in lower for t in (
        "overloaded", "rate limit", "429", "too many requests",
        "timeout", "connection", "503", "502", "500",
    ))


async def run_agent(prompt: str, user_message: str, model: str = "sonnet", max_retries: int = 2) -> Dict[str, Any]:
    """
    Run a Claude agent via the CLI in --print mode.

    Pipes the prompt via stdin, gets JSON output back. No SDK, no subprocess
    lifecycle bugs, no message parsing issues. Just like running claude from
    a terminal. Retries on transient errors.
    """
    claude_path = _find_claude_cli()
    full_message = f"{prompt}\n\n---\n\nHere is the content to analyze:\n\n{user_message}"
    model_id = MODEL_MAP.get(model, model)

    cmd = [
        claude_path,
        "--print",
        "--model", model_id,
        "--output-format", "json",
        "--max-turns", "3",
        "--no-session-persistence",
        "--dangerously-skip-permissions",
    ]

    last_error = None
    for attempt in range(1 + max_retries):
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await proc.communicate(input=full_message.encode("utf-8"))

            if proc.returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace")[:500]
                stdout_text = stdout.decode("utf-8", errors="replace")[:1000]
                # --output-format json puts errors in stdout as JSON envelope
                error_msg = ""
                if stdout_text.strip():
                    try:
                        envelope = json.loads(stdout_text)
                        error_msg = envelope.get("result", "")
                    except json.JSONDecodeError:
                        error_msg = stdout_text[:200]
                if not error_msg:
                    error_msg = stderr_text
                last_error = error_msg or f"CLI exit code {proc.returncode}"
                logger.warning(f"claude CLI attempt {attempt+1} exited {proc.returncode}: {last_error[:200]}")
                if attempt < max_retries and _is_transient_error(last_error):
                    await asyncio.sleep(2 ** attempt)
                    continue
                return {"error": last_error}

            # Parse the JSON envelope from --output-format json
            output = stdout.decode("utf-8").strip()
            try:
                envelope = json.loads(output)
            except json.JSONDecodeError:
                # Might be plain text if --output-format json wasn't honored
                return _extract_json(output)

            if envelope.get("is_error"):
                error_msg = envelope.get("result", "Unknown CLI error")
                last_error = error_msg
                if attempt < max_retries and _is_transient_error(error_msg):
                    logger.warning(f"CLI returned is_error on attempt {attempt+1}: {error_msg[:200]}")
                    await asyncio.sleep(2 ** attempt)
                    continue
                return {"error": error_msg}

            result_text = envelope.get("result", "")
            return _extract_json(result_text)

        except Exception as e:
            last_error = str(e)
            logger.warning(f"Agent attempt {attempt+1} error: {e}")
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
                continue
            return {"error": last_error}

    return {"error": last_error or "All retries exhausted"}


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
    return await run_agent(BUSINESS_TYPE_CLASSIFIER_PROMPT, msg, model="sonnet")
