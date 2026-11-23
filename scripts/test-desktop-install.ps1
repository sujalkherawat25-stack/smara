<#
.SYNOPSIS
  Installs a Smara Desktop installer silently as a normal user, checks it, launches it, and removes it again.

.DESCRIPTION
  Run this on a clean Windows PC or VM, logged in as a standard (non-administrator) user, with the installer copied
  there. It checks the installer's signature, a silent per-user install, the installed program's signature, the
  desktop shortcut, a launch that stays up, no new crash events, and a clean uninstall. Writes a plain-text report.

  Exit code 0 when every check passes, 1 otherwise.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$InstallerPath,
    [string]$ReportPath = (Join-Path $env:TEMP ("smara-install-report-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.txt')),
    [int]$LaunchSeconds = 25
)

$ErrorActionPreference = 'Continue'
$failures = 0
$lines = [System.Collections.Generic.List[string]]::new()

function Add-Result([string]$Status, [string]$Message) {
    if ($Status -eq 'FAIL') { $script:failures++ }
    $line = "[$Status] $Message"
    $lines.Add($line)
    Write-Host $line
}

$os = Get-CimInstance Win32_OperatingSystem
Add-Result 'INFO' ("Windows: {0} (build {1}), {2}" -f $os.Caption, $os.BuildNumber, $env:COMPUTERNAME)

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) {
    Add-Result 'WARN' 'Running as administrator. Run again as a standard user to test the per-user install path.'
} else {
    Add-Result 'PASS' 'Running as a standard user.'
}

$webView = @(
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
    'HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
) | Where-Object { Test-Path $_ }
if ($webView) {
    Add-Result 'INFO' 'WebView2 runtime is already installed.'
} else {
    Add-Result 'INFO' 'WebView2 runtime is not installed; the installer must fetch it (needs internet).'
}

if (-not (Test-Path -LiteralPath $InstallerPath)) {
    Add-Result 'FAIL' "Installer not found: $InstallerPath"
} else {
    $installerSig = Get-AuthenticodeSignature -LiteralPath $InstallerPath
    if ($installerSig.Status -eq 'Valid') {
        Add-Result 'PASS' "Installer signature is valid: $($installerSig.SignerCertificate.Subject)"
    } else {
        Add-Result 'FAIL' "Installer signature status is $($installerSig.Status): $($installerSig.StatusMessage)"
    }

    $startTime = Get-Date
    $install = Start-Process -FilePath $InstallerPath -ArgumentList '/S' -PassThru -Wait
    if ($install.ExitCode -eq 0) {
        Add-Result 'PASS' 'Silent install finished with exit code 0.'
    } else {
        Add-Result 'FAIL' "Silent install exited with code $($install.ExitCode)."
    }

    $installDir = Join-Path $env:LOCALAPPDATA 'Smara Desktop'
    $exe = Join-Path $installDir 'smara-desktop.exe'
    $shortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Smara Desktop.lnk'

    if (Test-Path -LiteralPath $exe) {
        Add-Result 'PASS' "Installed program found: $exe"
        $exeSig = Get-AuthenticodeSignature -LiteralPath $exe
        if ($exeSig.Status -eq 'Valid') {
            Add-Result 'PASS' "Installed program signature is valid: $($exeSig.SignerCertificate.Subject)"
        } else {
            Add-Result 'FAIL' "Installed program signature status is $($exeSig.Status)."
        }
    } else {
        Add-Result 'FAIL' "Installed program not found at $exe"
    }

    if (Test-Path -LiteralPath $shortcut) {
        Add-Result 'PASS' 'Desktop shortcut created.'
    } else {
        Add-Result 'FAIL' 'Desktop shortcut missing.'
    }

    if (Test-Path -LiteralPath $exe) {
        $launchedAt = Get-Date
        $app = Start-Process -FilePath $exe -PassThru
        Start-Sleep -Seconds $LaunchSeconds
        $app.Refresh()
        if (-not $app.HasExited) {
            Add-Result 'PASS' "Program still running after $LaunchSeconds seconds."
        } else {
            Add-Result 'FAIL' "Program exited early with code $($app.ExitCode)."
        }
        if (-not $app.HasExited) {
            Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue
        }

        $crashes = Get-WinEvent -FilterHashtable @{ LogName = 'Application'; StartTime = $launchedAt; Level = 1, 2 } -ErrorAction SilentlyContinue |
            Where-Object { $_.Message -match 'smara-desktop' }
        if ($crashes) {
            Add-Result 'FAIL' "$(@($crashes).Count) crash or error event(s) for smara-desktop during the launch."
        } else {
            Add-Result 'PASS' 'No crash events for smara-desktop during the launch.'
        }
    }

    $uninstaller = Join-Path $installDir 'uninstall.exe'
    if (Test-Path -LiteralPath $uninstaller) {
        $uninstall = Start-Process -FilePath $uninstaller -ArgumentList '/S' -PassThru -Wait
        Start-Sleep -Seconds 3
        if ($uninstall.ExitCode -eq 0 -and -not (Test-Path -LiteralPath $exe)) {
            Add-Result 'PASS' 'Silent uninstall removed the program.'
        } else {
            Add-Result 'FAIL' "Uninstall exit code $($uninstall.ExitCode); program still present: $(Test-Path -LiteralPath $exe)"
        }
        if (-not (Test-Path -LiteralPath $shortcut)) {
            Add-Result 'PASS' 'Desktop shortcut removed on uninstall.'
        } else {
            Add-Result 'FAIL' 'Desktop shortcut still present after uninstall.'
        }
    } else {
        Add-Result 'FAIL' "Uninstaller not found at $uninstaller"
    }
}

$summary = if ($failures -eq 0) { 'ALL CHECKS PASSED' } else { "$failures CHECK(S) FAILED" }
$lines.Insert(0, $summary)
$lines | Set-Content -LiteralPath $ReportPath -Encoding utf8
Write-Host ''
Write-Host "Report written to $ReportPath"
Write-Host $summary
exit ([int]($failures -ne 0))
