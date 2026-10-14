"""Command-line configuration and coordination of GitHub, repository, and model."""

import argparse
import os
import sys

from .agent import chat, investigate
from .config import CONFIG_DIR, load_config
from .github import GitHub
from .model import CompatibleModel
from .repository import Repository
from .tracing import traced, tracing_session


def main(argv=None):
    """Run the CLI with optional tracing configured for its entire lifetime."""
    try:
        with tracing_session():
            return _main(argv)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


@traced("issue_request")
def _issue_request(model, repo, github, reference, max_steps):
    """Group a one-shot GitHub fetch and investigation beneath one parent trace."""
    issue = github.issue(reference)
    print(f"Investigating: {issue['title']}\n{issue['url']}", file=sys.stderr)
    return investigate(model, repo, issue, max_steps)


def _main(argv=None):
    """Run issue search or investigation; return 0 on success, 1 on failure, 130 on cancel.

    argv can be supplied by tests; None reads arguments from the command line.
    Flags override environment defaults and config files; keys stay in environment variables.
    """
    parser = argparse.ArgumentParser(description="Investigate a public GitHub issue using a local clone (read-only).")
    parser.add_argument("--repo-config", default=str(CONFIG_DIR / "repository.toml"), help="Repository TOML file")
    parser.add_argument("--model-config", default=str(CONFIG_DIR / "model.toml"), help="Model TOML file")
    parser.add_argument("--repo-url", help="https://github.com/owner/repo (overrides config)")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--issue", help="Issue number or full issue URL")
    target.add_argument("--query", help="Search GitHub issues and print matches, without calling a model")
    parser.add_argument("--local-repo", help=r"Path to the existing clone, e.g. D:\repos\project")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env", help="Environment variable containing the model API key")
    parser.add_argument("--max-steps", type=int)
    args = parser.parse_args(argv)
    try:
        repository_config = load_config(args.repo_config, {"local_repo": str, "repo_url": str})
        # Search mode should work even when the model file is not configured correctly.
        model_config = {} if args.query else load_config(args.model_config, {
            "model": str, "base_url": str, "api_key_env": str, "max_steps": int, "json_mode": bool})
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    args.repo_url = args.repo_url or repository_config.get("repo_url")
    args.local_repo = args.local_repo or repository_config.get("local_repo")
    args.model = args.model or os.getenv("LLM_MODEL") or model_config.get("model")
    args.base_url = args.base_url or os.getenv("LLM_BASE_URL") or model_config.get("base_url", "http://localhost:11434/v1")
    args.api_key_env = args.api_key_env or model_config.get("api_key_env", "LLM_API_KEY")
    args.max_steps = args.max_steps if args.max_steps is not None else model_config.get("max_steps", 12)
    if not args.repo_url:
        parser.error("Set repo_url in config/repository.toml or pass --repo-url.")
    if not args.query and (not args.local_repo or not args.model):
        parser.error("Set local_repo in config/repository.toml and model in config/model.toml, or pass --local-repo and --model.")
    if not 1 <= args.max_steps <= 50:
        parser.error("--max-steps must be between 1 and 50.")
    try:
        github = GitHub(args.repo_url, os.getenv("GITHUB_TOKEN"))
        if args.query:
            # Issue discovery needs GitHub only, so no local clone or model is required.
            matches = github.search(args.query)
            for item in matches:
                print(f"#{item['number']} {item['title']}\n  {item['url']}")
            if not matches:
                print("No matching issues found.")
            return 0
        # Validate/index the clone before fetching evidence and starting model calls.
        repo = Repository(args.local_repo)
        model = CompatibleModel(args.base_url, args.model, os.getenv(args.api_key_env),
                                json_mode=model_config.get("json_mode", True))
        if not args.issue:
            chat(model, repo, github, args.max_steps)
            return 0
        print("Fetching issue and comments from GitHub...", file=sys.stderr)
        print(_issue_request(model, repo, github, args.issue, args.max_steps))
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
