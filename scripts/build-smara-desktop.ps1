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
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Smara virtualenv Python was not found at $python"
}

Push-Location $repoRoot
try {
    # Keep the CLI and bundled Desktop on one source revision. A regular local
    # wheel install avoids Hatchling's optional ``editables`` dependency, while
    # still updating the CLI before freezing the executor below. ``--no-deps``
    # keeps provider/browser packages untouched and ``--no-build-isolation``
    # makes this work on restricted networks with the provisioned Hatchling.
    & $python -m pip install . --no-deps --no-build-isolation
    Assert-NativeSuccess 'CLI install'
    # The executor is bundled from a file path (not ``python -m``), so the
    # package-relative import fallback needs the sibling module directory on
    # PyInstaller's analysis path as well.
    $executorDist = Join-Path $repoRoot "build\desktop-executor-$version"
    & $python -m PyInstaller --noconfirm --clean --onefile --paths src\smara --hidden-import agent_tools --hidden-import pypdf --hidden-import docx --hidden-import openpyxl --hidden-import pptx --name smara-desktop --distpath $executorDist --workpath build\pyinstaller src\smara\desktop_executor.py
    Assert-NativeSuccess 'PyInstaller'
    $resourceDir = Join-Path $appRoot 'src-tauri\resources'
    New-Item -ItemType Directory -Force -Path $resourceDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $executorDist 'smara-desktop.exe') -Destination (Join-Path $resourceDir 'smara-desktop.exe') -Force
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
