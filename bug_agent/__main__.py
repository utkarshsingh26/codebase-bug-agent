"""Enable running the package with python -m bug_agent."""

from .cli import main

# Pass the CLI's return code back to the shell for scripting and error detection.
raise SystemExit(main())
