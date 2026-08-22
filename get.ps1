# mnemo bootstrap installer: no local clone required.
#
#   irm https://raw.githubusercontent.com/DIMKA4621/mnemo/master/get.ps1 | iex
#
# Fetches a snapshot of the repo from GitHub, extracts it to a temp
# directory, and runs the real install.ps1 from inside it -- unmodified,
# exactly as a manual `git clone` + `.\install.ps1` would. The temp copy is
# always removed afterward, success or failure.
#
# `irm URL | iex` alone never passes arguments through -- PowerShell needs
# the `& { $(irm URL) } -Check -InstallHome D:\x` wrapper idiom to bind
# args to a piped script block. Whatever lands in that position is
# forwarded as-is to install.ps1; get.ps1 takes no flags of its own on
# purpose, so it can never collide with install.ps1's flag names or need
# updating when those change. Its two internal knobs -- which branch to
# fetch, and a test-only archive URL override -- are environment variables
# instead: $env:MNEMO_GET_REF (default "master"), $env:MNEMO_GET_ARCHIVE_URL.
#
# One default differs from install.ps1's own: unless -Model or -NoModel is
# already among the forwarded args, get.ps1 adds -Model itself. install.ps1
# left un-piped still asks (or silently skips when it can't ask); a one-
# liner's whole point is a single command that finishes the job, so this
# path assumes yes instead of leaving a ~2 GB download for the user to
# trigger by hand afterward. Pass -NoModel to opt out.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repo = "DIMKA4621/mnemo"
$ref = if ($env:MNEMO_GET_REF) { $env:MNEMO_GET_REF } else { "master" }
$url = if ($env:MNEMO_GET_ARCHIVE_URL) { $env:MNEMO_GET_ARCHIVE_URL } else { "https://codeload.github.com/$repo/zip/refs/heads/$ref" }

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("mnemo-src-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
$zipPath = Join-Path $tmp "src.zip"
$code = 0
# Read under Set-StrictMode before any native command has ever run in this
# process, $LASTEXITCODE throws "cannot be retrieved because it has not
# been set" instead of just being $null -- initialize it so the read below
# is always safe, whether or not install.ps1 itself calls exit.
$global:LASTEXITCODE = 0

try {
    Write-Host "get.ps1: downloading mnemo ($ref)..."
    Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing

    Write-Host "get.ps1: extracting..."
    Expand-Archive -LiteralPath $zipPath -DestinationPath $tmp -Force

    # GitHub's archive always has exactly one top-level "<repo>-<ref>" dir --
    # never hardcode the name, slashes in $ref get dashed by GitHub itself.
    $extracted = Get-ChildItem -LiteralPath $tmp -Directory | Select-Object -First 1
    if (-not $extracted) {
        throw "downloaded archive did not contain a source directory"
    }
    $installScript = Join-Path $extracted.FullName "install.ps1"
    if (-not (Test-Path -LiteralPath $installScript)) {
        throw "extracted source is missing install.ps1"
    }

    # $args must be splatted UNCHANGED, never copied into a new array or
    # List<string> first -- confirmed live, not a guess. PowerShell's
    # automatic $args (populated because this script has no param() block)
    # carries engine-level metadata marking which elements were originally
    # "-Name" tokens, and only that lets `@args` bind them as named
    # parameters on install.ps1 downstream. A freshly built array of the
    # very same strings loses that metadata, so `@args` splats every
    # element positionally instead -- "-InstallHome" itself ends up bound
    # as $InstallHome's VALUE, and a later "-Python" then has no positional
    # slot left to land in and the whole call fails. So -Model can only be
    # ADDED as a separate literal token in the call itself, never merged
    # into a rebuilt copy of $args.
    Write-Host "get.ps1: installing..."
    if ($args -match '^-(No)?Model$') {
        & $installScript @args
    }
    else {
        & $installScript @args -Model
    }
    $code = $LASTEXITCODE
}
catch {
    Write-Host "get.ps1: $($_.Exception.Message)" -ForegroundColor Red
    $code = 1
}
finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

if ($code) {
    # $PSCommandPath is empty when this script's TEXT was run via `iex`/
    # `& { ... }` rather than as a real file (-File, or `.\get.ps1` typed at
    # a prompt) -- confirmed live: `exit` from code with no real file behind
    # it terminates the CURRENT PowerShell process, i.e. the user's whole
    # open terminal, not just "this script." A one-liner install failing
    # should never take the user's shell down with it. When $PSCommandPath
    # IS set (our own test suite invokes get.ps1 via -File), exit normally
    # so a real process exit code is still reported.
    if ($PSCommandPath) {
        exit $code
    }
    Write-Host "get.ps1: failed (exit code $code)" -ForegroundColor Red
}
