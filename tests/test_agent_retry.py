"""Tests for run_agent() in agents/definitions.py.

run_agent calls the claude CLI directly via asyncio.create_subprocess_exec.
All subprocess calls are mocked — no real CLI invocations.
"""
import pytest
import json
from unittest.mock import patch, AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cli_result(result_text: str, is_error: bool = False, returncode: int = 0):
    """Build a mock subprocess that returns a JSON envelope like claude --output-format json."""
    envelope = json.dumps({
        "type": "result",
        "subtype": "success" if not is_error else "error",
        "is_error": is_error,
        "result": result_text,
        "duration_ms": 1000,
        "num_turns": 1,
        "session_id": "test",
    })
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(envelope.encode(), b""))
    proc.returncode = returncode
    return proc


def _make_failed_proc(returncode: int = 1, stderr: str = "something went wrong"):
    """Build a mock subprocess that exits with an error."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", stderr.encode()))
    proc.returncode = returncode
    return proc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunAgent:

    @pytest.mark.asyncio
    async def test_successful_call_returns_parsed_json(self):
        proc = _make_cli_result('{"score": 8, "reasoning": "good"}')

        with patch("agents.definitions.asyncio.create_subprocess_exec", return_value=proc):
            from agents.definitions import run_agent
            result = await run_agent("system prompt", "user msg")

        assert result == {"score": 8, "reasoning": "good"}

    @pytest.mark.asyncio
    async def test_successful_call_with_markdown_json(self):
        proc = _make_cli_result('```json\n{"score": 7}\n```')

        with patch("agents.definitions.asyncio.create_subprocess_exec", return_value=proc):
            from agents.definitions import run_agent
            result = await run_agent("prompt", "msg")

        assert result == {"score": 7}

    @pytest.mark.asyncio
    async def test_cli_exit_code_nonzero_returns_error(self):
        proc = _make_failed_proc(returncode=1, stderr="Lock acquisition failed")

        with patch("agents.definitions.asyncio.create_subprocess_exec", return_value=proc):
            from agents.definitions import run_agent
            result = await run_agent("prompt", "msg")

        assert "error" in result
        assert "exit code 1" in result["error"].lower() or "lock" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_cli_is_error_true_returns_error(self):
        proc = _make_cli_result("Model not available", is_error=True)

        with patch("agents.definitions.asyncio.create_subprocess_exec", return_value=proc):
            from agents.definitions import run_agent
            result = await run_agent("prompt", "msg")

        assert "error" in result

    @pytest.mark.asyncio
    async def test_empty_result_returns_error(self):
        proc = _make_cli_result("")

        with patch("agents.definitions.asyncio.create_subprocess_exec", return_value=proc):
            from agents.definitions import run_agent
            result = await run_agent("prompt", "msg")

        assert "error" in result

    @pytest.mark.asyncio
    async def test_model_mapping_sonnet(self):
        proc = _make_cli_result('{"ok": true}')

        with patch("agents.definitions.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            from agents.definitions import run_agent
            await run_agent("prompt", "msg", model="sonnet")

        cmd = mock_exec.call_args[0]
        assert "--model" in cmd
        model_idx = list(cmd).index("--model")
        assert cmd[model_idx + 1] == "sonnet"

    @pytest.mark.asyncio
    async def test_model_mapping_opus(self):
        proc = _make_cli_result('{"ok": true}')

        with patch("agents.definitions.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            from agents.definitions import run_agent
            await run_agent("prompt", "msg", model="opus")

        cmd = mock_exec.call_args[0]
        model_idx = list(cmd).index("--model")
        assert cmd[model_idx + 1] == "opus"

    @pytest.mark.asyncio
    async def test_uses_dangerously_skip_permissions(self):
        proc = _make_cli_result('{"ok": true}')

        with patch("agents.definitions.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            from agents.definitions import run_agent
            await run_agent("prompt", "msg")

        cmd = mock_exec.call_args[0]
        assert "--dangerously-skip-permissions" in cmd

    @pytest.mark.asyncio
    async def test_uses_print_mode(self):
        proc = _make_cli_result('{"ok": true}')

        with patch("agents.definitions.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            from agents.definitions import run_agent
            await run_agent("prompt", "msg")

        cmd = mock_exec.call_args[0]
        assert "--print" in cmd
        assert "--output-format" in cmd
        fmt_idx = list(cmd).index("--output-format")
        assert cmd[fmt_idx + 1] == "json"

    @pytest.mark.asyncio
    async def test_prompt_sent_via_stdin(self):
        proc = _make_cli_result('{"ok": true}')

        with patch("agents.definitions.asyncio.create_subprocess_exec", return_value=proc):
            from agents.definitions import run_agent
            await run_agent("system prompt", "user content")

        # Verify the prompt was passed via stdin
        communicate_call = proc.communicate.call_args
        stdin_bytes = communicate_call[1].get("input") or communicate_call[0][0] if communicate_call[0] else None
        # If passed as kwarg
        if stdin_bytes is None:
            stdin_bytes = communicate_call.kwargs.get("input")
        assert stdin_bytes is not None
        stdin_text = stdin_bytes.decode("utf-8")
        assert "system prompt" in stdin_text
        assert "user content" in stdin_text

    @pytest.mark.asyncio
    async def test_subprocess_exception_returns_error(self):
        with patch("agents.definitions.asyncio.create_subprocess_exec", side_effect=OSError("spawn failed")):
            from agents.definitions import run_agent
            result = await run_agent("prompt", "msg")

        assert "error" in result
        assert "spawn failed" in result["error"]


class TestExtractJson:

    def test_direct_json(self):
        from agents.definitions import _extract_json
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_markdown_json(self):
        from agents.definitions import _extract_json
        assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_in_text(self):
        from agents.definitions import _extract_json
        result = _extract_json('Here is the analysis: {"score": 5} end')
        assert result == {"score": 5}

    def test_empty_returns_error(self):
        from agents.definitions import _extract_json
        result = _extract_json("")
        assert "error" in result

    def test_unparseable_returns_error(self):
        from agents.definitions import _extract_json
        result = _extract_json("not json at all")
        assert "error" in result
