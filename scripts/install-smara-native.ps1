[CmdletBinding()]
param([Parameter(Mandatory)][string]$InstallerPath)

$ErrorActionPreference = 'Stop'
$smaraRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$smaraVersion = (Get-Content -LiteralPath (Join-Path $smaraRoot 'release\VERSION') -Raw).Trim()
$smaraInstaller = (Resolve-Path -LiteralPath $InstallerPath).Path
$smaraBundleRoot = Join-Path $smaraRoot 'apps\desktop\src-tauri\target\release\bundle\nsis'
if (-not $smaraInstaller.StartsWith($smaraBundleRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
    [IO.Path]::GetFileName($smaraInstaller) -ne "Smara Desktop_${smaraVersion}_x64-setup.exe") {
    throw 'Select the exact locally built installer for release/VERSION.'
}
$smaraCliSource = Join-Path $smaraRoot 'build\native-cli-candidate'
$smaraDesktopSource = Join-Path $smaraRoot 'apps\desktop\src-tauri\target\release\smara-desktop.exe'
. (Join-Path $PSScriptRoot 'smara-install-validation.ps1')
$smaraDesktopExpectedHash = Get-SmaraNsisPayloadHash -Path $smaraDesktopSource
if ((Get-Item -LiteralPath $smaraDesktopSource).VersionInfo.FileVersion -ne $smaraVersion) {
    throw 'Desktop binary version does not match release/VERSION.'
}
$smaraPayloadFiles = @('smara-native.exe', 'codex-command-runner.exe', 'codex-windows-sandbox-setup.exe', 'LICENSE', 'NOTICE', 'UPSTREAM.json')
foreach ($smaraPayloadFile in $smaraPayloadFiles) {
    $smaraExpected = (Get-FileHash -LiteralPath (Join-Path $smaraRoot "native\dist\$smaraPayloadFile")).Hash
    foreach ($smaraPayloadRoot in @((Join-Path $smaraCliSource 'native'), (Join-Path $smaraRoot 'apps\desktop\src-tauri\resources\native'))) {
        if ((Get-FileHash -LiteralPath (Join-Path $smaraPayloadRoot $smaraPayloadFile)).Hash -ne $smaraExpected) {
            throw "Stale or missing package payload: $smaraPayloadFile"
        }
    }
}
$smaraDesktopTarget = Join-Path $env:LOCALAPPDATA 'Smara Desktop'
if (Get-Process -Name 'smara-desktop' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq (Join-Path $smaraDesktopTarget 'smara-desktop.exe') }) {
    throw 'Close the installed Desktop before upgrading; active work is not terminated.'
}
# Preserve even old versions that accidentally stored user skills in INSTDIR.
# The NSIS uninstall step must not be the only copy of those files.
$smaraBackup = Join-Path $env:LOCALAPPDATA ('Smara-backups\desktop-pre-' + $smaraVersion + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
if (Test-Path -LiteralPath $smaraDesktopTarget) {
    if (Get-ChildItem -LiteralPath $smaraDesktopTarget -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) {
        throw 'Review install-directory links before backup; do not copy outside data.'
    }
    New-Item -ItemType Directory -Path (Split-Path $smaraBackup) -Force | Out-Null
    Copy-Item -LiteralPath $smaraDesktopTarget -Destination $smaraBackup -Recurse -Force
}
$smaraStateHashes = @{}
foreach ($smaraStateFile in @('desktop.json', 'desktop-ui.json', 'credentials.json')) {
    $smaraStatePath = Join-Path $env:APPDATA "Smara\$smaraStateFile"
    if (Test-Path -LiteralPath $smaraStatePath) {
        $smaraStateHashes[$smaraStatePath] = (Get-FileHash -LiteralPath $smaraStatePath).Hash
    }
}
$smaraInstallProcess = Start-Process -FilePath $smaraInstaller -ArgumentList @('/S', "/D=$smaraDesktopTarget") -WindowStyle Hidden -PassThru
if (-not $smaraInstallProcess.WaitForExit(60000)) {
    throw 'Installer outcome is unknown after 60 seconds; inspect it before retrying.'
}
if ($smaraInstallProcess.ExitCode -ne 0) { throw "Installer failed: $($smaraInstallProcess.ExitCode)" }
if ((Get-FileHash -LiteralPath (Join-Path $smaraDesktopTarget 'smara-desktop.exe')).Hash -ne $smaraDesktopExpectedHash) {
    throw 'Installed Desktop does not match the tested release binary.'
}
foreach ($smaraPayloadFile in $smaraPayloadFiles) {
    if ((Get-FileHash -LiteralPath (Join-Path $smaraDesktopTarget "resources\native\$smaraPayloadFile")).Hash -ne
        (Get-FileHash -LiteralPath (Join-Path $smaraRoot "native\dist\$smaraPayloadFile")).Hash) {
        throw "Installed Desktop payload mismatch: $smaraPayloadFile"
    }
}
if ((Get-FileHash -LiteralPath (Join-Path $smaraDesktopTarget 'resources\smara-desktop.exe')).Hash -ne
    (Get-FileHash -LiteralPath (Join-Path $smaraRoot 'apps\desktop\src-tauri\resources\smara-desktop.exe')).Hash) {
    throw 'Installed executor does not match the tested frozen executor.'
}
$smaraOldSkills = Join-Path $smaraBackup '.smara'
if (Test-Path -LiteralPath $smaraOldSkills) {
    $smaraRestoredSkills = Join-Path $smaraDesktopTarget '.smara'
    New-Item -ItemType Directory -Path $smaraRestoredSkills -Force | Out-Null
    Get-ChildItem -LiteralPath $smaraOldSkills -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $smaraRestoredSkills -Recurse -Force
    }
    Get-ChildItem -LiteralPath $smaraOldSkills -Recurse -File -Force | ForEach-Object {
        $smaraRelativeSkill = $_.FullName.Substring($smaraOldSkills.Length + 1)
        if ((Get-FileHash -LiteralPath $_.FullName).Hash -ne (Get-FileHash -LiteralPath (Join-Path $smaraRestoredSkills $smaraRelativeSkill)).Hash) {
            throw 'An existing install-directory skill was not restored intact.'
        }
    }
}
foreach ($smaraStatePath in $smaraStateHashes.Keys) {
    if ((Get-FileHash -LiteralPath $smaraStatePath).Hash -ne $smaraStateHashes[$smaraStatePath]) {
        throw 'Existing state changed during installation; review before launching.'
    }
}
$smaraCliTarget = Join-Path $env:LOCALAPPDATA "Programs\Smara CLI\$smaraVersion"
New-Item -ItemType Directory -Path (Join-Path $smaraCliTarget 'native') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $smaraCliSource 'smara.exe') -Destination $smaraCliTarget -Force
foreach ($smaraPayloadFile in $smaraPayloadFiles) {
    Copy-Item -LiteralPath (Join-Path $smaraCliSource "native\$smaraPayloadFile") -Destination (Join-Path $smaraCliTarget 'native') -Force
}
$smaraUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($smaraUserPath -split ';') -contains $smaraCliTarget)) {
    [Environment]::SetEnvironmentVariable('Path', "$smaraCliTarget;$smaraUserPath", 'User')
}
Write-Host "Installed Desktop $smaraVersion at $smaraDesktopTarget"
Write-Host "Installed CLI at $smaraCliTarget. Open a new terminal for its updated PATH."
Write-Host "Previous Desktop, including its install-directory skills, is preserved at $smaraBackup"
Write-Host 'Existing Desktop preferences and protected credentials are unchanged.'
