import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

from bug_agent.agent import ChatSession, chat, investigate, parse_action, issue_references
from bug_agent.cli import main
from bug_agent.config import load_config
from bug_agent.github import GitHub, parse_repo
from bug_agent.model import CompatibleModel
from bug_agent.repository import Repository


class RepositoryTests(unittest.TestCase):
    def test_search_read_and_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("def start():\n    raise RuntimeError('crash')\n", encoding="utf-8")
            (root / ".env").write_text("SECRET=crash")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "dep.js").write_text("crash")
            (root / "binary.bin").write_bytes(b"\x00crash")
            repo = Repository(folder)
            matches = repo.search("CRASH")["matches"]
            self.assertEqual([(m["path"], m["line"]) for m in matches], [("src/app.py", 2)])
            self.assertIn("2:     raise", repo.read_file("src/app.py", 2, 2)["content"])
            for path in ("../outside.txt", ".env", str(root / "src/app.py")):
                with self.assertRaises(ValueError):
                    repo.read_file(path)
            with self.assertRaises(ValueError):
                repo.read_file("src/app.py", 0)

    def test_file_replaced_by_external_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outside:
            path = Path(folder, "app.py")
            path.write_text("original")
            target = Path(outside, "private.py")
            target.write_text("private")
            repo = Repository(folder)
            path.unlink()
            try:
                path.symlink_to(target)
            except OSError:
                self.skipTest("Creating symlinks requires Windows developer mode or elevated privileges")
            with self.assertRaises(ValueError):
                repo.read_file("app.py")


class GitHubTests(unittest.TestCase):
    def test_pull_requests_are_returned_and_labeled(self):
        github = GitHub("https://github.com/owner/repo")
        item = {"html_url": "https://github.com/owner/repo/pull/123", "title": "Fix startup",
                "body": "Proposed change", "comments": 0, "pull_request": {"url": "api-url"}}
        with patch.object(github, "get", return_value=item) as get:
            for reference in ("123", "https://github.com/owner/repo/pull/123"):
                result = github.issue(reference)
                self.assertEqual(result["type"], "pull_request")
                self.assertEqual(result["body"], "Proposed change")
                get.assert_called_with("repos/owner/repo/issues/123")
        self.assertEqual(issue_references("Summarize https://github.com/owner/repo/pull/123"),
                         ["https://github.com/owner/repo/pull/123"])
        self.assertEqual(issue_references("Tell me about PR 123"), ["123"])

    def test_issue_and_comments(self):
        github = GitHub("https://github.com/owner/repo.git")
        item = {"html_url": "https://github.com/owner/repo/issues/3", "title": "Crash",
                "body": "start crashes", "comments": 1}
        with patch.object(github, "get", side_effect=[item, [{"user": {"login": "dev"}, "body": "confirmed"}]]) as get:
            result = github.issue("https://github.com/owner/repo/issues/3")
        self.assertEqual(result["comments"][0]["body"], "confirmed")
        self.assertEqual(get.call_args_list[0].args[0], "repos/owner/repo/issues/3")
        with patch.object(github, "get", return_value={**item, "comments": 0}) as get:
            github.issue("https://github.com/other/repo/issues/3")
            get.assert_called_once_with("repos/other/repo/issues/3")

    def test_repository_issue_pages_and_upstream_search(self):
        for suffix in ("", "/", "/issues", "/issues/123?test=true"):
            self.assertEqual(parse_repo("https://github.com/encode/httpx" + suffix), "encode/httpx")
        github = GitHub("https://github.com/someone/httpx")
        with patch.object(github, "get", return_value={"items": []}) as get:
            github.search("startup", repo_url="https://github.com/encode/httpx/issues")
            self.assertIn("repo%3Aencode%2Fhttpx", get.call_args.args[0])
        with self.assertRaises(ValueError):
            github.issue("https://github.com.evil.example/encode/httpx/issues/123")

    def test_host_validation_and_query_encoding(self):
        with self.assertRaises(ValueError):
            parse_repo("https://github.com.evil.example/owner/repo")
        github = GitHub("https://github.com/owner/repo")
        with patch.object(github, "get", return_value={"items": []}) as get:
            self.assertEqual(github.search("crash & hang"), [])
            self.assertIn("%26", get.call_args.args[0])


