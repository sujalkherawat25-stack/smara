[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$appRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\apps\desktop')).Path
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Smara virtualenv Python was not found at $python"
}

Push-Location $repoRoot
try {
    & $python -m PyInstaller --noconfirm --clean --onefile --name smara-desktop --distpath build\desktop-executor --workpath build\pyinstaller src\smara\desktop_executor.py
    $resourceDir = Join-Path $appRoot 'src-tauri\resources'
    New-Item -ItemType Directory -Force -Path $resourceDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $repoRoot 'build\desktop-executor\smara-desktop.exe') -Destination (Join-Path $resourceDir 'smara-desktop.exe') -Force
} finally {
    Pop-Location
}

Push-Location $appRoot
try {
    npm install
    npm run build
    Push-Location src-tauri
    try { cargo check } finally { Pop-Location }
    npm exec tauri build
} finally {
    Pop-Location
}

Write-Host "Smara Desktop installers are under apps\desktop\src-tauri\target\release\bundle."
