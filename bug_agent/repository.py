"""Read-only tools for inspecting files beneath a local repository root."""

import os
from pathlib import Path
from .tracing import traced


# Skip metadata, dependencies, generated output, and common credential folders.
EXCLUDED = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
            "dist", "build", ".next", ".aws", ".ssh"}


def hidden_secret(name):
    """Recognize common secret filenames; this does not inspect file contents."""
    lowered = name.lower()
    return lowered == ".env" or lowered.startswith(".env.") or lowered in {
        "id_rsa", "id_ed25519", "credentials", "credentials.json"} or lowered.endswith((".pem", ".key", ".p12"))


class Repository:
    """Index eligible files under one root and expose bounded inspection tools."""

    def __init__(self, root: str):
        """Validate the root and inventory up to 30,000 files of at most 1 MB each."""
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Local repo must be a directory.")
        self.files = []
        self.truncated = False
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            # Changing dirs in place prevents os.walk from entering skipped folders.
            # Windows junctions are directory links and require an explicit check.
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not hidden_secret(d)
                             and not Path(directory, d).is_symlink() and not Path(directory, d).is_junction())
            for name in sorted(files):
                path = Path(directory, name)
                if hidden_secret(name) or path.is_symlink():
                    continue
                if path.stat().st_size <= 1_000_000:
                    self.files.append(path.relative_to(self.root).as_posix())
                if len(self.files) >= 30000:
                    self.truncated = True
                    break
            if self.truncated:
                break
        # Preserve listing order in self.files; use a set for fast access checks.
        self.allowed = set(self.files)

    def _text(self, relative):
        """Read an indexed file after checking its current location, size, and format."""
        if relative not in self.allowed:
            raise ValueError("Path is not in the repository's readable file list.")
        # Recheck the resolved location in case a link replaced the indexed file.
        path = (self.root / relative).resolve(strict=True)
        if not path.is_relative_to(self.root) or path.stat().st_size > 1_000_000:
            raise ValueError("Path escapes repository or exceeds the size limit.")
        data = path.read_bytes()
        # NUL bytes are a simple binary-file heuristic, not a full format detector.
        if b"\x00" in data:
            raise ValueError("Binary file skipped.")
        return data.decode("utf-8", errors="replace")

    @traced("repository.list_files", "tool")
    def list_files(self, query=""):
        """Return up to 200 paths containing query, ignoring case, and match counts."""
        matches = [p for p in self.files if query.casefold() in p.casefold()]
        return {"files": matches[:200], "matches": len(matches), "inventory_truncated": self.truncated}

    @traced("repository.search", "tool")
    def search(self, query):
        """Find literal text across readable files and return at most 60 line matches."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Search query must be a nonempty literal string.")
        matches = []
        for relative in self.files:
            try:
                lines = self._text(relative).splitlines()
            except (ValueError, OSError):
                # Binary, deleted, or inaccessible files should not stop the search.
                continue
            for number, line in enumerate(lines, 1):
                if query.casefold() in line.casefold():
                    matches.append({"path": relative, "line": number, "text": line[:400]})
                    if len(matches) >= 60:
                        return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}

    @traced("repository.read_file", "tool")
    def read_file(self, path, start=1, end=160):
        """Read an inclusive, one-based line range, capped at 200 numbered lines."""
        if type(start) is not int or type(end) is not int or start < 1 or end < start:
            raise ValueError("Use positive integer lines with end >= start.")
        lines = self._text(path).splitlines()
        # Limit both line count and line width to keep model responses manageable.
        end = min(end, start + 199)
        return {"path": path, "total_lines": len(lines),
                "content": "\n".join(f"{n}: {lines[n - 1][:500]}" for n in range(start, min(end, len(lines)) + 1))}
