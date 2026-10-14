"""Validate optional tracing with local SDK contexts and no LangSmith uploads."""

import importlib.util
import os
import unittest
from unittest.mock import patch

from bug_agent.tracing import _active, _safe, traced, tracing_session


class TracingTests(unittest.TestCase):
    def test_disabled_tracing_runs_without_sdk_calls(self):
        @traced("test")
        def operation(value):
            return value + 1

        with patch("bug_agent.tracing.load_config", return_value={"enabled": False}), \
                patch.dict(os.environ, {"LANGSMITH_TRACING": "false"}):
            with tracing_session():
                self.assertEqual(operation(2), 3)

    def test_credentials_are_masked_and_objects_not_serialized(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "secret-12345678"}):
            result = _safe({"api_key": "key", "messages": ["echo secret-12345678"], "client": object()})
        self.assertEqual(result["api_key"], "[REDACTED]")
        self.assertEqual(result["messages"], ["echo [REDACTED]"])
        self.assertEqual(result["client"], "<object>")

    @unittest.skipUnless(importlib.util.find_spec("langsmith"), "Optional LangSmith SDK not installed")
    def test_nested_spans_outputs_errors_and_session_metadata(self):
        from langsmith import get_current_run_tree, tracing_context

        seen = {}

        class Session:
            session_id = "test-thread"

            @traced("request")
            def ask(self, text):
                seen["parent"] = get_current_run_tree()
                return child(text)

        @traced("tool", "tool")
        def child(text):
            seen["child"] = get_current_run_tree()
            if text == "fail":
                raise ValueError("fixture failure")
            return text.upper()

        token = _active.set(True)
        try:
            # 'local' mode builds real SDK RunTrees without sending network requests.
            with tracing_context(enabled="local"):
                self.assertEqual(Session().ask("hello"), "HELLO")
                parent, tool = seen["parent"], seen["child"]
                self.assertEqual(tool.parent_run_id, parent.id)
                self.assertEqual(parent.metadata["session_id"], "test-thread")
                self.assertEqual(parent.inputs, {"text": "hello"})
                self.assertEqual(tool.outputs, {"result": "HELLO"})
                with self.assertRaisesRegex(ValueError, "fixture failure"):
                    Session().ask("fail")
                self.assertIn("fixture failure", seen["child"].error)
        finally:
            _active.reset(token)

    @unittest.skipUnless(importlib.util.find_spec("langsmith"), "Optional LangSmith SDK not installed")
    def test_enabled_tracing_requires_key_and_flushes_on_exit(self):
        with patch("bug_agent.tracing.load_config", return_value={"enabled": True}), \
                patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "API key"):
                with tracing_session():
                    pass
        with patch("bug_agent.tracing.load_config", return_value={"enabled": True}), \
                patch.dict(os.environ, {"LANGSMITH_API_KEY": "test-key"}, clear=True), \
                patch("langsmith.Client") as client:
            with self.assertRaises(ValueError):
                with tracing_session():
                    raise ValueError("exit early")
            client.return_value.flush.assert_called_once()
            self.assertFalse(_active.get())
