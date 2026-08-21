# Regenerate .mcp.json from .mcp.json.template + .mcp.env.
#
# mnemo:dynamic-setup/2
# Substitutions are DISCOVERED from the template, never listed here. Adding a
# server means editing the template and .mcp.env; this file never changes.
#
# The Windows half of mcp-setup.sh, and it must produce byte-identical output:
# same discovery, same lookup rules, same failure. Windows PowerShell 5.1.
Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$template  = Join-Path $scriptDir ".mcp.json.template"
$envFile   = Join-Path $scriptDir ".mcp.env"
$output    = Join-Path $scriptDir ".mcp.json"

if (-not (Test-Path -LiteralPath $template -PathType Leaf)) {
    [Console]::Error.WriteLine("mcp-setup: missing $template")
    exit 1
}
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    [Console]::Error.WriteLine("mcp-setup: missing .mcp.env -- run: Copy-Item .mcp.env.example .mcp.env")
    exit 1
}

# Read KEY=VALUE. The file is READ, never executed: it holds credentials.
# First definition wins, matching how the shell half reads the file top-down.
$values = @{}
foreach ($line in [IO.File]::ReadAllLines($envFile)) {
    $trimmed = $line.Trim()
    if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
    $split = $line.IndexOf("=")
    if ($split -lt 1) { continue }
    $key = $line.Substring(0, $split).Trim()
    $value = $line.Substring($split + 1)
    # Trim, then unquote. Spaces are tolerated around the `=` on the key side,
    # so tolerating them on the value side is the only consistent reading --
    # and `PORT = 4646` otherwise yields a port with spaces in it. A value that
    # genuinely wants padding says so by quoting.
    $value = $value.Trim()
    # One layer of surrounding quotes, if the value carries them.
    if ($value.Length -ge 2) {
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
    }
    if (-not $values.ContainsKey($key)) { $values[$key] = $value }
}

$content = [IO.File]::ReadAllText($template)

# Every placeholder the template actually contains, deduplicated and sorted -
# the same set the shell half derives, so a missing-variable message lists
# them in the same order.
$names = [regex]::Matches($content, '\{\{([A-Za-z0-9_]+)\}\}') |
    ForEach-Object { $_.Groups[1].Value } |
    Sort-Object -Unique

$missing = @()
foreach ($name in $names) {
    if ($values.ContainsKey($name)) {
        # `.Replace` and not `-replace`: the latter reads the pattern as a
        # regex and `$` in a token would be taken as a capture reference.
        $content = $content.Replace("{{$name}}", $values[$name])
    }
    else {
        $missing += $name
    }
}

if ($missing.Count -gt 0) {
    [Console]::Error.WriteLine("mcp-setup: no value in .mcp.env for: " + ($missing -join " "))
    [Console]::Error.WriteLine("mcp-setup: .mcp.json NOT written")
    exit 1
}

# LF and a single trailing newline, so both halves write the same bytes.
$content = $content.Replace("`r`n", "`n").TrimEnd("`n") + "`n"
[IO.File]::WriteAllText($output, $content, (New-Object Text.UTF8Encoding $false))
Write-Host "mcp-setup: wrote .mcp.json"
