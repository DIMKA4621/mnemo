# mnemo engine installer for native Windows (PowerShell 5.1+).
# Installs or refreshes the user-scope engine without downloading the model.
[CmdletBinding()]
param(
    [switch]$Check,
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
        & $VenvPython -c "import fastembed, sqlite_vec, semantic_text_splitter, mcp" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $deps = "present"
        }
    }
    Write-Report "python deps" $deps

    $modelCached = $false
    if ($deps -eq "present") {
        $modelProbe = "import os,sys; home=sys.argv[1]; os.environ['MNEMO_HOME']=home; sys.path.insert(0,home); from src.embedder import is_model_cached; raise SystemExit(0 if is_model_cached() else 1)"
        & $VenvPython -c $modelProbe $EngineHome 2>$null
        $modelCached = $LASTEXITCODE -eq 0
    }
    Write-Report "model cache" $(if ($modelCached) { "present (warmed)" } else { "empty / incomplete (run: mnemo warmup)" })
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

    $copyRequired = $true
    if (Test-Path -LiteralPath $Launcher -PathType Leaf) {
        $generatedHash = (Get-FileHash -LiteralPath $generated -Algorithm SHA256).Hash
        $launcherHash = (Get-FileHash -LiteralPath $Launcher -Algorithm SHA256).Hash
        $copyRequired = $generatedHash -ne $launcherHash
    }
    if ($copyRequired) {
        try {
            Copy-Item -LiteralPath $generated -Destination $Launcher -Force
        }
        catch {
            throw "Cannot refresh $Launcher. Close Claude Code sessions using mnemo and retry. $($_.Exception.Message)"
        }
    }
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
                    throw "HOME resolves to '$resolved', but PowerShell ~ resolves to '$powerShellHome'. mnemo requires both to match for portable MCP and hook wiring."
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

    Write-Status "done. The embedding model is NOT downloaded by install."
    Write-Status "warm it once with:  & '$launcher' warmup"
}

try {
    Invoke-Install
}
catch {
    [Console]::Error.WriteLine("install.ps1: ERROR: $($_.Exception.Message)")
    exit 1
}
