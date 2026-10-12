[CmdletBinding()]
param([switch]$Apply, [string]$KeepPrevious = '0.1.14')
$ErrorActionPreference = 'Stop'
$smaraCleanupRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$smaraCleanupCurrent = (Get-Content -LiteralPath (Join-Path $smaraCleanupRoot 'release\VERSION') -Raw).Trim()
$smaraCleanupBuild = (Resolve-Path -LiteralPath (Join-Path $smaraCleanupRoot 'build')).Path
$smaraCleanupCli = Join-Path $env:LOCALAPPDATA 'Programs\Smara CLI'
$smaraCleanupTargets = @()
foreach ($smaraCleanupItem in (Get-ChildItem -LiteralPath $smaraCleanupBuild -Directory)) {
    if ($smaraCleanupItem.Name -match '^desktop-executor-(\d+\.\d+\.\d+)(-slim)?$' -and
        $Matches[1] -notin @($smaraCleanupCurrent, $KeepPrevious)) {
        $smaraCleanupTargets += $smaraCleanupItem.FullName
    }
}
if (Test-Path -LiteralPath $smaraCleanupCli) {
    $smaraCleanupCli = (Resolve-Path -LiteralPath $smaraCleanupCli).Path
    foreach ($smaraCleanupItem in (Get-ChildItem -LiteralPath $smaraCleanupCli -Directory)) {
        if ($smaraCleanupItem.Name -match '^\d+\.\d+\.\d+$' -and $smaraCleanupItem.Name -notin @($smaraCleanupCurrent, $KeepPrevious)) {
            $smaraCleanupTargets += $smaraCleanupItem.FullName
        }
    }
}
$smaraCleanupPlan = @()
foreach ($smaraCleanupTarget in $smaraCleanupTargets) {
    $smaraCleanupResolved = (Resolve-Path -LiteralPath $smaraCleanupTarget).Path
    if (-not ($smaraCleanupResolved.StartsWith($smaraCleanupBuild + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $smaraCleanupResolved.StartsWith($smaraCleanupCli + '\', [StringComparison]::OrdinalIgnoreCase))) { throw 'Cleanup target escaped its named root' }
    $smaraCleanupTree = @((Get-Item -LiteralPath $smaraCleanupResolved)) + @(Get-ChildItem -LiteralPath $smaraCleanupResolved -Recurse -Force)
    if ($smaraCleanupTree | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) { throw "Reparse point in cleanup target: $smaraCleanupResolved" }
    if ($smaraCleanupTree | Where-Object { $_.Name -in @('.git', '.smara', 'credentials.json', 'desktop.json', 'native-goals.sqlite') }) { throw "User/project state in cleanup target: $smaraCleanupResolved" }
    if (Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($smaraCleanupResolved + '\', [StringComparison]::OrdinalIgnoreCase) }) { throw "Running process uses cleanup target: $smaraCleanupResolved" }
    if ($smaraCleanupResolved.StartsWith($smaraCleanupRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        $smaraCleanupRelative = [IO.Path]::GetRelativePath($smaraCleanupRoot, $smaraCleanupResolved)
        $smaraCleanupTracked = @(& git -C $smaraCleanupRoot ls-files -- $smaraCleanupRelative)
        if ($LASTEXITCODE -ne 0 -or $smaraCleanupTracked.Count) { throw "Tracked files in cleanup target: $smaraCleanupResolved" }
    }
    $smaraCleanupBytes = ($smaraCleanupTree | Where-Object { -not $_.PSIsContainer } | Measure-Object -Property Length -Sum).Sum
    $smaraCleanupPlan += [pscustomobject]@{path=$smaraCleanupResolved; bytes=[long]$smaraCleanupBytes}
}
$smaraCleanupPlan | ConvertTo-Json -Depth 3
if ($Apply) {
    foreach ($smaraCleanupEntry in $smaraCleanupPlan) {
        # The resolved, link-free targets above are explicit version directories.
        Remove-Item -LiteralPath $smaraCleanupEntry.path -Recurse -Force
    }
    $smaraCleanupUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $smaraCleanupNewPath = (($smaraCleanupUserPath -split ';') | Where-Object {
        $smaraCleanupPathEntry = $_.Trim().TrimEnd('\')
        -not ($smaraCleanupPlan | Where-Object { $_.path.Equals($smaraCleanupPathEntry, [StringComparison]::OrdinalIgnoreCase) })
    }) -join ';'
    if ($smaraCleanupNewPath -ne $smaraCleanupUserPath) { [Environment]::SetEnvironmentVariable('Path', $smaraCleanupNewPath, 'User') }
    Write-Host ('Removed obsolete generated packages: {0:N2} GiB. Current candidate, previous version, source, histories and keys preserved.' -f (($smaraCleanupPlan | Measure-Object -Property bytes -Sum).Sum / 1GB))
} else { Write-Host 'Preview only; pass -Apply to remove exactly these validated obsolete version directories.' }