class IntegrationTests(unittest.TestCase):
    def test_explicit_issue_is_fetched_before_model_can_finish(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Mock()
            model.complete.return_value = '{"action":"finish","report":"Issue 123 concerns startup failures."}'
            github = Mock()
            github.issue.return_value = {"title": "Startup failure", "body": "Error when starting",
                                         "url": "https://github.com/owner/repo/issues/123"}
            session = ChatSession(model, Repository(folder), github)
            session.ask("Tell me about issue #123.")
            github.issue.assert_called_once_with("123")
            history = "\n".join(m["content"] for m in model.complete.call_args.args[0])
            self.assertIn("Startup failure", history)
            self.assertIn("Error when starting", history)

    def test_explicit_lookup_failure_is_reported_without_model_guess(self):
        with tempfile.TemporaryDirectory() as folder:
            model, github = Mock(), Mock()
            github.issue.side_effect = RuntimeError("HTTP 404")
            session = ChatSession(model, Repository(folder), github)
            with self.assertRaisesRegex(RuntimeError, "Could not fetch issue 123: HTTP 404"):
                session.ask("Tell me about issue #123.")
            model.complete.assert_not_called()
            self.assertEqual(len(session.messages), 2)

    def test_explicit_issue_reference_forms_and_repo_scope(self):
        self.assertEqual(issue_references("issue 123 and #456"), ["123", "456"])
        self.assertEqual(issue_references("issue number 123"), ["123"])
        self.assertEqual(issue_references("Tell me about #123 in https://github.com/encode/httpx"),
                         ["https://github.com/encode/httpx/issues/123"])
        self.assertEqual(issue_references("Read https://github.com/encode/httpx/issues/123."),
                         ["https://github.com/encode/httpx/issues/123"])
        self.assertEqual(issue_references("Look up startup crashes"), [])

    def test_failed_chat_turn_does_not_poison_followups(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Mock()
            model.complete.side_effect = ["ordinary prose", "still not JSON",
                                         '{"action":"finish","report":"Recovered"}']
            session = ChatSession(model, Repository(folder), Mock(), max_steps=1)
            with self.assertRaisesRegex(RuntimeError, "Last validation error"):
                session.ask("first request")
            self.assertEqual(len(session.messages), 2)
            self.assertEqual(session.ask("second request"), "Recovered")

    def test_action_parser_accepts_prose_wrapping_and_nested_parameters(self):
        action = parse_action('Here is the action: {"action":"search","parameters":{"query":"startup"}}')
        self.assertEqual(action["query"], "startup")

    def test_json_mode_can_be_disabled_for_other_providers(self):
        response = {"choices": [{"message": {"content": '{"action":"finish","report":"ok"}'}}]}
        with patch("bug_agent.model.request_json", return_value=response) as request:
            CompatibleModel("http://localhost/v1", "model", json_mode=False).complete([])
            self.assertNotIn("response_format", request.call_args.kwargs["payload"])
            CompatibleModel("http://localhost/v1", "model").complete([])
            self.assertEqual(request.call_args.kwargs["payload"]["response_format"], {"type": "json_object"})

    def test_chat_looks_up_issue_reads_code_and_retains_followup_context(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "app.py").write_text("raise Exception('startup crash')\n")
            model = Mock()
            model.complete.side_effect = [json.dumps(action) for action in [
                {"action": "search_issues", "query": "startup crash"},
                {"action": "read_issue", "reference": 12},
                {"action": "search", "query": "startup crash"},
                {"action": "read_file", "path": "app.py"},
                {"action": "finish", "report": "Issue 12: app.py:1 raises an exception."},
                {"action": "finish", "report": "Theoretical fix: handle the startup failure; untested."},
            ]]
            github = Mock()
            github.search.return_value = [{"number": 12, "title": "Startup crash"}]
            github.issue.return_value = {"title": "Startup crash", "body": "fails on startup"}
            session = ChatSession(model, Repository(folder), github)
            self.assertIn("app.py:1", session.ask("Look up startup crashes."))
            self.assertIn("Theoretical fix", session.ask("How would you fix that?"))
            github.search.assert_called_once_with("startup crash")
            github.issue.assert_called_once_with("12")
            history = "\n".join(m["content"] for m in session.messages)
            self.assertIn("Look up startup crashes.", history)
            self.assertIn("1: raise Exception", history)
            self.assertIn("How would you fix that?", history)

    def test_chat_reset_exit_and_eof(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Mock()
            model.complete.return_value = json.dumps({"action": "finish", "report": "Hello"})
            with patch("builtins.input", side_effect=["", "hello", "/reset", "new question", "/exit"]):
                chat(model, Repository(folder), Mock())
            last_history = model.complete.call_args.args[0]
            self.assertFalse(any(m["content"] == "hello" for m in last_history))
            self.assertTrue(any(m["content"] == "new question" for m in last_history))
            with patch("builtins.input", side_effect=EOFError):
                chat(model, Repository(folder), Mock())

    def test_no_startup_target_launches_chat(self):
        with tempfile.TemporaryDirectory() as folder, patch("bug_agent.cli.chat") as interactive:
            self.assertEqual(main(["--repo-url", "https://github.com/owner/repo",
                                   "--local-repo", folder, "--model", "llama3:latest"]), 0)
            interactive.assert_called_once()

    def test_config_driven_run_and_flag_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            repo_config = Path(folder, "repository.toml")
            repo_config.write_text("repo_url = 'https://github.com/owner/repo'\nlocal_repo = 'D:\\repos\\project'\n")
            model_config = Path(folder, "model.toml")
            model_config.write_text("model = 'llama3:latest'\nbase_url = 'http://localhost:11434/v1'\nmax_steps = 5\n")
            flags = ["--repo-config", str(repo_config), "--model-config", str(model_config), "--issue", "3"]
            with patch.dict(os.environ, {}, clear=True), patch("bug_agent.cli.GitHub") as github, \
                    patch("bug_agent.cli.Repository") as repo, patch("bug_agent.cli.CompatibleModel") as model, \
                    patch("bug_agent.cli.investigate", return_value="report") as run:
                self.assertEqual(main(flags), 0)
                repo.assert_called_with("D:\\repos\\project")
                model.assert_called_with("http://localhost:11434/v1", "llama3:latest", None, json_mode=True)
                self.assertEqual(run.call_args.args[3], 5)
                os.environ["LLM_MODEL"] = "gemma3:4b"
                self.assertEqual(main(flags), 0)
                self.assertEqual(model.call_args.args[1], "gemma3:4b")
                self.assertEqual(main(flags + ["--model", "llama3.2:1b", "--local-repo", folder]), 0)
                self.assertEqual(model.call_args.args[1], "llama3.2:1b")
                repo.assert_called_with(folder)

    def test_config_reports_bad_types_and_missing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "model.toml")
            self.assertEqual(load_config(path, {"max_steps": int}), {})
            path.write_text("max_steps = 'twelve'")
            with self.assertRaisesRegex(ValueError, "must be int"):
                load_config(path, {"max_steps": int})
            path.write_text("max_steps = [")
            with self.assertRaisesRegex(ValueError, "Invalid TOML"):
                load_config(path, {"max_steps": int})

    def test_http_adapter_and_agent_search_read_report(self):
        requests = []
        replies = ["not json", json.dumps({"action": "search", "query": "crash"}),
                   json.dumps({"action": "read_file", "path": "app.py", "start": 1, "end": 2}),
                   json.dumps({"action": "finish", "report": "Likely source: app.py:2. Not executed."})]

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append((self.path, self.headers.get("Authorization"),
                                 json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                body = json.dumps({"choices": [{"message": {"content": replies.pop(0)}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                Path(folder, "app.py").write_text("def start():\n    raise Exception('crash')\n")
                model = CompatibleModel(f"http://127.0.0.1:{server.server_port}/v1", "test-model", "test-key")
                report = investigate(model, Repository(folder), {"title": "crash"}, max_steps=5)
            self.assertIn("app.py:2", report)
            self.assertEqual(requests[0][0], "/v1/chat/completions")
            self.assertEqual(requests[0][1], "Bearer test-key")
            self.assertEqual(requests[0][2]["model"], "test-model")
            final_context = requests[-1][2]["messages"]
            self.assertIn("2:     raise", final_context[-1]["content"])
            self.assertIn("Correct the request", final_context[3]["content"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_budget_forces_final_report(self):
        class Model:
            def __init__(self):
                self.calls = 0

            def complete(self, messages):
                self.calls += 1
                return json.dumps({"action": "list_files"} if self.calls == 1 else
                                  {"action": "finish", "report": "Investigation incomplete."})

        with tempfile.TemporaryDirectory() as folder:
            model = Model()
            self.assertEqual(investigate(model, Repository(folder), {}, 1), "Investigation incomplete.")
            self.assertEqual(model.calls, 2)

    def test_query_mode_needs_no_model(self):
        with patch("bug_agent.cli.GitHub.search", return_value=[]):
            self.assertEqual(main(["--repo-url", "https://github.com/owner/repo", "--query", "crash"]), 0)


if __name__ == "__main__":
    unittest.main()
