# Codebase Bug Agent

## LangSmith tracing

Install the optional SDK once:

```powershell
python -m pip install -e ".[tracing]"
```

Create a LangSmith API key in your LangSmith account, then set it in PowerShell
(the agent does not load `.env` files):

```powershell
$env:LANGSMITH_API_KEY = 'your-langsmith-api-key'
$env:LANGSMITH_TRACING = 'true'
python -m bug_agent
```

Alternatively, set `enabled = true` in `config/tracing.toml` after setting the key.
That file also contains the project name, endpoint, and key environment variable
name. `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT`, and `LANGSMITH_TRACING` override
file settings. Use the endpoint for your LangSmith region or deployment. Tracing
is off by default; disabling it keeps the agent usable without the SDK.

Open your **codebase-bug-agent** tracing project in LangSmith after asking a question.
The trace tree records:

```text
chat_request                         # question, final response, session/thread ID
  github.read_issue                  # automatic explicit issue fetch, if applicable
  investigation_steps
    model.complete                   # messages, response, model name, endpoint
    parse_action                     # chosen action or parsing failure
    github.search_issues / read_issue # model-requested GitHub lookups
    repository.list_files / search / read_file
    ...                              # subsequent decisions and tool calls
```

Each span includes timing, inputs, outputs, and errors. One-shot `--issue` runs
have an `issue_request` parent, and `--query` records the GitHub search. Recent
conversation is captured in model messages; requests in one chat share a
session/thread ID, and `/reset` starts a new ID. Pending uploads are flushed on
normal exit. SDK upload errors appear on stderr; an enabled message means tracing
was configured, not that the server has confirmed storage.

Traces include user prompts, issue text, local source snippets, model responses,
and theoretical fix proposals, even when inference uses local Ollama. Credential
fields and known credential environment values are masked; this is not general
secret detection. HTTP headers and client objects are not captured. Traces record
observable steps, not hidden model reasoning. Token/cost accounting is not added.

