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

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repo = "DIMKA4621/mnemo"
$ref = if ($env:MNEMO_GET_REF) { $env:MNEMO_GET_REF } else { "master" }
$url = if ($env:MNEMO_GET_ARCHIVE_URL) { $env:MNEMO_GET_ARCHIVE_URL } else { "https://codeload.github.com/$repo/zip/refs/heads/$ref" }

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("mnemo-src-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
$zipPath = Join-Path $tmp "src.zip"
$code = 0

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

    Write-Host "get.ps1: installing..."
    & $installScript @args
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
    exit $code
}
