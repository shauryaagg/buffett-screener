"""Tests for _extract_json() from agents/definitions.py."""
import pytest
from agents.definitions import _extract_json


class TestExtractJsonPureParse:

    def test_pure_json_string(self):
        raw = '{"score": 7, "reasoning": "solid"}'
        result = _extract_json(raw)
        assert result == {"score": 7, "reasoning": "solid"}

    def test_pure_json_with_whitespace(self):
        raw = '  \n  {"a": 1}  \n  '
        result = _extract_json(raw)
        assert result == {"a": 1}


class TestExtractJsonCodeBlocks:

    def test_json_code_block(self):
        raw = 'Here is my analysis:\n```json\n{"score": 8}\n```\nDone.'
        result = _extract_json(raw)
        assert result == {"score": 8}

    def test_generic_code_block(self):
        raw = 'Result:\n```\n{"value": 42}\n```'
        result = _extract_json(raw)
        assert result == {"value": 42}

    def test_json_code_block_with_extra_whitespace(self):
        raw = '```json\n\n  { "key" : "val" }  \n\n```'
        result = _extract_json(raw)
        assert result == {"key": "val"}


class TestExtractJsonEmbedded:

    def test_json_embedded_in_text(self):
        raw = 'I think the answer is {"result": true} and that is my analysis.'
        result = _extract_json(raw)
        assert result == {"result": True}

    def test_json_with_nested_braces(self):
        raw = 'Prefix {"outer": {"inner": 1}} suffix'
        result = _extract_json(raw)
        assert result == {"outer": {"inner": 1}}


class TestExtractJsonErrorCases:

    def test_malformed_json_in_code_block(self):
        """Malformed JSON in ```json block should not crash; falls through to brace search."""
        raw = '```json\n{bad json\n```'
        result = _extract_json(raw)
        # Falls through to brace extraction which will also fail, yielding error dict
        assert "error" in result
        assert "raw_text" in result

    def test_no_closing_backticks(self):
        """Missing closing ``` should not crash."""
        raw = '```json\n{"valid": true}'
        # No closing ```, so the ```json extraction raises ValueError on index(),
        # then falls through to brace extraction which should work.
        result = _extract_json(raw)
        assert result == {"valid": True}

    def test_completely_unparseable(self):
        raw = "This is just plain text with no JSON at all."
        result = _extract_json(raw)
        assert "error" in result
        assert "raw_text" in result
        assert "plain text" in result["raw_text"]

    def test_empty_string(self):
        result = _extract_json("")
        assert "error" in result
        assert "raw_text" in result

    def test_only_braces_but_invalid_json(self):
        raw = "{ not valid json at all }"
        result = _extract_json(raw)
        assert "error" in result

    def test_json_array_not_object(self):
        """_extract_json looks for braces, so a bare array will fail to parse
        via direct json.loads (which succeeds for arrays) — but the function
        returns any valid JSON including arrays from the first try."""
        raw = '[1, 2, 3]'
        result = _extract_json(raw)
        # json.loads succeeds on the first try for arrays
        assert result == [1, 2, 3]
