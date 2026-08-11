# mnemo engine installer for native Windows (PowerShell 5.1+).
#
# This is THE machine-level command: one run takes a clean machine to a
# working, verified engine. Afterwards there is exactly one more thing to do,
# and only you can do it -- `mnemo init` inside the project you want memory
# for, because the installer cannot know which directory that is.
#
#   .\install.ps1                install or refresh, then warm, start, verify
#   .\install.ps1 -Check         report engine state, change nothing
#   .\install.ps1 -DepsOnly      refresh venv packages only, leave src\ alone
#   .\install.ps1 -InstallHome D install into D instead of the default
#   .\install.ps1 -NoModel       skip the model download
#   .\install.ps1 -Model         download it without asking (scripts)
#   .\install.ps1 -NoStart       do not start the service
#
# state\ (per-project indexes) and model-cache\ are never touched.
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$DepsOnly,
    [switch]$NoAutostart,
    [switch]$NoModel,
    [switch]$Model,
    [switch]$NoStart,
    [string]$InstallHome,
    [string]$Python
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Status {
    param([string]$Message)
    Write-Host "install.ps1: $Message"
}

function Write-Report {
    param(
        [string]$Label,
        [string]$Value
    )
    Write-Host ("install.ps1:   {0,-13} {1}" -f $Label, $Value)
}

function Invoke-Checked {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$FailureMessage
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (exit $LASTEXITCODE)"
    }
}

function Resolve-PythonCommand {
    param([string]$ExplicitPython)

    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($ExplicitPython)) {
        $candidates += [pscustomobject]@{
            Exe = $ExplicitPython
            Prefix = @()
        }
    }
    else {
        $py = Get-Command "py.exe" -ErrorAction SilentlyContinue
        if ($null -ne $py) {
            $candidates += [pscustomobject]@{
                Exe = $py.Source
                Prefix = @("-3")
            }
        }
        $pythonExe = Get-Command "python.exe" -ErrorAction SilentlyContinue
        if ($null -ne $pythonExe) {
            $candidates += [pscustomobject]@{
                Exe = $pythonExe.Source
                Prefix = @()
            }
        }
        $python3Exe = Get-Command "python3.exe" -ErrorAction SilentlyContinue
        if ($null -ne $python3Exe) {
            $candidates += [pscustomobject]@{
                Exe = $python3Exe.Source
                Prefix = @()
            }
        }
    }

    foreach ($candidate in $candidates) {
        $exe = [string]$candidate.Exe
        $prefix = @($candidate.Prefix)
        try {
            $probeCode = "import struct,sys; print('%d.%d;%d;%s;%s' % (sys.version_info[0], sys.version_info[1], struct.calcsize('P')*8, sys.executable, getattr(sys, '_base_executable', sys.executable)))"
            $probe = & $exe @prefix -c $probeCode 2>$null
            if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($probe)) {
                continue
            }
            $parts = ([string]$probe).Trim().Split(";", 4)
            $version = [version]$parts[0]
            $bits = [int]$parts[1]
            if ($version -lt [version]"3.10") {
                continue
            }
            if ($bits -ne 64) {
                throw "Python must be 64-bit; found $bits-bit at $($parts[2])"
            }
            return [pscustomobject]@{
                Exe = $exe
                Prefix = $prefix
                Version = $version
                Bits = $bits
                Interpreter = $parts[2]
                BaseInterpreter = $parts[3]
            }
        }
        catch {
            if (-not [string]::IsNullOrWhiteSpace($ExplicitPython)) {
                throw
            }
        }
    }

    throw "Python 3.10+ x64 was not found. Install 64-bit Python and retry."
}

function Test-ModelCached {
    param(
        [string]$EngineHome,
        [string]$VenvPython
    )

    # Asks the engine itself rather than looking for files: `is_model_cached`
    # knows what a *complete* snapshot is, and a half-downloaded one must read
    # as absent or the installer would skip the warmup that repairs it.
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        return $false
    }
    $probe = "import os,sys; home=sys.argv[1]; os.environ['MNEMO_HOME']=home; " +
        "sys.path.insert(0,home); from src.embedder import is_model_cached; " +
        "raise SystemExit(0 if is_model_cached() else 1)"
    & $VenvPython -c $probe $EngineHome 2>$null
    return $LASTEXITCODE -eq 0
}

