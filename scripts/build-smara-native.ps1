[CmdletBinding()]
param(
    [string]$Toolchain = 'stable-x86_64-pc-windows-msvc',
    [ValidateSet('dev', 'release')][string]$Profile = 'release',
    [ValidateRange(1, 64)][int]$Jobs = 2,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$smaraRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$sourceRoot = Join-Path $smaraRoot 'vendor\codex\codex-rs'
$manifest = Get-Content -LiteralPath (Join-Path $smaraRoot 'native\UPSTREAM.json') -Raw | ConvertFrom-Json
if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'core\src\lib.rs'))) {
    throw 'The full imported Codex runtime source is missing.'
}
Write-Host "Building Smara from copied Codex source revision $($manifest.commit)"
Push-Location $sourceRoot
try {
    if ($CheckOnly) {
        & cargo "+$Toolchain" check --locked --jobs $Jobs -p codex-cli --bin smara-native
    } else {
        # Large native crates otherwise compete for Windows commit memory and
        # metadata mappings. Bound concurrency without changing release code.
        & cargo "+$Toolchain" build --locked --jobs $Jobs --profile $Profile -p codex-cli --bin smara-native -p codex-windows-sandbox --bin codex-windows-sandbox-setup --bin codex-command-runner
    }
    if ($LASTEXITCODE -ne 0) { throw "Native source build failed ($LASTEXITCODE)." }
    if (-not $CheckOnly) {
        $destination = Join-Path $smaraRoot 'native\dist'
        $profileDirectory = if ($Profile -eq 'dev') { 'debug' } else { 'release' }
        New-Item -ItemType Directory -Force -Path $destination | Out-Null
        foreach ($binary in @('smara-native.exe', 'codex-windows-sandbox-setup.exe', 'codex-command-runner.exe')) {
            $built = Join-Path $sourceRoot "target\$profileDirectory\$binary"
            if (-not (Test-Path -LiteralPath $built -PathType Leaf)) { throw "Required native binary missing: $binary" }
            Copy-Item -LiteralPath $built -Destination (Join-Path $destination $binary) -Force
        }
        Copy-Item -LiteralPath (Join-Path $smaraRoot 'vendor\codex\LICENSE') -Destination $destination -Force
        Copy-Item -LiteralPath (Join-Path $smaraRoot 'vendor\codex\NOTICE') -Destination $destination -Force
        Copy-Item -LiteralPath (Join-Path $smaraRoot 'native\UPSTREAM.json') -Destination $destination -Force
    }
} finally { Pop-Location }
