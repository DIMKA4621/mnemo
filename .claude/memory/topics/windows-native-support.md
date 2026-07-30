# Native Windows support

## Supported contract

- Native Windows 10/11 uses built-in Windows PowerShell 5.1 and 64-bit Python 3.10+; WSL, PowerShell 7, Git Bash, and a PATH entry are not required.
- `install.ps1` installs to the canonical `%USERPROFILE%\.claude\mnemo` home, creates/reuses `.venv`, mirrors `src`, preserves `state` and `model-cache`, and writes a real `bin\mnemo.exe`.
- The Windows console executable is generated from the unique `mnemo_bootstrap` entry point. It derives engine home from `sys.prefix`, sets `MNEMO_HOME`, prepends engine home to `sys.path`, configures UTF-8 pipes, and only then imports `src.cli`. This prevents a target project's own `src` package from shadowing mnemo.
- Portable project wiring is identical on every OS:
  - MCP direct stdio command: `${HOME}/.claude/mnemo/bin/mnemo`, args `mcp`;
  - hooks: shell-form `~/.claude/mnemo/bin/mnemo <subcommand>`.
- On Windows, `HOME` and PowerShell `$HOME`/`%USERPROFILE%` must resolve to the same directory. The installer creates user `HOME` only when absent and refuses mismatches so MCP and hooks cannot target different engines. After first creating `HOME`, reopen the launching terminal/IDE before restarting Claude Code.
- `-InstallHome` is for isolated/manual checks; git-tracked wiring always targets the canonical home.
- Project root precedence is explicit root > `MNEMO_ROOT` > `CLAUDE_PROJECT_DIR` > cwd.
- Indexed relative file identifiers use POSIX `/` separators on every platform.
- Model cache readiness requires the complete current ONNX/tokenizer snapshot, not merely a non-empty cache directory. Model download remains explicit via `warmup`.

## Verification

The native Windows implementation is covered by:

- model-independent platform/scaffold/path tests;
- installer smoke tests for spaces/Unicode, canonical HOME refusal, a real extensionless-resolved `mnemo.exe`, PowerShell hook form, project-local `src` shadowing, partial model-cache detection, and state/cache preservation;
- recall regression and scoped MCP stdio checks;
- a temporary Windows project initialized under a Unicode/space path with its generated SessionStart hook executed successfully.

The local engine is installed at `C:\Users\dima\.claude\mnemo`, dependencies are present, and `multilingual-e5-large` is warmed (1024 dimensions).