function Show-CheckReport {
    param(
        [string]$EngineHome,
        [string]$VenvPython,
        [string]$Launcher
    )

    Write-Status "engine home: $EngineHome"
    Write-Report "home dir" $(if (Test-Path -LiteralPath $EngineHome -PathType Container) { "present" } else { "MISSING" })
    Write-Report "engine code" $(if (Test-Path -LiteralPath (Join-Path $EngineHome "src\cli.py") -PathType Leaf) { "present" } else { "MISSING" })
    Write-Report "venv python" $(if (Test-Path -LiteralPath $VenvPython -PathType Leaf) { "present" } else { "MISSING" })
    Write-Report "launcher" $(if (Test-Path -LiteralPath $Launcher -PathType Leaf) { "present" } else { "MISSING" })

    $deps = "MISSING / incomplete"
    if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
        # Every runtime dependency the engine imports (v2 core + v3 service layer).
        $depProbe = "import fastembed, sqlite_vec, semantic_text_splitter, mcp; " +
            "import fastapi, uvicorn, watchdog, httpx"
        & $VenvPython -c $depProbe 2>$null
        if ($LASTEXITCODE -eq 0) {
            $deps = "present"
        }
    }
    Write-Report "python deps" $deps

    $modelCached = $false
    if ($deps -eq "present") {
        $modelCached = Test-ModelCached $EngineHome $VenvPython
    }
    Write-Report "model cache" $(if ($modelCached) { "present (warmed)" } else { "empty / incomplete (run: mnemo warmup)" })

    # v3 state: the service itself, its endpoint, the API token and the bank
    # registry. state/ now holds banks.json, which is NOT reconstructible -
    # so the check reports it rather than assuming.
    $servicePid = Get-LiveServicePid $EngineHome
    $endpoint = $null
    $infoPath = Join-Path $EngineHome "state\service.json"
    if (Test-Path -LiteralPath $infoPath -PathType Leaf) {
        try {
            $info = Get-Content -LiteralPath $infoPath -Raw | ConvertFrom-Json
            if ($info.host -and $info.port) { $endpoint = "http://$($info.host):$($info.port)" }
        }
        catch { $endpoint = $null }
    }
    if ($servicePid -gt 0) {
        Write-Report "service" "running (pid $servicePid)"
    }
    else {
        Write-Report "service" "stopped"
    }
    Write-Report "endpoint" $(if ($endpoint) { $endpoint } else { "not published" })

    $health = "not checked (service down)"
    if ($servicePid -gt 0 -and $endpoint) {
        try {
            $reply = Invoke-WebRequest -Uri "$endpoint/health" -UseBasicParsing -TimeoutSec 3
            $health = if ($reply.StatusCode -eq 200) { "ok (200)" } else { "HTTP $($reply.StatusCode)" }
        }
        catch {
            $health = "UNREACHABLE (process alive, /health silent)"
        }
    }
    Write-Report "health" $health

    $tokenPath = Join-Path $EngineHome "state\api.token"
    $tokenState = "missing (created on first service start)"
    if (Test-Path -LiteralPath $tokenPath -PathType Leaf) {
        $envToken = [Environment]::GetEnvironmentVariable("MNEMO_API_TOKEN", "User")
        $tokenState = if ([string]::IsNullOrWhiteSpace($envToken)) {
            "present (but MNEMO_API_TOKEN is not exported)"
        }
        else {
            "present + exported"
        }
    }
    Write-Report "api token" $tokenState

    $banksPath = Join-Path $EngineHome "state\banks.json"
    $banksState = "none registered"
    if (Test-Path -LiteralPath $banksPath -PathType Leaf) {
        try {
            $banks = Get-Content -LiteralPath $banksPath -Raw | ConvertFrom-Json
            $entries = @($banks.banks)
            if ($entries.Count -eq 0 -and $null -ne $banks) { $entries = @($banks) }
            $banksState = "$($entries.Count) registered"
        }
        catch {
            $banksState = "PRESENT BUT UNPARSEABLE - do not delete, fix by hand"
        }
    }
    Write-Report "banks" $banksState

    # 2>&1 rather than 2>$null: under $ErrorActionPreference='Stop', PS 5.1
    # turns a native command's redirected stderr into a terminating error.
    $autostart = "disabled"
    try {
        $null = & schtasks /Query /TN "mnemo service" 2>&1
        if ($LASTEXITCODE -eq 0) { $autostart = "enabled (hidden logon task)" }
    }
    catch { }
    Write-Report "autostart" $autostart
}

