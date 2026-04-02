"""Tests for run_agent() rate-limit retry logic in agents/definitions.py.

The retry logic retries up to 3 times with exponential backoff (30s, 60s, 120s)
when real rate-limit errors occur, then re-raises. Non-rate-limit errors
(including SDK parse failures containing "rate_limit") are returned as error
dicts without retrying.

All calls to claude_code_sdk.query are mocked — no real API calls.
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_message(text: str):
    """Build a fake message object matching the shape run_agent expects."""
    block = MagicMock()
    block.text = text
    msg = MagicMock()
    msg.content = [block]
    return msg


class _FakeAsyncIter:
    """Turn a list of messages into an async iterator (what query() returns)."""

    def __init__(self, messages):
        self._messages = list(messages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


def _patch_sdk(mock_query_fn):
    """Patch claude_code_sdk.query and ClaudeCodeOptions at the SDK module level.

    run_agent uses a lazy import (`from claude_code_sdk import query, ClaudeCodeOptions`)
    so we must patch them on the claude_code_sdk module itself.
    """
    return (
        patch("claude_code_sdk.query", side_effect=mock_query_fn),
        patch("claude_code_sdk.ClaudeCodeOptions", MagicMock()),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunAgentRateLimitRetry:
    """Covers the retry logic added to run_agent for rate-limit errors."""

    @pytest.mark.asyncio
    async def test_non_rate_limit_error_retries_as_transient_then_returns_error(self):
        """A non-rate-limit exception retries up to 3 times as a transient subprocess
        failure, then returns an error dict."""
        def query_fn(*a, **kw):
            raise ValueError("invalid token")

        p1, p2 = _patch_sdk(query_fn)
        with p1, p2, patch("agents.definitions.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            from agents.definitions import run_agent
            result = await run_agent("system prompt", "user msg")

        assert "error" in result
        assert "invalid token" in result["error"]
        # Retries twice with 2s delays for transient subprocess failures
        assert mock_sleep.call_count == 2
        assert all(c.args[0] == 2 for c in mock_sleep.call_args_list)

    @pytest.mark.asyncio
    async def test_rate_limit_retry_succeeds_on_second_attempt(self):
        """Rate-limit on first call, success on first retry -> returns result."""
        call_count = 0

        def query_fn(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("rate limit exceeded")
            return _FakeAsyncIter([_make_message('{"score": 8}')])

        p1, p2 = _patch_sdk(query_fn)
        with p1, p2, patch("agents.definitions.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            from agents.definitions import run_agent
            result = await run_agent("prompt", "msg")

        assert result == {"score": 8}
        # Should have slept once with first backoff delay (30s)
        mock_sleep.assert_called_once_with(30)

    @pytest.mark.asyncio
    async def test_rate_limit_retry_succeeds_on_third_attempt(self):
        """Rate-limit on first two retries, success on third."""
        call_count = 0

        def query_fn(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:  # original call + retries 1-2
                raise Exception("429 Too Many Requests")
            return _FakeAsyncIter([_make_message('{"result": "ok"}')])

        p1, p2 = _patch_sdk(query_fn)
        with p1, p2, patch("agents.definitions.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            from agents.definitions import run_agent
            result = await run_agent("prompt", "msg")

        assert result == {"result": "ok"}
        # Slept 3 times: 30, 60, 120
        assert mock_sleep.call_count == 3
        sleep_args = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_args == [30, 60, 120]

    @pytest.mark.asyncio
    async def test_rate_limit_all_retries_exhausted_reraises(self):
        """When all 3 retries also hit rate limits, the exception is re-raised."""
        def query_fn(*a, **kw):
            raise Exception("rate limit exceeded")

        p1, p2 = _patch_sdk(query_fn)
        with p1, p2, patch("agents.definitions.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            from agents.definitions import run_agent

            with pytest.raises(Exception, match="rate limit exceeded"):
                await run_agent("prompt", "msg")

        # Original call + 3 retries, but sleeps happen only for retries
        assert mock_sleep.call_count == 3
        sleep_args = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_args == [30, 60, 120]

    @pytest.mark.asyncio
    async def test_retry_hits_non_rate_limit_error_returns_error_dict(self):
        """Rate-limit on first call, then a different error on retry ->
        returns error dict for the non-rate-limit error (does not raise)."""
        call_count = 0

        def query_fn(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("overloaded")
            raise Exception("connection reset by peer")

        p1, p2 = _patch_sdk(query_fn)
        with p1, p2, patch("agents.definitions.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            from agents.definitions import run_agent
            result = await run_agent("prompt", "msg")

        assert "error" in result
        assert "connection reset by peer" in result["error"]
        # Only one sleep (for the first retry attempt)
        mock_sleep.assert_called_once_with(30)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("error_msg", [
        "rate limit exceeded",
        "429 Too Many Requests",
        "server is overloaded",
        "too many requests",
    ])
    async def test_rate_limit_detection_variants(self, error_msg):
        """Verify all rate-limit string variants trigger retry."""
        call_count = 0

        def query_fn(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception(error_msg)
            return _FakeAsyncIter([_make_message('{"ok": true}')])

        p1, p2 = _patch_sdk(query_fn)
        with p1, p2, patch("agents.definitions.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            from agents.definitions import run_agent
            result = await run_agent("prompt", "msg")

        assert result == {"ok": True}, f"Failed for error message: {error_msg}"
        assert mock_sleep.call_count >= 1, f"No retry for: {error_msg}"

    @pytest.mark.asyncio
    async def test_sdk_parse_error_not_treated_as_rate_limit(self):
        """MessageParseError('rate_limit_event') should NOT trigger rate-limit retries.

        It may retry as a transient subprocess error, but should never trigger the
        30s/60s/120s rate-limit backoff. The error dict is returned after transient retries.
        """
        class MessageParseError(Exception):
            pass

        def query_fn(*a, **kw):
            raise MessageParseError("Unknown message type: rate_limit_event")

        p1, p2 = _patch_sdk(query_fn)
        with p1, p2, patch("agents.definitions.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            from agents.definitions import run_agent
            result = await run_agent("prompt", "msg")

        assert "error" in result
        assert "rate_limit_event" in result["error"]
        # Only transient retries (2s delays), never rate-limit retries (30s+)
        for call in mock_sleep.call_args_list:
            assert call.args[0] == 2, f"Expected 2s transient delay, got {call.args[0]}s (rate limit backoff)"

    @pytest.mark.asyncio
    async def test_successful_first_call_no_retry(self):
        """When the first call succeeds, no retries or sleeps occur."""
        def query_fn(*a, **kw):
            return _FakeAsyncIter([_make_message('{"status": "good"}')])

        p1, p2 = _patch_sdk(query_fn)
        with p1, p2, patch("agents.definitions.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            from agents.definitions import run_agent
            result = await run_agent("prompt", "msg")

        assert result == {"status": "good"}
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_exponential_backoff_delays_are_correct(self):
        """Verify the exact backoff schedule: 30s, 60s, 120s."""
        call_count = 0

        def query_fn(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("too many requests")
            return _FakeAsyncIter([_make_message('{"ok": true}')])

        p1, p2 = _patch_sdk(query_fn)
        with p1, p2, patch("agents.definitions.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            from agents.definitions import run_agent
            result = await run_agent("prompt", "msg")

        assert result == {"ok": True}
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0].args[0] == 30
        assert mock_sleep.call_args_list[1].args[0] == 60
