[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Assert-NativeSuccess([string]$step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$step failed with exit code $LASTEXITCODE"
    }
}

$appRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\apps\desktop')).Path
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$version = (Get-Content -LiteralPath (Join-Path $repoRoot 'release\VERSION') -Raw).Trim()
# A Desktop build may never silently ship the old engine as its native core.
& (Join-Path $PSScriptRoot 'build-smara-native.ps1')
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Smara virtualenv Python was not found at $python"
}

Push-Location $repoRoot
try {
    # Freeze directly from this checkout. Installing a wheel into the shared
    # developer virtualenv here shadows ``src\smara`` with a copied package,
    # so later CLI commands and tests can silently execute stale code. The
    # explicit source path keeps the bundle and checkout on one revision
    # without mutating the developer environment.
    $executorDist = Join-Path $repoRoot "build\desktop-executor-$version"
    # Benchmark/data-science dependencies are development-only. PyInstaller
    # otherwise discovers them through optional imports and adds roughly an
    # entire analytics environment to the desktop executor.
    $pyInstallerExcludes = @(
        'pandas'
        'pyarrow'
        'datasets'
        'pytest'
        'tkinter'
        '_tkinter'
    )
    $pyInstallerArgs = @(
        '-m', 'PyInstaller'
        '--noconfirm'
        '--clean'
        '--onefile'
        '--paths', 'src\smara'
        '--hidden-import', 'agent_tools'
        '--hidden-import', 'pypdf'
        '--hidden-import', 'docx'
        '--hidden-import', 'openpyxl'
        '--hidden-import', 'pptx'
        '--hidden-import', 'smara.research_watch'
        '--hidden-import', 'smara.app_adapter'
        '--hidden-import', 'smara.research_chat'
        '--hidden-import', 'smara.native_runtime'
        '--hidden-import', 'smara.native_provider'
        '--hidden-import', 'smara.native_tools'
    )
    foreach ($module in $pyInstallerExcludes) {
        $pyInstallerArgs += @('--exclude-module', $module)
    }
    $pyInstallerArgs += @(
        '--name', 'smara-desktop'
        '--distpath', $executorDist
        '--workpath', 'build\pyinstaller'
        'src\smara\desktop_executor.py'
    )
    & $python @pyInstallerArgs
    Assert-NativeSuccess 'PyInstaller'
    $resourceDir = Join-Path $appRoot 'src-tauri\resources'
    New-Item -ItemType Directory -Force -Path $resourceDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $executorDist 'smara-desktop.exe') -Destination (Join-Path $resourceDir 'smara-desktop.exe') -Force
    $nativeResourceDir = Join-Path $resourceDir 'native'
    New-Item -ItemType Directory -Force -Path $nativeResourceDir | Out-Null
    foreach ($nativeResource in @('smara-native.exe', 'codex-windows-sandbox-setup.exe', 'codex-command-runner.exe', 'LICENSE', 'NOTICE', 'UPSTREAM.json')) {
        Copy-Item -LiteralPath (Join-Path $repoRoot "native\dist\$nativeResource") -Destination (Join-Path $nativeResourceDir $nativeResource) -Force
    }
    Copy-Item -LiteralPath (Join-Path $repoRoot 'release\evidence\LIVE_WEB_ACCEPTANCE_V5.json') -Destination (Join-Path $resourceDir 'LIVE_WEB_ACCEPTANCE_V5.json') -Force
    Get-ChildItem -Path (Join-Path $repoRoot 'release\evidence\LIVE_WEB_ACCEPTANCE_V5*.json') -File | ForEach-Object {
        $smaraReportMetadata = Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json
        if ($smaraReportMetadata.selected_task_count -eq 24 -or $smaraReportMetadata.summary.expected -eq 24) {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $resourceDir $_.Name) -Force
        }
    }
} finally {
    Pop-Location
}

Push-Location $appRoot
try {
    npm ci
    Assert-NativeSuccess 'npm ci'
    npm run build
    Assert-NativeSuccess 'frontend build'
    Push-Location src-tauri
    try { cargo check; Assert-NativeSuccess 'cargo check' } finally { Pop-Location }
    # NSIS is the supported beta distribution path on Windows. WiX/MSI is
    # optional and can fail on machines without a working light.exe install.
    npm exec tauri build -- --bundles nsis
    Assert-NativeSuccess 'Tauri package build'
} finally {
    Pop-Location
}

Write-Host "Smara Desktop installers are under apps\desktop\src-tauri\target\release\bundle."