function Format-Bytes {
    param([long]$Bytes)
    if ($Bytes -lt 1024) { return "$Bytes B" }
    $units = @("KiB", "MiB", "GiB", "TiB")
    $value = [double]$Bytes
    foreach ($unit in $units) {
        $value = $value / 1024.0
        if ($value -lt 1024.0) {
            return ("{0:0.#} {1}" -f $value, $unit)
        }
    }
    return ("{0:0.#} PiB" -f ($value / 1024.0))
}

function Get-LegacyEngineState {
    # A v2 engine, recognised by what v2 never had: a banks registry.
    #
    # v2 had no registry at all - it indexed one project per invocation and
    # named the file sha1(PROJECT root). v3 registers banks and names the
    # file sha1(BANK root), so every v2 index is orphaned the moment v3 runs:
    # nothing will ever open it again, and nothing in it says whose it was.
    # A v2 database has no `meta` table either, so the path cannot even be
    # recovered from the inside.
    #
    # Absent banks.json plus index files is therefore unambiguous, and it is
    # also safe: with no registry, no index can belong to a live bank.
    param([string]$EngineHome)

    $stateDir = Join-Path $EngineHome "state"
    if (-not (Test-Path -LiteralPath $stateDir -PathType Container)) { return $null }
    if (Test-Path -LiteralPath (Join-Path $stateDir "banks.json") -PathType Leaf) { return $null }

    # service.db is the v3 journal, not a bank index. It cannot be here on a
    # real v2 engine, but excluding it by name costs nothing and keeps this
    # from ever counting the engine's own log as user data.
    $indexes = @(
        Get-ChildItem -LiteralPath $stateDir -Filter "*.db" -File -ErrorAction SilentlyContinue |
            Where-Object { $_.BaseName -ne "service" }
    )
    if ($indexes.Count -eq 0) { return $null }

    $total = 0
    foreach ($index in $indexes) { $total += $index.Length }
    return [pscustomobject]@{
        Count = $indexes.Count
        Bytes = [long]$total
    }
}

