<#
.SYNOPSIS
  Authenticode-signs Windows files with a code-signing certificate, then checks each signature.

.DESCRIPTION
  The certificate is looked up by thumbprint in the current user's certificate store. A CA-issued code-signing
  certificate keeps its private key on a hardware token or a cloud key service, so the token or its client asks for
  the PIN itself. This script never reads, prints or stores a PIN or password.

  Release builds set SMARA_SIGN_THUMBPRINT (and optionally SMARA_SIGN_TIMESTAMP_URL). See docs/WINDOWS_CODE_SIGNING.md.

.PARAMETER VerifyOnly
  Do not sign; only check that the files already carry a valid signature from the thumbprint.

.PARAMETER AllowUntrustedForTest
  For a throwaway self-signed certificate only. Accepts a signature whose chain is not trusted. Never use for releases.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string[]]$Files,
    [string]$Thumbprint = $env:SMARA_SIGN_THUMBPRINT,
    [string]$TimestampUrl = $(if ($env:SMARA_SIGN_TIMESTAMP_URL) { $env:SMARA_SIGN_TIMESTAMP_URL } else { 'http://timestamp.digicert.com' }),
    [switch]$VerifyOnly,
    [switch]$AllowUntrustedForTest
)

$ErrorActionPreference = 'Stop'

function Get-CleanThumbprint([string]$Value) {
    $clean = ($Value -replace '\s', '').ToUpperInvariant()
    if ($clean -notmatch '^[0-9A-F]{40}$') {
        throw 'The certificate thumbprint must be 40 hexadecimal characters (SMARA_SIGN_THUMBPRINT).'
    }
    return $clean
}

function Find-SignTool {
    $bin = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    $found = Get-ChildItem -Path $bin -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
        Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $found) {
        throw 'signtool.exe was not found. Install the Windows SDK (Signing Tools for Desktop Apps).'
    }
    return $found.FullName
}

function Get-SigningCertificate([string]$Print) {
    $cert = Get-ChildItem -Path Cert:\CurrentUser\My | Where-Object { $_.Thumbprint -eq $Print } | Select-Object -First 1
    if (-not $cert) {
        throw "Certificate $Print is not in Cert:\CurrentUser\My. Plug in the token, or start the cloud signing client, then try again."
    }
    if (-not $cert.HasPrivateKey) {
        throw 'The certificate has no private key on this PC. The token or cloud key is missing.'
    }
    $codeSigning = $cert.EnhancedKeyUsageList | Where-Object { $_.FriendlyName -eq 'Code Signing' }
    if (-not $codeSigning) {
        throw 'This certificate is not marked for code signing.'
    }
    if ($cert.NotAfter -lt (Get-Date).AddDays(7)) {
        throw "The certificate expires on $($cert.NotAfter.ToString('yyyy-MM-dd')). Renew it before releasing."
    }
    return $cert
}

function Assert-Signed([string]$File, [string]$ExpectedThumbprint, [bool]$RequireTrusted) {
    $signature = Get-AuthenticodeSignature -LiteralPath $File
    if ($null -eq $signature.SignerCertificate) {
        throw "$File is not signed."
    }
    if ($ExpectedThumbprint -and $signature.SignerCertificate.Thumbprint -ne $ExpectedThumbprint) {
        throw "$File is signed by a different certificate ($($signature.SignerCertificate.Subject))."
    }
    if ($RequireTrusted -and $signature.Status -ne 'Valid') {
        throw "$File signature status is $($signature.Status): $($signature.StatusMessage)"
    }
    Write-Host "OK  $File  status=$($signature.Status)  signer=$($signature.SignerCertificate.Subject)"
}

$targets = @($Files | ForEach-Object { (Resolve-Path -LiteralPath $_).Path })
$expected = $null
if ($Thumbprint) {
    $expected = Get-CleanThumbprint $Thumbprint
}

if (-not $VerifyOnly) {
    if (-not $expected) {
        throw 'No certificate given. Set SMARA_SIGN_THUMBPRINT or pass -Thumbprint, or use -VerifyOnly.'
    }
    $cert = Get-SigningCertificate $expected
    $signtool = Find-SignTool
    foreach ($target in $targets) {
        # Only unsigned builds are signed here. A second signature would make the result hard to verify.
        $existing = Get-AuthenticodeSignature -LiteralPath $target
        if ($existing.Status -ne 'NotSigned') {
            throw "$target already has a signature ($($existing.Status)). Sign only unsigned builds."
        }
        $signArgs = @('sign', '/sha1', $cert.Thumbprint, '/fd', 'sha256')
        if ($TimestampUrl) {
            $signArgs += @('/tr', $TimestampUrl, '/td', 'sha256')
        }
        & $signtool @signArgs $target
        if ($LASTEXITCODE -ne 0) {
            throw "signtool failed for $target (exit code $LASTEXITCODE)."
        }
    }
}

foreach ($target in $targets) {
    Assert-Signed $target $expected (-not $AllowUntrustedForTest)
}
