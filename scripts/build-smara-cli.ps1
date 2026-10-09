[CmdletBinding()]
param([ValidateSet('dev', 'release')][string]$Profile = 'release', [switch]$SkipNativeBuild)
$ErrorActionPreference = 'Stop'
$smaraCliRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $SkipNativeBuild) { & (Join-Path $PSScriptRoot 'build-smara-native.ps1') -Profile $Profile }
$smaraCliDist = Join-Path $smaraCliRoot 'build\native-cli-candidate'
$smaraCliPython = Join-Path $smaraCliRoot '.venv\Scripts\python.exe'
Push-Location $smaraCliRoot
try {
    $smaraCliArgs = @('-m','PyInstaller','--noconfirm','--clean','--onefile','--name','smara','--paths','src',
        '--distpath',$smaraCliDist,'--workpath','build\pyinstaller-native-cli','--specpath','build',
        '--hidden-import','smara.native_tools','--hidden-import','smara.native_profiles','--hidden-import','smara.native_management','--hidden-import','smara.native_schedule','--hidden-import','smara.native_browser','--collect-all','playwright')
    foreach ($smaraCliExcluded in @('pandas','pyarrow','datasets','pytest','tkinter','_tkinter',
        'numpy','psycopg','psycopg_pool','smara.store',
        'smara.cli','smara.desktop_executor','smara.local_agent_runtime','smara.self_healing','smara.swarm')) {
        $smaraCliArgs += @('--exclude-module', $smaraCliExcluded)
    }
    $smaraCliArgs += 'scripts\native_cli_entry.py'
    & $smaraCliPython @smaraCliArgs
    if ($LASTEXITCODE -ne 0) { throw 'Native CLI freezing failed' }
    $smaraCliNative = Join-Path $smaraCliDist 'native'
    New-Item -ItemType Directory -Force -Path $smaraCliNative | Out-Null
    foreach ($smaraCliArtifact in @('smara-native.exe','codex-windows-sandbox-setup.exe','codex-command-runner.exe','LICENSE','NOTICE','UPSTREAM.json')) {
        $smaraCliSource = if ($smaraCliArtifact -eq 'UPSTREAM.json') { Join-Path $smaraCliRoot 'native\UPSTREAM.json' } else { Join-Path $smaraCliRoot "native\dist\$smaraCliArtifact" }
        Copy-Item -LiteralPath $smaraCliSource -Destination (Join-Path $smaraCliNative $smaraCliArtifact) -Force
    }
    & (Join-Path $smaraCliDist 'smara.exe') source-status
    if ($LASTEXITCODE -ne 0) { throw 'Packaged native source-status failed' }
} finally { Pop-Location }
Write-Host "Native portable CLI candidate: $smaraCliDist. Install/release gates are separate."