function Sync-EngineCode {
    param(
        [string]$RepoRoot,
        [string]$EngineHome
    )

    $source = Join-Path $RepoRoot "src"
    $destination = Join-Path $EngineHome "src"
    if (-not (Test-Path -LiteralPath (Join-Path $source "cli.py") -PathType Leaf)) {
        throw "Run install.ps1 from the mnemo repository (src\cli.py not found)."
    }

    if (Test-Path -LiteralPath $destination) {
        Remove-Item -LiteralPath $destination -Recurse -Force
    }
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Copy-Item -Path (Join-Path $source "*") -Destination $destination -Recurse -Force

    Get-ChildItem -LiteralPath $destination -Directory -Recurse -Force |
        Where-Object { $_.Name -eq "__pycache__" } |
        Remove-Item -Recurse -Force

    foreach ($file in @("requirements.txt", "pyproject.toml", "mnemo_bootstrap.py")) {
        Copy-Item -LiteralPath (Join-Path $RepoRoot $file) `
            -Destination (Join-Path $EngineHome $file) -Force
    }
}

function Install-Launcher {
    param(
        [string]$VenvPython,
        [string]$EngineHome,
        [string]$Launcher
    )

    Invoke-Checked $VenvPython @(
        "-m", "pip", "install", "--quiet", "--no-deps",
        "--force-reinstall", $EngineHome
    ) "Failed to install the mnemo launcher"

    $generated = Join-Path $EngineHome ".venv\Scripts\mnemo.exe"
    if (-not (Test-Path -LiteralPath $generated -PathType Leaf)) {
        throw "pip did not create the expected launcher: $generated"
    }

    # Both launchers: the console one for humans and hooks, the GUI-subsystem
    # one for anything spawned in the background (Task Scheduler, autostart).
    foreach ($pair in @(
        @{ Generated = "mnemo.exe";  Target = $Launcher },
        @{ Generated = "mnemow.exe"; Target = (Join-Path (Split-Path -Parent $Launcher) "mnemow.exe") }
    )) {
        $generated = Join-Path $EngineHome (".venv\Scripts\" + $pair.Generated)
        if (-not (Test-Path -LiteralPath $generated -PathType Leaf)) {
            throw "pip did not create the expected launcher: $generated"
        }
        $target = [string]$pair.Target
        $copyRequired = $true
        if (Test-Path -LiteralPath $target -PathType Leaf) {
            $generatedHash = (Get-FileHash -LiteralPath $generated -Algorithm SHA256).Hash
            $launcherHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
            $copyRequired = $generatedHash -ne $launcherHash
        }
        if ($copyRequired) {
            try {
                Copy-Item -LiteralPath $generated -Destination $target -Force
            }
            catch {
                throw "Cannot refresh $target. The mnemo service is the likeliest holder of the lock: stop it with '$Launcher service stop' (also close Claude Code sessions using mnemo) and retry. $($_.Exception.Message)"
            }
        }
    }
}

function Confirm-ModelDownload {
    # Decide whether to fetch the model. Flags win; otherwise ask; never guess.
    if ($script:NoModel) { return $false }
    if ($script:Model) { return $true }

    # Nobody is there to answer in CI, a scheduled task or a piped run, and a
    # prompt nobody sees would either hang or read one character of the
    # caller's data as the answer. Non-interactive means: skip, and say the
    # command that finishes the job.
    $interactive = [Environment]::UserInteractive
    try {
        if ([Console]::IsInputRedirected) { $interactive = $false }
    }
    catch {
        # No console at all (hosted runspace) -- treat as non-interactive.
        $interactive = $false
    }
    if (-not $interactive) {
        Write-Status ("the embedding model is not cached; skipping the " +
            "download (non-interactive run -- pass -Model to fetch it)")
        return $false
    }

    Write-Status "the embedding model is not cached yet (~2.2 GB, one time)."
    $answer = Read-Host "install.ps1: download it now? [Y/n]"
    return ($answer.Trim() -eq "") -or ($answer.Trim().ToLowerInvariant() -in @("y", "yes"))
}

function Invoke-ModelWarmup {
    param([string]$Launcher)

    & $Launcher warmup
    if ($LASTEXITCODE -ne 0) {
        Write-Status "the model download failed; retry with '$Launcher' warmup"
        return $false
    }
    return $true
}

function Get-LiveServicePid {
    param([string]$EngineHome)

    # service.pid is ours, service.json is the backend's; either identifies a
    # process that would hold the venv and block a refresh.
    foreach ($name in @("service.pid", "service.json")) {
        $path = Join-Path $EngineHome "state\$name"
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        try {
            $data = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        }
        catch { continue }
        $candidate = 0
        if ($null -ne $data.pid) { $candidate = [int]$data.pid }
        if ($candidate -le 0) { continue }
        if ($null -ne (Get-Process -Id $candidate -ErrorAction SilentlyContinue)) {
            return $candidate
        }
    }
    return 0
}

function Stop-EngineService {
    param([string]$Launcher, [int]$ServicePid)

    if (Test-Path -LiteralPath $Launcher -PathType Leaf) {
        # A pre-v3 engine has no `service` verb; its argparse error is not a
        # problem, the force-stop below covers it. 2>&1, not 2>$null: see the
        # note in Show-CheckReport about EAP=Stop and native stderr.
        try { & $Launcher service stop 2>&1 | Out-Null } catch { }
    }
    for ($i = 0; $i -lt 20; $i++) {
        if ($null -eq (Get-Process -Id $ServicePid -ErrorAction SilentlyContinue)) {
            Write-Status "service stopped for refresh (pid $ServicePid)"
            return
        }
        Start-Sleep -Milliseconds 100
    }
    Stop-Process -Id $ServicePid -Force -ErrorAction SilentlyContinue
    Write-Status "service force-stopped for refresh (pid $ServicePid)"
}

function Set-ApiTokenEnvironment {
    param([string]$VenvPython, [string]$EngineHome)

    # The git-tracked .mcp.json refers to ${MNEMO_API_TOKEN}; without the
    # user-scope variable that placeholder never expands and MCP cannot
    # connect. Set exactly like HOME: only when absent, never overwritten.
    $existing = [Environment]::GetEnvironmentVariable("MNEMO_API_TOKEN", "User")
    if (-not [string]::IsNullOrWhiteSpace($existing)) {
        Write-Status "MNEMO_API_TOKEN already set (left untouched)"
        return
    }
    $code = "import os,sys; home=sys.argv[1]; os.environ['MNEMO_HOME']=home; " +
        "sys.path.insert(0, home); from src.api import api_token; print(api_token())"
    $token = ""
    try { $token = & $VenvPython -c $code $EngineHome 2>&1 } catch { $token = "" }
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token)) {
        Write-Status "could not read the API token; set MNEMO_API_TOKEN by hand"
        return
    }
    [Environment]::SetEnvironmentVariable("MNEMO_API_TOKEN", ([string]$token).Trim(), "User")
    Write-Status "MNEMO_API_TOKEN exported (user scope)"
    Write-Status "reopen the terminal or IDE for it to take effect"
}

function Register-PowerShellProfile {
    param([string]$Launcher)

    # A function in the profile, NOT a PATH change: mnemo stays callable by
    # name in a shell without touching machine-wide state (NFR: no PATH
    # mutation). The block is fenced so it can be rewritten or removed
    # without disturbing anything else in the user's profile.
    $begin = "# >>> mnemo >>>"
    $end = "# <<< mnemo <<<"
    $block = @(
        $begin,
        "# Added by install.ps1 - calls the installed engine launcher by full path.",
        "function mnemo { & '$Launcher' @args }",
        $end
    ) -join [Environment]::NewLine

    $profilePath = $PROFILE.CurrentUserAllHosts
    $directory = Split-Path -Parent $profilePath
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    $current = ""
    if (Test-Path -LiteralPath $profilePath -PathType Leaf) {
        $current = Get-Content -LiteralPath $profilePath -Raw
        if ($null -eq $current) { $current = "" }
    }

    if ($current -match [regex]::Escape($begin)) {
        $pattern = "(?s)" + [regex]::Escape($begin) + ".*?" + [regex]::Escape($end)
        $updated = [regex]::Replace($current, $pattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($m) $block })
        if ($updated -eq $current) {
            Write-Status "PowerShell profile already registers mnemo"
            return
        }
        Set-Content -LiteralPath $profilePath -Value $updated -Encoding UTF8
        Write-Status "PowerShell profile: mnemo block refreshed"
        return
    }

    $separator = ""
    if (-not [string]::IsNullOrWhiteSpace($current)) {
        $separator = [Environment]::NewLine + [Environment]::NewLine
    }
    Set-Content -LiteralPath $profilePath -Value ($current + $separator + $block) -Encoding UTF8
    Write-Status "PowerShell profile: mnemo registered ($profilePath)"
}

function Invoke-Install {
    $repoRoot = $PSScriptRoot
    $usingDefaultHome = [string]::IsNullOrWhiteSpace($InstallHome)
    $powerShellHome = [System.IO.Path]::GetFullPath([string]$HOME)
    $processHome = $env:HOME
    $userHome = [Environment]::GetEnvironmentVariable("HOME", "User")

    if ($usingDefaultHome) {
        foreach ($candidate in @($processHome, $userHome)) {
            if (-not [string]::IsNullOrWhiteSpace($candidate)) {
                $resolved = [System.IO.Path]::GetFullPath($candidate)
                if ($resolved -ne $powerShellHome) {
                    # MCP no longer depends on this (it is a URL + token since
                    # phase 4), but the git-tracked hook command still is
                    # `~/.claude/mnemo/bin/mnemo`, and the engine itself lives
                    # under HOME. Both break if the two disagree.
                    throw "HOME resolves to '$resolved', but PowerShell ~ resolves to '$powerShellHome'. mnemo requires both to match: the engine lives under HOME and the git-tracked hook resolves ~ at run time."
                }
            }
        }
        $engineHome = Join-Path $powerShellHome ".claude\mnemo"
    }
    else {
        $engineHome = [System.IO.Path]::GetFullPath($InstallHome)
    }

    $venvPython = Join-Path $engineHome ".venv\Scripts\python.exe"
    $launcher = Join-Path $engineHome "bin\mnemo.exe"

    if ($Check) {
        Show-CheckReport $engineHome $venvPython $launcher
        return
    }

    # Dependency-only refresh: update the venv packages and nothing else. The
    # engine code mirror and the launcher are left exactly as they are, so this
    # is safe to run while src/ is mid-refactor in the repository.
    if ($DepsOnly) {
        if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            throw "-DepsOnly needs an installed engine at $engineHome; run install.ps1 without it first."
        }
        foreach ($file in @("requirements.txt", "pyproject.toml", "mnemo_bootstrap.py")) {
            if (-not (Test-Path -LiteralPath (Join-Path $repoRoot $file) -PathType Leaf)) {
                throw "Run install.ps1 from the mnemo repository ($file not found)."
            }
        }
        Write-Status "engine home: $engineHome"
        foreach ($file in @("requirements.txt", "pyproject.toml", "mnemo_bootstrap.py")) {
            Copy-Item -LiteralPath (Join-Path $repoRoot $file) `
                -Destination (Join-Path $engineHome $file) -Force
        }
        Invoke-Checked $venvPython @("-m", "pip", "install", "--quiet", "--upgrade", "pip") "Failed to upgrade pip"
        Invoke-Checked $venvPython @("-m", "pip", "install", "--quiet", "-r", (Join-Path $engineHome "requirements.txt")) "Failed to install Python dependencies"
        # Refresh the launcher package's declared dependencies (they are read
        # from requirements.txt at build time) so `pip check` stays honest.
        # bin\mnemo.exe is deliberately not rewritten: it holds an absolute path
        # into the venv and is unaffected by a metadata-only reinstall.
        Invoke-Checked $venvPython @(
            "-m", "pip", "install", "--quiet", "--no-deps",
            "--force-reinstall", $engineHome
        ) "Failed to refresh the launcher package metadata"
        Write-Status "python deps installed (deps-only: engine code and launcher untouched)"
        return
    }

    if ($usingDefaultHome) {
        if ([string]::IsNullOrWhiteSpace($userHome)) {
            [Environment]::SetEnvironmentVariable("HOME", $powerShellHome, "User")
            Write-Status "created user HOME=$powerShellHome"
            Write-Status "close and reopen the launching terminal or IDE, then restart Claude Code"
        }
        if ([string]::IsNullOrWhiteSpace($processHome)) {
            $env:HOME = $powerShellHome
        }
    }
    else {
        Write-Status "custom InstallHome is for isolated/manual use; project wiring targets $powerShellHome\.claude\mnemo"
    }

    $pythonCommand = Resolve-PythonCommand $Python
    Write-Status "python: $($pythonCommand.Interpreter) ($($pythonCommand.Version), $($pythonCommand.Bits)-bit)"

    foreach ($directory in @(
        $engineHome,
        (Join-Path $engineHome "state"),
        (Join-Path $engineHome "model-cache"),
        (Join-Path $engineHome "bin")
    )) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    Write-Status "engine home: $engineHome"

    # Taken before the mirror, because the mirror is what makes this a v3
    # engine. Acted on further down, once the launcher exists to act with.
    $legacyEngine = Get-LegacyEngineState $engineHome
    if ($null -ne $legacyEngine) {
        Write-Status "found a v2 engine: $($legacyEngine.Count) index file(s), $(Format-Bytes $legacyEngine.Bytes)"
    }

    # stop -> refresh -> start. The running backend is the likeliest holder
    # of a lock on the venv, so it must be down before the mirror, and it is
    # only brought back if it was up in the first place.
    $servicePid = Get-LiveServicePid $engineHome
    $serviceWasRunning = $servicePid -gt 0
    if ($serviceWasRunning) {
        Stop-EngineService $launcher $servicePid
    }

    Sync-EngineCode $repoRoot $engineHome
    Write-Status "engine code refreshed"

    $venvDir = Join-Path $engineHome ".venv"
    $recreateVenv = $false
    $venvCommand = $null
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        try {
            $venvCommand = Resolve-PythonCommand $venvPython
        }
        catch {
            $recreateVenv = $true
            Write-Status "existing virtualenv is invalid; rebuilding"
        }
        if (-not $recreateVenv -and -not [string]::IsNullOrWhiteSpace($Python)) {
            $requestedBase = [System.IO.Path]::GetFullPath($pythonCommand.Interpreter)
            $existingBase = [System.IO.Path]::GetFullPath($venvCommand.BaseInterpreter)
            if ($requestedBase -ne $existingBase) {
                $recreateVenv = $true
                Write-Status "existing virtualenv uses a different Python; rebuilding"
            }
        }
    }

    if ($recreateVenv -and (Test-Path -LiteralPath $venvDir)) {
        Remove-Item -LiteralPath $venvDir -Recurse -Force
    }

    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        $exe = [string]$pythonCommand.Exe
        $prefix = @($pythonCommand.Prefix)
        Invoke-Checked $exe ($prefix + @("-m", "venv", $venvDir)) "Failed to create the virtual environment"
        Write-Status "virtualenv created"
    }
    else {
        Write-Status "virtualenv reused (Python $($venvCommand.Version), $($venvCommand.Bits)-bit)"
    }

    Invoke-Checked $venvPython @("-m", "pip", "install", "--quiet", "--upgrade", "pip") "Failed to upgrade pip"
    Invoke-Checked $venvPython @("-m", "pip", "install", "--quiet", "-r", (Join-Path $engineHome "requirements.txt")) "Failed to install Python dependencies"
    Write-Status "python deps installed"

    Install-Launcher $venvPython $engineHome $launcher
    Write-Status "launcher written: $launcher"

    # The v2 indexes go now. Not a user-scope action - this only ever touches
    # the state directory of the home being installed - so it runs for a
    # custom -InstallHome too. `clean-orphans` rather than a Remove-Item loop
    # here: it is the tested path, it refuses service.db, and it re-reads the
    # registry at the moment of deletion instead of trusting a stale listing.
    if ($null -ne $legacyEngine) {
        Write-Status "v2 keyed indexes by project root, v3 keys them by bank root:"
        Write-Status "  none of them can be reused, and nothing else will ever open them."
        & $launcher clean-orphans --yes
        if ($LASTEXITCODE -ne 0) {
            Write-Status "could not remove the v2 indexes; run '$launcher clean-orphans' by hand"
        }
        Write-Status "v2 registered no banks, so every project needs 'mnemo init' again."
        Write-Status "the check below lists the ones this machine can still find."
    }

    # User-scope registrations (env var, shell profile, logon task) belong to
    # the real engine only. A custom -InstallHome is an isolated/manual copy
    # -- and the test suite uses one -- so it must never reach out and touch
    # the user's profile or Task Scheduler.
    if ($usingDefaultHome) {
        Set-ApiTokenEnvironment $venvPython $engineHome
        Register-PowerShellProfile $launcher

        if (-not $NoAutostart) {
            & $launcher autostart enable
            if ($LASTEXITCODE -ne 0) {
                Write-Status "autostart registration failed (run '$launcher autostart enable' by hand)"
            }
        }
    }
    else {
        Write-Status "isolated home: skipped token export, profile and autostart"
    }

    # An isolated -InstallHome is a manual or test copy (the suite uses one),
    # so it gets none of this: no 2.2 GB download, no service claiming the
    # port, no verification of a thing that is not the machine's engine. It
    # only ever restores a service that this same run stopped.
    if (-not $usingDefaultHome) {
        if ($serviceWasRunning) {
            & $launcher service start
            if ($LASTEXITCODE -ne 0) {
                Write-Status "the service did not come back up; start it with '$launcher service start'"
            }
        }
        Write-Status "isolated home: skipped the model, the service and the check"
        Write-Status "done."
        return
    }

    # Model before service: the resident loads it on first use, so warming
    # now is the difference between a service that answers and one that
    # answers in two minutes.
    if (Test-ModelCached $engineHome $venvPython) {
        Write-Status "model already cached"
    }
    elseif (Confirm-ModelDownload) {
        [void](Invoke-ModelWarmup $launcher)
    }
    else {
        Write-Status "skipped the model. Search will not work until you run:"
        Write-Status "  & '$launcher' warmup"
    }

    # Started even when nothing was running before -- that is the whole point
    # on a clean machine. Registering a logon task and then leaving the
    # service down until the next reboot is the kind of half-install that
    # reads as "it does not work".
    if (-not $NoStart) {
        & $launcher service start
        if ($LASTEXITCODE -ne 0) {
            Write-Status "the service did not start; try '$launcher service start'"
        }
    }

    # End on evidence, not on a promise: the last thing on screen should be
    # the engine reporting its own state.
    Write-Status "verifying --"
    & $launcher doctor

    Write-Status "done. One thing left, and only you know where:"
    Write-Status "  cd <your project>;  & '$launcher' init"
}

# Dot-sourcing (`. .\install.ps1`) loads the functions without installing -
# that is how the tests exercise the profile and task-XML logic in isolation.
if ($MyInvocation.InvocationName -eq ".") {
    return
}

try {
    Invoke-Install
}
catch {
    [Console]::Error.WriteLine("install.ps1: ERROR: $($_.Exception.Message)")
    exit 1
}
