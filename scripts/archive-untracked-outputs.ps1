[CmdletBinding()]
param([switch]$Apply)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$archive = Join-Path $repo ('artifacts\history\workspace-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
Push-Location $repo
try {
    $raw = (& git -c core.quotepath=false ls-files --others --exclude-standard -z) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw 'Could not inventory untracked files.' }
    $files = @($raw.Split([char]0) | Where-Object {
        $_ -match '^(release/.*\.(md|json|xml|png|jpg|jpeg|html|css)|reports/.*\.(md|json|csv)|live_crypto_prices\.csv|scripts/run_installed_database_research\.py)$'
    })
    $records = @()
    foreach ($relative in $files) {
        $source = (Resolve-Path -LiteralPath (Join-Path $repo $relative)).Path
        $destination = [IO.Path]::GetFullPath((Join-Path $archive $relative))
        if (-not $source.StartsWith($repo + '\', [StringComparison]::OrdinalIgnoreCase) -or
            -not $destination.StartsWith($archive + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Archive target is outside the expected repository directories.'
        }
        $item = Get-Item -LiteralPath $source -Force
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Review linked or non-file archive input: $relative"
        }
        $parent = $item.Directory
        while ($parent.FullName -ne $repo) {
            if ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Review linked parent: $relative" }
            $parent = $parent.Parent
        }
        $records += [PSCustomObject]@{ path = $relative; bytes = $item.Length; sha256 = (Get-FileHash -LiteralPath $source).Hash }
    }
    if ($Apply -and $records.Count) {
        New-Item -ItemType Directory -Path $archive | Out-Null
        # Record the original paths/hashes before moving any file. A partial
        # archive can be inspected and recovered without deleting user data.
        [IO.File]::WriteAllText((Join-Path $archive 'manifest.json'), ($records | ConvertTo-Json -Depth 4))
        foreach ($record in $records) {
            $destination = Join-Path $archive $record.path
            New-Item -ItemType Directory -Path (Split-Path $destination) -Force | Out-Null
            Move-Item -LiteralPath (Join-Path $repo $record.path) -Destination $destination
            if ((Get-FileHash -LiteralPath $destination).Hash -ne $record.sha256) { throw 'Archive verification failed.' }
        }
    }
    [PSCustomObject]@{ applied = [bool]$Apply; count = $records.Count; bytes = ($records | Measure-Object bytes -Sum).Sum; archive = $archive }
} finally { Pop-Location }
