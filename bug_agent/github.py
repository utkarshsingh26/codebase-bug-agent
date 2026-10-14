"""Look up public repository issues through GitHub's REST API."""

import re
from urllib.parse import urlencode, urlparse

from .http import request_json
from .tracing import traced


def parse_repo(url: str) -> str:
    """Normalize a public GitHub repository, issues-list, or issue URL to owner/repo."""
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    valid_path = len(parts) == 2 or (len(parts) == 3 and parts[2] in {"issues", "pulls"}) or (
        len(parts) == 4 and parts[2] in {"issues", "pull"} and parts[3].isdecimal())
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or not valid_path:
        raise ValueError("Use a public GitHub repository or issue URL, e.g. https://github.com/encode/httpx/issues. "
                         "The repository can belong to any user or organization.")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if not re.fullmatch(r"[\w.-]+", owner) or not re.fullmatch(r"[\w.-]+", repo):
        raise ValueError("Invalid GitHub repository name.")
    return f"{owner}/{repo}"


class GitHub:
    """Use a default repository while allowing explicit lookups in other repositories."""

    def __init__(self, repo_url: str, token: str | None = None):
        """Normalize the repository and prepare headers with optional authentication."""
        self.repo = parse_repo(repo_url)
        self.headers = {"Accept": "application/vnd.github+json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def get(self, path):
        """Fetch JSON from a relative API path using this client's headers."""
        return request_json("https://api.github.com/" + path, headers=self.headers)

    @traced("github.search_issues", "tool")
    def search(self, query, repo_url=None):
        """Search the default repository, or an explicitly supplied public repository."""
        repo = parse_repo(repo_url) if repo_url else self.repo
        params = urlencode({"q": f"{query} repo:{repo} is:issue", "per_page": 10})
        result = self.get("search/issues?" + params)
        return [{"number": i["number"], "title": i["title"], "url": i["html_url"]}
                for i in result["items"]]

    @traced("github.read_issue", "tool")
    def issue(self, reference: str):
        """Fetch an issue or PR number, or a full URL, including discussion comments."""
        repo = self.repo
        if reference.isdecimal():
            number = int(reference)
        else:
            parsed = urlparse(reference)
            match = re.fullmatch(r"/([\w.-]+)/([\w.-]+)/(?:issues|pull)/(\d+)/?", parsed.path)
            if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or not match:
                raise ValueError("Use a GitHub issue or pull request URL ending in /issues/123 or /pull/123.")
            repo = f"{match[1]}/{match[2]}"
            number = int(match[3])
        if number < 1:
            raise ValueError("Issue number must be positive.")
        path = f"repos/{repo}/issues/{number}"
        item = self.get(path)
        # Issues and PRs share numbering and this endpoint; label the actual type.
        kind = "pull_request" if "pull_request" in item else "issue"
        comments = self.get(path + "/comments?per_page=30") if item["comments"] else []
        # Cap evidence lengths so a long discussion cannot dominate model context.
        return {"url": item["html_url"], "title": item["title"], "type": kind,
                "body": (item.get("body") or "")[:16000],
                "comments": [{"author": c["user"]["login"], "body": (c.get("body") or "")[:4000]}
                             for c in comments],
                "note": "Body capped at 16000 characters; first 30 comments, each capped at 4000."}