SDK reference: [LangSmith tracing helpers](https://reference.langchain.com/python/langsmith/run_helpers).

Explicit number/URL lookups also accept pull requests and label their actual
type. PR summaries use the description and discussion comments; PR diffs,
review comments, and merge status are not fetched. General issue searches
continue to search issues only.

## Conversational mode

Fill in `config/repository.toml`, keep Ollama running, and start a chat:

```powershell
python -m bug_agent
```

Enter requests at the `You:` prompt, for example:

```text
You: Look up issues about crashes on startup and investigate the relevant code.
You: Focus on issue #123 instead. What could be causing it?
You: Suggest a theoretical fix and explain the tradeoffs.
You: What tests would validate that fix?
```

The model can search GitHub issues, fetch issue details, and search/read the local
clone during the conversation. It keeps recent conversation and tool evidence in
memory for follow-ups. Long conversations drop older messages to limit context;
history is not saved after exit. Type `/reset` to start fresh or `/exit` to quit.
JSON mode is enabled in `config/model.toml` to constrain model actions. Set
`json_mode = false` only for endpoints that do not support JSON mode. Invalid
actions display their validation errors, and failed turns are removed from chat
history so the next request can start cleanly.
Proposed fixes and example patches are explanations only; files are never edited
and suggested tests are not run. Existing `--issue` and `--query` commands still
work as single requests.

The configured `repo_url` can belong to any user or organization; ownership is
not required. Repository URLs, `/issues` pages, and full issue URLs are accepted.
Bare issue numbers use that default repository. To investigate an issue from
another repository or upstream, paste its full GitHub issue URL into chat. You
can also ask to search a different repository by supplying its URL in your message.
The local clone path stays the same, so ensure it contains relevant code.
Explicit references such as `#123`, `issue 123`, and full GitHub issue URLs are
fetched automatically before the model answers. If lookup fails, the actual error
is shown instead of asking the model to guess. Up to five explicit issues can be
requested per message. General issue searches still use the model's lookup actions.

A small, read-only Python agent for investigating **public GitHub issues** against
an existing local clone. Give it a repository URL, an issue number or URL, and a
folder such as `D:\repos\my-project`. It fetches the issue and comments, lets a
language model search/read the local files, and prints an investigation with
file/line references. It does not clone repositories or modify their files.

Requires Python 3.12+. No runtime dependencies. Run from this directory using
`python -m bug_agent`; optionally install with `python -m pip install -e .` to
use `bug-agent` instead.

## Local model with Ollama

### Configure once, then use short commands

Edit `config/repository.toml` with your actual local clone path and GitHub URL:

```toml
local_repo = 'D:\repos\my-project'
repo_url = 'https://github.com/owner/my-project'
```

`config/model.toml` already selects your installed `llama3:latest` through local
Ollama. Change `model` to `gemma3:4b` or `llama3.2:1b` to try your other installed
models. Model effectiveness at following the JSON action protocol will vary.
Keep Ollama running, then run from this project's directory:

```powershell
python -m bug_agent --issue 123
python -m bug_agent --query 'crash on startup'
```

The default config files are located relative to this project's package, not
the terminal's working directory. `--repo-config` and `--model-config` can select
alternative TOML files. Missing files are allowed when settings are supplied
through flags/environment. Repository flags override the repository file;
model settings use flags first, then `LLM_MODEL`/`LLM_BASE_URL`, then the model
file. API keys remain in the environment variable named by `api_key_env`.
Issue search does not load the model config or call a model.

Install/pull a model capable of following JSON instructions, and ensure Ollama
is running. For example, run `ollama pull qwen2.5-coder:7b`, then in PowerShell:

```powershell
python -m bug_agent --repo-url https://github.com/owner/project --issue 123 --local-repo 'D:\repos\project' --model qwen2.5-coder:7b
```

The default model endpoint is `http://localhost:11434/v1`. No API key is needed
for local Ollama. Model installation is separate from this agent.

## Hosted API or another local server

Use a service supporting the Chat Completions request/response format:

```powershell
$env:LLM_BASE_URL = 'https://your-provider.example/v1'
$env:LLM_MODEL = 'your-model-name'
$env:LLM_API_KEY = 'your-api-key'
python -m bug_agent --repo-url https://github.com/owner/project --issue 123 --local-repo 'D:\repos\project'
```

`--base-url` and `--model` override environment variables. `--api-key-env`
selects a different environment variable, for example `--api-key-env PROVIDER_KEY`.
Use a base URL ending in `/v1` (or the provider's equivalent); the adapter appends
`/chat/completions`. Native provider APIs with different formats need a new
adapter implementing `Model.complete(messages) -> str`; the investigation loop
does not depend on the provider. No native function-calling support is required:
the agent uses a small JSON action protocol, useful with local SLMs too.

## Find an issue first

```powershell
python -m bug_agent --repo-url https://github.com/owner/project --query 'crash on startup'
```

This prints up to 10 GitHub issue matches without a model call. Then pass a
number or the full `https://github.com/owner/project/issues/123` URL to `--issue`.
An optional `GITHUB_TOKEN` environment variable increases GitHub rate limits.

## Boundaries and implementation

- `github.py`: public GitHub issue lookup and search; other issue hosts are not yet supported.
- `model.py`: provider-independent protocol and compatible HTTP adapter.
- `repository.py`: filename listing, literal case-insensitive text search, line reads.
- `agent.py`: bounded model/tool loop; `--max-steps` defaults to 12, followed by one final report request.
- `cli.py`: configuration and user-facing command.

The agent sends the issue, file inventory, search results, and selected source
snippets to the configured model endpoint. With a hosted model, those snippets
leave your machine. With local Ollama, inference stays on your local endpoint;
GitHub lookup still requires internet. Common secret files, dependency/build
directories, symlinks, and junctions are skipped. These exclusions are best-effort,
not secret detection; only point it at source you intend the provider to read.

Files over 1 MB and binary files are skipped. The inventory is capped at 30,000
files; listings at 200 paths; searches at 60 matches; reads at 200 lines. It uses
Python traversal, so ripgrep is not required. It does not interpret `.gitignore`,
verify the clone's remote/branch, execute tests, or confirm a diagnosis. Reports
are model-generated hypotheses grounded in inspected code, and may be incorrect.
The issue body is capped at 16,000 characters and the first 30 comments at 4,000
characters each; tool responses and initial context also have character caps.

## Validation

```powershell
python -m unittest discover -s tests -v
```

API references: [GitHub issues](https://docs.github.com/en/rest/issues/issues)
and [Ollama compatibility](https://ollama.com/blog/openai-compatibility).
