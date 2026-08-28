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
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Smara virtualenv Python was not found at $python"
}

Push-Location $repoRoot
try {
    & $python -m PyInstaller --noconfirm --clean --onefile --name smara-desktop --distpath build\desktop-executor --workpath build\pyinstaller src\smara\desktop_executor.py
    Assert-NativeSuccess 'PyInstaller'
    $resourceDir = Join-Path $appRoot 'src-tauri\resources'
    New-Item -ItemType Directory -Force -Path $resourceDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $repoRoot 'build\desktop-executor\smara-desktop.exe') -Destination (Join-Path $resourceDir 'smara-desktop.exe') -Force
} finally {
    Pop-Location
}

Push-Location $appRoot
try {
    npm install
    Assert-NativeSuccess 'npm install'
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
