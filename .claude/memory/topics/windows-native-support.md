# Native Windows support

## Supported contract

- Native Windows 10/11 uses built-in Windows PowerShell 5.1 and 64-bit Python 3.10+; WSL, PowerShell 7, Git Bash, and a PATH entry are not required.
- `install.ps1` installs to the canonical `%USERPROFILE%\.claude\mnemo` home, preserves `state` and `model-cache`, and writes real `bin\mnemo.exe`/`mnemow.exe`.
  **Змінено 2026-08-20 (unstaged, self-update — див. вище й `topics/engine-self-update-design.md`):** код і venv більше не в корені, а під `versions/<tag>/{src,.venv}`, з `current` (junction) на активну версію; `install.ps1`'s `Build-EngineVersion` збирає одну версійну теку (код-мірор + повний runtime venv + перевірка `sqlite-vec`), `Set-CurrentVersion` перемикає junction, `Publish-Launchers` копіює стабільні exe в `bin\`.
  Свіжий інстал сьогодні кладе `versions\local\`.
- The Windows console executable is generated from the unique `mnemo_bootstrap` entry point.
  **Змінено 2026-08-20 (unstaged, робота над self-update рушія — `topics/engine-self-update-design.md`):** воно більше не імпортує `src.cli` in-process.
  `_engine_home()` тепер резолвиться через `sys.argv[0]` (не `sys.prefix` — той лишається прив'язаний до venv, в якому `pip install --no-deps` зібрав exe на етапі білда, а не до місця реального запуску), і `main()` **спавнить subprocess** `current/.venv/Scripts/(python|pythonw).exe -m src.cli <argv>` зі успадкованим stdio/exit code.
  Причина: версійний venv (`versions/<tag>/.venv/`) означає, що більше немає одного статичного venv, на який можна було б покластися in-process.
  `MNEMO_HOME` лишається **неверсійним** (`state/`/`model-cache/` спільні), `current` — junction на активну версію.
  Це прибирає стару обіцянку («This prevents a target project's own `src` package from shadowing mnemo») — вона й досі правдива по суті (dispatched subprocess так само не бачить чужого `src`), просто механізм інший.
- Portable project wiring is identical on every OS:
  - MCP direct stdio command: `${HOME}/.claude/mnemo/bin/mnemo`, args `mcp`;
  - hooks: shell-form `~/.claude/mnemo/bin/mnemo <subcommand>`.
- On Windows, `HOME` and PowerShell `$HOME`/`%USERPROFILE%` must resolve to the same directory.
  The installer creates user `HOME` only when absent and refuses mismatches so MCP and hooks cannot target different engines.
  After first creating `HOME`, reopen the launching terminal/IDE before restarting Claude Code.
- `-InstallHome` is for isolated/manual checks; git-tracked wiring always targets the canonical home.
- Project root precedence is explicit root > `MNEMO_ROOT` > `CLAUDE_PROJECT_DIR` > cwd.
- Indexed relative file identifiers use POSIX `/` separators on every platform.
- Model cache readiness requires the complete current ONNX/tokenizer snapshot, not merely a non-empty cache directory.
  Model download remains explicit via `warmup`.

## Verification

The native Windows implementation is covered by:

- model-independent platform/scaffold/path tests;
- installer smoke tests for spaces/Unicode, canonical HOME refusal, a real extensionless-resolved `mnemo.exe`, PowerShell hook form, project-local `src` shadowing, partial model-cache detection, and state/cache preservation;
- recall regression and scoped MCP stdio checks;
- a temporary Windows project initialized under a Unicode/space path with its generated SessionStart hook executed successfully.

The local engine is installed at `C:\Users\dima\.claude\mnemo`, dependencies are present, and `multilingual-e5-large` is warmed (1024 dimensions).
