[CmdletBinding()]
param([string]$SourceManifest)

$ErrorActionPreference = 'Stop'

function Assert-NativeSuccess([string]$step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$step failed with exit code $LASTEXITCODE"
    }
}

$appRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\apps\desktop')).Path
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$smaraCandidateExe = Join-Path $appRoot 'src-tauri\target\release\smara-desktop.exe'
if (Get-Process -Name 'smara-desktop' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $smaraCandidateExe }) {
    throw 'Close the locally built release candidate before rebuilding or packaging it.'
}
$version = (Get-Content -LiteralPath (Join-Path $repoRoot 'release\VERSION') -Raw).Trim()
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if ($SourceManifest) {
    $SourceManifest = (Resolve-Path -LiteralPath $SourceManifest).Path
    & $python (Join-Path $PSScriptRoot 'native_release_manifest.py') verify --manifest $SourceManifest
    Assert-NativeSuccess 'Pre-build source provenance'
} else { Write-Warning 'Unsealed candidate build: capture and pass -SourceManifest before publication.' }
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
        'numpy'
        'psycopg'
        'psycopg_pool'
        'smara.store'
        'smara.agent_tools'
        'smara.autonomous_agent'
        'smara.cli'
        'smara.desktop_executor'
        'smara.local_agent_runtime'
        'smara.self_healing'
        'smara.swarm'
        'smara.tool_synthesis'
    )
    $pyInstallerArgs = @(
        '-m', 'PyInstaller'
        '--noconfirm'
        '--clean'
        # A folder, not a single file: a single file unpacks ~70 MB into %TEMP% on EVERY start (about 2.5 s per call, and
        # it leaves a temp folder behind each time). A folder starts in about 0.5 s. The entry point keeps the same path.
        '--onedir'
        '--paths', 'src'
        '--add-data', ((Join-Path $repoRoot 'release\VERSION') + ';release')
        '--hidden-import', 'smara.native_management'
        '--hidden-import', 'smara.native_changes'
        '--hidden-import', 'smara.native_runtime'
        '--hidden-import', 'smara.native_provider'
        '--hidden-import', 'smara.native_memory'
        '--collect-all', 'syntarus'
        '--hidden-import', 'smara.native_tools'
        '--hidden-import', 'smara.native_sources'
        '--hidden-import', 'smara.native_limits'
        '--hidden-import', 'smara.native_documents'
        '--hidden-import', 'smara.native_pdf'
        '--collect-all', 'pypdfium2'
        '--collect-all', 'pypdfium2_raw'
        '--hidden-import', 'smara.native_goals'
        '--hidden-import', 'smara.native_goal_runtime'
        '--hidden-import', 'smara.native_telegram'
        '--hidden-import', 'smara.native_outcomes'
        '--hidden-import', 'smara.native_voice'
        '--hidden-import', 'smara.native_browser'
        '--hidden-import', 'smara.native_budget'
        '--hidden-import', 'smara.native_admission'
        '--hidden-import', 'smara.native_outreach'
        '--hidden-import', 'smara.native_mail_oauth'
        '--hidden-import', 'smara.native_background'
        '--hidden-import', 'smara.native_browser_control'
        '--hidden-import', 'smara.native_browser_transfer'
        '--hidden-import', 'smara.native_computer'
        '--hidden-import', 'smara.native_windows'
        '--hidden-import', 'smara.native_office'
        '--hidden-import', 'smara.native_tally'
        '--hidden-import', 'win32com.client'
        '--hidden-import', 'pythoncom'
        '--collect-all', 'pywinauto'
        '--collect-all', 'comtypes'
        '--hidden-import', 'psutil'
        '--collect-all', 'playwright'
    )
    foreach ($module in $pyInstallerExcludes) {
        $pyInstallerArgs += @('--exclude-module', $module)
    }
    $pyInstallerArgs += @(
        '--name', 'smara-desktop'
        '--distpath', $executorDist
        '--workpath', 'build\pyinstaller'
        'scripts\native_desktop_entry.py'
    )
    & $python @pyInstallerArgs
    Assert-NativeSuccess 'PyInstaller'
    $resourceDir = Join-Path $appRoot 'src-tauri\resources'
    New-Item -ItemType Directory -Force -Path $resourceDir | Out-Null
    # Same path as before (resources\smara-desktop.exe), with its support files in resources\_internal.
    $executorFolder = Join-Path $executorDist 'smara-desktop'
    Copy-Item -LiteralPath (Join-Path $executorFolder 'smara-desktop.exe') -Destination (Join-Path $resourceDir 'smara-desktop.exe') -Force
    $internalTarget = Join-Path $resourceDir '_internal'
    if (Test-Path -LiteralPath $internalTarget) { Remove-Item -LiteralPath $internalTarget -Recurse -Force }
    Copy-Item -LiteralPath (Join-Path $executorFolder '_internal') -Destination $internalTarget -Recurse -Force
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

# Signing is optional for local builds. Release builds set SMARA_SIGN_THUMBPRINT (docs/WINDOWS_CODE_SIGNING.md).
$signingThumbprint = ($env:SMARA_SIGN_THUMBPRINT -replace '\s', '')
$tauriExtra = @()
if ($signingThumbprint) {
    $bundledExecutables = @(
        (Join-Path $resourceDir 'smara-desktop.exe')
        (Join-Path $resourceDir 'native\smara-native.exe')
        (Join-Path $resourceDir 'native\codex-windows-sandbox-setup.exe')
        (Join-Path $resourceDir 'native\codex-command-runner.exe')
    )
    # Bundled executors are signed before packaging, so the installed copies are trusted too.
    & (Join-Path $PSScriptRoot 'sign-windows-files.ps1') -Files $bundledExecutables -Thumbprint $signingThumbprint
    # Tauri signs the app executable and the installer itself when it gets the thumbprint.
    $timestamp = if ($env:SMARA_SIGN_TIMESTAMP_URL) { $env:SMARA_SIGN_TIMESTAMP_URL } else { 'http://timestamp.digicert.com' }
    $tauriSigning = @{ bundle = @{ windows = @{ certificateThumbprint = $signingThumbprint; digestAlgorithm = 'sha256'; timestampUrl = $timestamp } } }
    $tauriSigningPath = Join-Path $env:TEMP 'smara-tauri-signing.json'
    $tauriSigning | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $tauriSigningPath -Encoding utf8
    $tauriExtra = @('--config', $tauriSigningPath)
} else {
    Write-Warning 'UNSIGNED build: SMARA_SIGN_THUMBPRINT is not set. Windows will show an unknown-publisher warning.'
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
    npm exec tauri build -- --bundles nsis $tauriExtra
    Assert-NativeSuccess 'Tauri package build'
    if ($signingThumbprint) {
        $installerBuilt = Get-ChildItem -Path (Join-Path $appRoot 'src-tauri\target\release\bundle\nsis') -Filter '*-setup.exe' |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        & (Join-Path $PSScriptRoot 'sign-windows-files.ps1') -Files @($installerBuilt.FullName) -Thumbprint $signingThumbprint -VerifyOnly
    }
} finally {
    Pop-Location
}

Write-Host "Smara Desktop installers are under apps\desktop\src-tauri\target\release\bundle."
if ($SourceManifest) {
    & $python (Join-Path $PSScriptRoot 'native_release_manifest.py') verify --manifest $SourceManifest
    Assert-NativeSuccess 'Post-build source provenance'
}
