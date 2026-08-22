# mnemo engine uninstaller for native Windows (PowerShell 5.1+).
#
# The exact inverse of install.ps1: it removes everything that installer
# writes to this machine, and nothing else. Your projects, their `.md` and
# this repository are never touched -- the markdown is the source of truth,
# so what goes here is only the derived, rebuildable half.
#
#   .\uninstall.ps1                 remove it all (shows the list, asks first)
#   .\uninstall.ps1 -DryRun         show what would go, change nothing
#   .\uninstall.ps1 -Yes            do not ask (scripts)
#   .\uninstall.ps1 -KeepModel      leave model-cache\ (~2.2 GB) in place
#   .\uninstall.ps1 -KeepState      leave state\ (registry, indexes, journal)
#   .\uninstall.ps1 -InstallHome D  uninstall an isolated copy instead
#
# An uninstaller is reached for precisely when something is already broken,
# so no step here may depend on another having worked. Every removal is
# independent and best-effort: a missing task, a missing launcher or a
# half-deleted venv is a state to report, not a reason to abort. The exit
# code is 1 only if something that exists could not be removed.
[CmdletBinding()]
param(
    [switch]$KeepModel,
    [switch]$KeepState,
    [switch]$DryRun,
    [switch]$Yes,
    [string]$InstallHome
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:Results = @()

function Write-Status {
    param([string]$Message)
    Write-Host "uninstall.ps1: $Message"
}

function Write-Report {
    param(
        [string]$Label,
        [string]$Value
    )
    Write-Host ("uninstall.ps1:   {0,-15} {1}" -f $Label, $Value)
}

function Add-Result {
    param(
        [string]$Item,
        [ValidateSet("removed", "absent", "kept", "failed")]
        [string]$Status,
        [string]$Detail = ""
    )
    $script:Results += [pscustomobject]@{
        Item = $Item
        Status = $Status
        Detail = $Detail
    }
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

function Get-TreeSize {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return [long]0 }
    try {
        $measured = Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum
        if ($null -eq $measured -or $null -eq $measured.Sum) { return [long]0 }
        return [long]$measured.Sum
    }
    catch {
        return [long]0
    }
}

function Read-JsonFile {
    # Returns $null rather than throwing: a corrupt state file must not stop
    # an uninstall -- it is one more thing that is about to be deleted.
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try {
        $text = Get-Content -LiteralPath $Path -Raw
        if ([string]::IsNullOrWhiteSpace($text)) { return $null }
        return $text | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Get-JsonValue {
    # StrictMode turns a missing property on a PSCustomObject into a
    # terminating error, and every state file here is one a crash may have
    # truncated. Ask before reading.
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    if ($Object.PSObject.Properties.Name -notcontains $Name) { return $null }
    return $Object.$Name
}

function Get-RecordedProcesses {
    <#
        The PIDs mnemo itself recorded, not processes discovered by scanning.
        Both files matter and they disagree by design: service.pid is what
        `service start` spawned, service.json is what the backend published
        about itself -- on Windows the venv's pythonw.exe is a redirector
        stub, so the spawned PID is not the PID doing the work.
    #>
    param([string]$EngineHome)

    $found = @()
    foreach ($name in @("service.pid", "service.json")) {
        $data = Read-JsonFile (Join-Path $EngineHome "state\$name")
        $recorded = Get-JsonValue $data "pid"
        if ($null -eq $recorded) { continue }
        $candidate = 0
        try { $candidate = [int]$recorded } catch { continue }
        if ($candidate -le 0) { continue }
        if ($found -contains $candidate) { continue }
        if ($null -ne (Get-Process -Id $candidate -ErrorAction SilentlyContinue)) {
            $found += $candidate
        }
    }
    # `,` keeps the array an array: a bare `return @()` unrolls to $null on the
    # way out, and $null.Count is a terminating error under StrictMode.
    return ,$found
}

function Get-RegisteredBanks {
    param([string]$EngineHome)

    $data = Read-JsonFile (Join-Path $EngineHome "state\banks.json")
    if ($null -eq $data) { return ,@() }
    $entries = Get-JsonValue $data "banks"
    if ($null -eq $entries) { $entries = $data }
    $banks = @()
    foreach ($entry in @($entries)) {
        $name = Get-JsonValue $entry "name"
        $root = Get-JsonValue $entry "root"
        if ($null -eq $name -and $null -eq $root) { continue }
        $banks += [pscustomobject]@{
            Name = if ($null -eq $name) { "(unnamed)" } else { [string]$name }
            Root = if ($null -eq $root) { "(no root)" } else { [string]$root }
        }
    }
    return ,$banks
}

function Test-AutostartTask {
    # 2>&1 rather than 2>$null: under $ErrorActionPreference='Stop', PS 5.1
    # turns a native command's redirected stderr into a terminating error.
    try {
        $null = & schtasks /Query /TN "mnemo service" 2>&1
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function Test-ProfileBlock {
    param([string]$ProfilePath)
    if (-not (Test-Path -LiteralPath $ProfilePath -PathType Leaf)) { return $false }
    try {
        $current = Get-Content -LiteralPath $ProfilePath -Raw
    }
    catch {
        return $false
    }
    if ($null -eq $current) { return $false }
    return $current.Contains("# >>> mnemo >>>")
}

function Stop-EngineProcesses {
    param([string]$EngineHome, [string]$Launcher)

    $running = Get-RecordedProcesses $EngineHome
    if ($running.Count -eq 0) {
        Add-Result "service" "absent" "not running"
        return
    }

    # The launcher first, always: `service stop` knows the fingerprint check,
    # refuses to kill a PID it does not own, and takes the embedding resident
    # down with it (which is what releases the model files). Killing recorded
    # PIDs is the fallback for an engine too broken to have a launcher.
    if (Test-Path -LiteralPath $Launcher -PathType Leaf) {
        try { & $Launcher service stop 2>&1 | Out-Null } catch { }
    }

    for ($i = 0; $i -lt 30; $i++) {
        if ((Get-RecordedProcesses $EngineHome).Count -eq 0) {
            Add-Result "service" "removed" "stopped"
            return
        }
        Start-Sleep -Milliseconds 100
    }

    foreach ($processId in (Get-RecordedProcesses $EngineHome)) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 300

    $left = Get-RecordedProcesses $EngineHome
    if ($left.Count -eq 0) {
        Add-Result "service" "removed" "force-stopped"
    }
    else {
        Add-Result "service" "failed" "still running (pid $($left -join ', ')) - stop it and re-run"
    }
}

function Remove-AutostartTask {
    param([string]$Launcher)

    if (-not (Test-AutostartTask)) {
        Add-Result "autostart task" "absent" ""
        return
    }

    if (Test-Path -LiteralPath $Launcher -PathType Leaf) {
        try { & $Launcher autostart disable 2>&1 | Out-Null } catch { }
    }
    if (-not (Test-AutostartTask)) {
        Add-Result "autostart task" "removed" "'mnemo service'"
        return
    }

    # No launcher, or it failed: schtasks is what autostart.py calls anyway.
    try { $null = & schtasks /Delete /TN "mnemo service" /F 2>&1 } catch { }
    if (Test-AutostartTask) {
        Add-Result "autostart task" "failed" "schtasks /Delete /TN 'mnemo service' /F did not remove it"
    }
    else {
        Add-Result "autostart task" "removed" "'mnemo service'"
    }
}

function Remove-ProfileBlock {
    param([string]$ProfilePath)

    if (-not (Test-ProfileBlock $ProfilePath)) {
        Add-Result "profile block" "absent" ""
        return
    }

    $begin = "# >>> mnemo >>>"
    $end = "# <<< mnemo <<<"
    try {
        $current = Get-Content -LiteralPath $ProfilePath -Raw
        $pattern = "(?s)\r?\n?" + [regex]::Escape($begin) + ".*?" + [regex]::Escape($end) + "\r?\n?"
        $updated = [regex]::Replace($current, $pattern, [Environment]::NewLine)
        # Only mnemo's fence goes; whatever else lives in the profile stays
        # exactly as it was, including the blank lines around it.
        $updated = $updated.TrimEnd() + [Environment]::NewLine
        if ([string]::IsNullOrWhiteSpace($updated)) {
            Remove-Item -LiteralPath $ProfilePath -Force
            Add-Result "profile block" "removed" "profile was ours alone, file deleted"
            return
        }
        Set-Content -LiteralPath $ProfilePath -Value $updated -Encoding UTF8
        Add-Result "profile block" "removed" $ProfilePath
    }
    catch {
        Add-Result "profile block" "failed" $_.Exception.Message
    }
}

function Remove-ApiTokenEnvironment {
    $existing = [Environment]::GetEnvironmentVariable("MNEMO_API_TOKEN", "User")
    if ([string]::IsNullOrWhiteSpace($existing)) {
        Add-Result "MNEMO_API_TOKEN" "absent" ""
        return
    }
    try {
        [Environment]::SetEnvironmentVariable("MNEMO_API_TOKEN", $null, "User")
        Add-Result "MNEMO_API_TOKEN" "removed" "user scope"
    }
    catch {
        Add-Result "MNEMO_API_TOKEN" "failed" $_.Exception.Message
    }
}

function Remove-Tree {
    <#
        Delete with a short retry. The venv and the model cache are exactly
        what a still-shutting-down process holds open, and a lock that clears
        in 200 ms should not read as a failed uninstall.
    #>
    param([string]$Path, [string]$Label)

    if (-not (Test-Path -LiteralPath $Path)) {
        Add-Result $Label "absent" ""
        return
    }

    $size = Get-TreeSize $Path
    for ($attempt = 0; $attempt -lt 4; $attempt++) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            Add-Result $Label "removed" (Format-Bytes $size)
            return
        }
        catch {
            $lastError = $_.Exception.Message
            Start-Sleep -Milliseconds 400
        }
    }
    if (Test-Path -LiteralPath $Path) {
        Add-Result $Label "failed" "$Path - $lastError"
    }
    else {
        Add-Result $Label "removed" (Format-Bytes $size)
    }
}

function Confirm-Removal {
    param([long]$TotalBytes)

    if ($Yes) { return $true }

    $interactive = [Environment]::UserInteractive
    try {
        if ([Console]::IsInputRedirected) { $interactive = $false }
    }
    catch {
        $interactive = $false
    }
    if (-not $interactive) {
        # A prompt nobody can see would hang forever or read one byte of the
        # caller's data as consent -- for a delete, neither is acceptable.
        Write-Status "non-interactive run and no -Yes: nothing was removed."
        Write-Status "re-run with -Yes to confirm, or -DryRun to see the list."
        return $false
    }

    Write-Status ("about to remove {0}. This cannot be undone." -f (Format-Bytes $TotalBytes))
    $answer = Read-Host "uninstall.ps1: proceed? [y/N]"
    return ($answer.Trim().ToLowerInvariant() -in @("y", "yes"))
}

function Invoke-Uninstall {
    $usingDefaultHome = [string]::IsNullOrWhiteSpace($InstallHome)
    $powerShellHome = [System.IO.Path]::GetFullPath([string]$HOME)
    if ($usingDefaultHome) {
        $engineHome = Join-Path $powerShellHome ".mnemo"
    }
    else {
        $engineHome = [System.IO.Path]::GetFullPath($InstallHome)
    }
    $launcher = Join-Path $engineHome "bin\mnemo.exe"
    $profilePath = $PROFILE.CurrentUserAllHosts

    Write-Status "engine home: $engineHome"

    if (-not (Test-Path -LiteralPath $engineHome -PathType Container)) {
        Write-Status "no engine installed there."
        # The machine-level registrations can outlive the directory (a manual
        # `rm -rf` leaves the task and the variable behind), so keep going and
        # report on them rather than declaring victory.
    }

    # ---- survey: measure before touching anything ----------------------
    $stateDir = Join-Path $engineHome "state"
    $modelDir = Join-Path $engineHome "model-cache"
    # Versioned layout (self-update): src/ and .venv live under
    # versions/<tag>/, not directly under $engineHome any more. `versions\`
    # covers every retained tag's size in one measurement; `current` is
    # only ever a junction/alias (its own tree size is ~0, the bytes are
    # counted once under versions\).
    $versionsDir = Join-Path $engineHome "versions"
    $currentLink = Join-Path $engineHome "current"

    $totalSize = Get-TreeSize $engineHome
    $stateSize = Get-TreeSize $stateDir
    $modelSize = Get-TreeSize $modelDir
    $versionsSize = Get-TreeSize $versionsDir
    $banks = Get-RegisteredBanks $engineHome
    $running = Get-RecordedProcesses $engineHome
    $hasTask = Test-AutostartTask
    $hasProfile = Test-ProfileBlock $profilePath
    $hasToken = -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("MNEMO_API_TOKEN", "User"))
    $indexCount = 0
    if (Test-Path -LiteralPath $stateDir -PathType Container) {
        $indexCount = @(Get-ChildItem -LiteralPath $stateDir -Filter "*.db" -File -ErrorAction SilentlyContinue).Count
    }

    $goingSize = $totalSize
    if ($KeepModel) { $goingSize -= $modelSize }
    if ($KeepState) { $goingSize -= $stateSize }

    Write-Status "this would remove --"
    Write-Report "engine code" $(if (Test-Path -LiteralPath (Join-Path $currentLink "src\cli.py") -PathType Leaf) { "src\, launchers, requirements (versions\, current)" } else { "MISSING" })
    Write-Report "versions (code + venv)" $(if ($versionsSize -gt 0) { Format-Bytes $versionsSize } else { "absent" })
    if ($KeepModel) {
        Write-Report "model cache" ("KEPT (" + (Format-Bytes $modelSize) + ")")
    }
    else {
        Write-Report "model cache" $(if ($modelSize -gt 0) { (Format-Bytes $modelSize) + " - re-download on next install" } else { "absent" })
    }
    if ($KeepState) {
        Write-Report "state" ("KEPT (" + (Format-Bytes $stateSize) + ", $indexCount index files)")
    }
    else {
        Write-Report "state" $(if ($stateSize -gt 0) { (Format-Bytes $stateSize) + ", $indexCount index files, registry, journal, tokens" } else { "absent" })
    }
    Write-Report "service" $(if ($running.Count -gt 0) { "running (pid $($running -join ', ')) - will be stopped" } else { "not running" })
    # The logon task, the profile block and the variable are machine-level and
    # belong to the real engine. Listing them under "this would remove" for an
    # isolated copy would be a promise the removal step correctly refuses to
    # keep, so they are not listed there at all.
    if ($usingDefaultHome) {
        Write-Report "autostart" $(if ($hasTask) { "logon task 'mnemo service'" } else { "not registered" })
        Write-Report "profile block" $(if ($hasProfile) { $profilePath } else { "not present" })
        Write-Report "MNEMO_API_TOKEN" $(if ($hasToken) { "user-scope variable" } else { "not set" })
    }
    Write-Report "total" (Format-Bytes $goingSize)

    if (-not $usingDefaultHome) {
        Write-Status "isolated home: the profile, the variable and the logon task belong to the real engine and are left alone"
    }

    if ($banks.Count -gt 0 -and -not $KeepState) {
        Write-Status "$($banks.Count) registered bank(s) will be forgotten. Their .md are NOT touched:"
        foreach ($bank in $banks) {
            Write-Report $bank.Name $bank.Root
        }
        Write-Status "after reinstalling, re-run 'mnemo init' in each project: the tokens in their .mcp.json refer to a registry that will no longer exist."
    }

    if ($DryRun) {
        Write-Status "dry run: nothing was removed."
        return 0
    }

    if (-not (Confirm-Removal $goingSize)) {
        Write-Status "cancelled; nothing was removed."
        return 0
    }

    # ---- removal: independent steps, none aborts the rest ---------------
    Stop-EngineProcesses $engineHome $launcher

    if ($usingDefaultHome) {
        Remove-AutostartTask $launcher
        Remove-ProfileBlock $profilePath
        Remove-ApiTokenEnvironment
    }

    if ($KeepModel -or $KeepState) {
        # Selective: remove every child of the engine home except the ones
        # explicitly kept. Children rather than a whitelist, so leftovers of
        # older layouts (build\, *.egg-info, crash logs) go too.
        $kept = @()
        if ($KeepModel) { $kept += "model-cache" }
        if ($KeepState) { $kept += "state" }
        if (Test-Path -LiteralPath $engineHome -PathType Container) {
            foreach ($child in Get-ChildItem -LiteralPath $engineHome -Force) {
                if ($kept -contains $child.Name) {
                    Add-Result $child.Name "kept" (Format-Bytes (Get-TreeSize $child.FullName))
                    continue
                }
                Remove-Tree $child.FullName $child.Name
            }
        }
        else {
            Add-Result "engine home" "absent" ""
        }
    }
    else {
        Remove-Tree $engineHome "engine home"
    }

    # ---- report ---------------------------------------------------------
    Write-Status "result --"
    foreach ($result in $script:Results) {
        $line = $result.Status
        if (-not [string]::IsNullOrWhiteSpace($result.Detail)) {
            $line = "$line - $($result.Detail)"
        }
        Write-Report $result.Item $line
    }

    $failed = @($script:Results | Where-Object { $_.Status -eq "failed" })

    # ---- what deliberately survives -------------------------------------
    Write-Status "left alone on purpose --"
    $homeVariable = [Environment]::GetEnvironmentVariable("HOME", "User")
    if (-not [string]::IsNullOrWhiteSpace($homeVariable)) {
        Write-Report "HOME" "$homeVariable (install.ps1 only sets it when absent and keeps no record; git, ssh and others may rely on it now)"
    }
    Write-Report "your projects" ".md, .mcp.json, .claude\rules\mnemo-memory.md - the markdown is the source of truth"
    Write-Report "this repository" "the source; delete the checkout by hand if you want it gone"

    if ($failed.Count -gt 0) {
        Write-Status "done, with $($failed.Count) item(s) NOT removed (listed above)."
        return 1
    }
    Write-Status "done. mnemo is gone from this machine."
    return 0
}

# Dot-sourcing (`. .\uninstall.ps1`) loads the functions without uninstalling
# - that is how the tests exercise the profile and survey logic in isolation.
if ($MyInvocation.InvocationName -eq ".") {
    return
}

try {
    $result = Invoke-Uninstall
    # $PSCommandPath is empty when this script's TEXT was run via `iex`/
    # `& { ... }` rather than as a real file -- confirmed live, not a
    # guess: `exit`, even with a successful $result of 0, terminates the
    # CURRENT PowerShell process when there's no real file behind the
    # execution, which for the one-liner IS the user's whole open
    # terminal (that is exactly what "iex \"& { \$(irm ...) } -Yes\""
    # does). A real file invocation (a clone's `.\uninstall.ps1`, or our
    # own test suite's `-File`) still exits normally so a real process
    # exit code is reported.
    if ($PSCommandPath) {
        exit $result
    }
}
catch {
    [Console]::Error.WriteLine("uninstall.ps1: ERROR: $($_.Exception.Message)")
    if ($PSCommandPath) {
        exit 1
    }
}
