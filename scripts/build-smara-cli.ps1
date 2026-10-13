[CmdletBinding()]
param([ValidateSet('dev', 'release')][string]$Profile = 'release', [switch]$SkipNativeBuild, [string]$SourceManifest)
$ErrorActionPreference = 'Stop'
$smaraCliRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $SkipNativeBuild) { & (Join-Path $PSScriptRoot 'build-smara-native.ps1') -Profile $Profile }
$smaraCliDist = Join-Path $smaraCliRoot 'build\native-cli-candidate'
$smaraCliPython = Join-Path $smaraCliRoot '.venv\Scripts\python.exe'
if ($SourceManifest) {
    $SourceManifest = (Resolve-Path -LiteralPath $SourceManifest).Path
    & $smaraCliPython (Join-Path $PSScriptRoot 'native_release_manifest.py') verify --manifest $SourceManifest
    if ($LASTEXITCODE -ne 0) { throw 'CLI source provenance failed' }
} else { Write-Warning 'Unsealed CLI candidate: pass -SourceManifest before publication.' }
Push-Location $smaraCliRoot
try {
    $smaraCliArgs = @('-m','PyInstaller','--noconfirm','--clean','--onefile','--name','smara','--paths','src',
        '--distpath',$smaraCliDist,'--workpath','build\pyinstaller-native-cli','--specpath','build',
        '--add-data',((Join-Path $smaraCliRoot 'release\VERSION') + ';release'),
        '--hidden-import','smara.native_tools','--hidden-import','smara.native_limits','--hidden-import','smara.native_documents','--hidden-import','smara.native_profiles','--hidden-import','smara.native_management','--hidden-import','smara.native_changes','--hidden-import','smara.native_schedule','--hidden-import','smara.native_goals','--hidden-import','smara.native_goal_runtime','--hidden-import','smara.native_telegram','--hidden-import','smara.native_browser','--collect-all','playwright')
    foreach ($smaraCliExcluded in @('pandas','pyarrow','datasets','pytest','tkinter','_tkinter',
        'numpy','psycopg','psycopg_pool','smara.store',
        'smara.agent_tools','smara.autonomous_agent','smara.cli','smara.desktop_executor',
        'smara.local_agent_runtime','smara.self_healing','smara.swarm','smara.tool_synthesis')) {
        $smaraCliArgs += @('--exclude-module', $smaraCliExcluded)
    }
    $smaraCliArgs += @('--hidden-import','smara.native_outcomes','--hidden-import','smara.native_voice')
    $smaraCliArgs += @('--hidden-import','smara.native_sources')
    $smaraCliArgs += @('--hidden-import','smara.native_memory','--collect-all','syntarus')
    $smaraCliArgs += @('--hidden-import','smara.native_budget','--hidden-import','smara.native_admission','--hidden-import','smara.native_outreach','--hidden-import','smara.native_mail_oauth')
    $smaraCliArgs += @('--hidden-import','smara.native_background','--hidden-import','smara.native_browser_control','--hidden-import','smara.native_browser_transfer','--hidden-import','smara.native_computer','--hidden-import','smara.native_windows','--collect-all','pywinauto','--collect-all','comtypes','--hidden-import','psutil')
    $smaraCliArgs += @('--hidden-import','smara.native_pdf','--collect-all','pypdfium2','--collect-all','pypdfium2_raw')
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
    if ($SourceManifest) {
        & $smaraCliPython (Join-Path $PSScriptRoot 'native_release_manifest.py') verify --manifest $SourceManifest
        if ($LASTEXITCODE -ne 0) { throw 'Post-build CLI source provenance failed' }
    }
} finally { Pop-Location }
Write-Host "Native portable CLI candidate: $smaraCliDist. Install/release gates are separate."
