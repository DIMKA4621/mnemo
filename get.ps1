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
# updating when those change. Its internal knobs -- which ref to fetch, and
# a test-only archive/API URL override -- are environment variables
# instead: $env:MNEMO_GET_REF, $env:MNEMO_GET_ARCHIVE_URL,
# $env:MNEMO_GET_RELEASE_API_URL.
#
# Default source: the latest GitHub release, not the moving `master`
# branch -- a one-liner should hand people the last thing that was
# actually tagged and shipped, same source engine_update.py's own
# self-update pulls from, not whatever happens to be on master mid-work.
# $env:MNEMO_GET_REF overrides this and is taken as a BRANCH name (heads/),
# for pointing the bootstrapper at an unreleased branch by hand -- an
# explicit, deliberate override, never an error path.
#
# If the releases/latest lookup itself fails -- no releases yet, GitHub
# unreachable, rate-limited -- this is a hard installation error, NOT a
# silent fallback to `master` (2026-08-22 decision, reversing the original
# soft-fallback behaviour): a one-liner that silently hands someone
# unreleased `master` when it meant to hand them the latest release would
# make the version they end up running depend on which GitHub API call
# happened to work that day. The error message names the likely causes and
# points at the manual-clone fallback.
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

function Get-FileWithSpinner {
    <#
        Downloads $Uri to $OutFile behind the same in-place spinner
        (backspace-redraw, | / - \) as install.ps1's own
        Invoke-CheckedWithHeartbeat -- but this script cannot dot-source
        that function yet: install.ps1 lives INSIDE the very archive being
        downloaded here, so nothing is reachable to reuse except the
        pattern itself (Register-ObjectEvent + a polling loop), applied to
        System.Net.WebClient's async download instead of a
        System.Diagnostics.Process. Deliberately NOT a Start-Job around a
        blocking Invoke-WebRequest: same console-popping risk documented
        on Invoke-CheckedWithHeartbeat applies to any PowerShell background
        job, and WebClient's own async methods need no job/process/thread
        of this script's own at all.
    #>
    param([string]$Uri, [string]$OutFile, [string]$Label)

    Write-Host -NoNewline "get.ps1: $Label "
    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    $client = New-Object System.Net.WebClient
    $client.Headers.Add("User-Agent", "mnemo-bootstrap")
    # A plain object (reference type) so the event handler's mutations are
    # visible on this side once the download finishes -- the handler runs
    # on its own thread and cannot write back through a local variable.
    $state = [pscustomobject]@{ Done = $false; Error = $null }
    $onDone = {
        $Event.MessageData.Done = $true
        if ($EventArgs.Error) { $Event.MessageData.Error = $EventArgs.Error }
    }
    $sub = Register-ObjectEvent -InputObject $client -EventName DownloadFileCompleted -Action $onDone -MessageData $state
    $spinnerPrinted = $false
    try {
        $client.DownloadFileAsync([uri]$Uri, $OutFile)
        $spinFrames = @('|', '/', '-', '\')
        $spinIndex = 0
        while (-not $state.Done) {
            Start-Sleep -Milliseconds 130
            if ($spinnerPrinted) { Write-Host -NoNewline "`b" }
            Write-Host -NoNewline $spinFrames[$spinIndex % $spinFrames.Length]
            $spinIndex++
            $spinnerPrinted = $true
        }
    }
    finally {
        Unregister-Event -SourceIdentifier $sub.Name -ErrorAction SilentlyContinue
        Remove-Job -Job $sub -Force -ErrorAction SilentlyContinue
        $client.Dispose()
    }

    # Same zero-tick guard as Invoke-CheckedWithHeartbeat: a download that
    # completes before the loop above ever sleeps once would otherwise eat
    # a character off $Label instead of a spinner frame it never drew.
    if ($spinnerPrinted) { Write-Host -NoNewline "`b" }
    if ($state.Error) {
        Write-Host ("failed ({0:N0}s)" -f $sw.Elapsed.TotalSeconds)
        throw $state.Error
    }
    Write-Host ("done ({0:N0}s)" -f $sw.Elapsed.TotalSeconds)
}

function Resolve-MnemoArchiveUrl {
    if ($env:MNEMO_GET_ARCHIVE_URL) {
        return @{ Url = $env:MNEMO_GET_ARCHIVE_URL; Label = "custom archive"; Tag = $null }
    }
    if ($env:MNEMO_GET_REF) {
        $ref = $env:MNEMO_GET_REF
        return @{ Url = "https://codeload.github.com/$repo/zip/refs/heads/$ref"; Label = $ref; Tag = $null }
    }

    $apiUrl = if ($env:MNEMO_GET_RELEASE_API_URL) { $env:MNEMO_GET_RELEASE_API_URL } else { "https://api.github.com/repos/$repo/releases/latest" }
    $tag = $null
    $lookupError = $null
    try {
        $release = Invoke-RestMethod -Uri $apiUrl -TimeoutSec 10 -Headers @{
            Accept       = "application/vnd.github+json"
            "User-Agent" = "mnemo-bootstrap"
        }
        if ($release.tag_name) { $tag = $release.tag_name }
    }
    catch {
        $lookupError = $_.Exception.Message
    }
    if ($tag) {
        return @{ Url = "https://codeload.github.com/$repo/zip/refs/tags/$tag"; Label = $tag; Tag = $tag }
    }

    $detail = if ($lookupError) { $lookupError } else { "GitHub reported no releases for $repo" }
    throw (
        "could not find a GitHub release to install ($detail). " +
        "Check your network connection, or install from a manual clone instead: " +
        "git clone https://github.com/$repo.git; cd mnemo; .\install.ps1"
    )
}

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
    # Resolved INSIDE this try, not before it -- Resolve-MnemoArchiveUrl can
    # now throw (no release found), and this is the one catch block whose
    # exit handling already knows how to fail without closing the caller's
    # shell when run via `irm | iex` (see the $PSCommandPath check below).
    $resolved = Resolve-MnemoArchiveUrl
    $url = $resolved.Url
    $refLabel = $resolved.Label

    Get-FileWithSpinner -Uri $url -OutFile $zipPath -Label "downloading mnemo ($refLabel)"

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
    # GitHub's archive carries no .git directory (confirmed: engine_update.py's
    # own stage_release() docstring notes the same fact), so install.ps1's
    # own git-based version detection can never see a tag here -- without
    # this, every get.ps1 install would report itself as "local" forever and
    # nag to "update" to the very release it just installed (the same class
    # of "current lies about what is actually running" bug this session's
    # other fixes closed elsewhere). get.ps1 already knows the exact tag it
    # resolved -- pass it through rather than making install.ps1 re-derive
    # it from nothing. Only set for a CONFIRMED release ($resolved.Tag);
    # left unset for a custom archive or an explicit $env:MNEMO_GET_REF
    # override -- neither names an installable version, so "local" remains
    # the correct, honest answer for them.
    if ($resolved.Tag) {
        $env:MNEMO_INSTALL_TAG = $resolved.Tag
    }

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
