<#
Publish a built Smara Desktop installer to Smara's server, where installed apps find it (in-app "Update and restart").

  powershell -File scripts\publish-desktop-update.ps1 -Notes "What changed, one line"

Needs: a finished build (scripts\build-smara-desktop.ps1, which signs the installer and writes its .sig next to it) and SSH
access to the server. Uploads the installer, then writes latest.json last, so an app never sees a release whose file is
still uploading. The app installs it only if the signature matches the public key built into it.
#>
param(
    [Parameter(Mandatory = $true)][string]$Notes,
    [string]$Server = 'ubuntu@140.245.251.13',
    [string]$Key = (Join-Path $HOME 'Downloads\ssh-key-2026-05-29.key')
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$version = (Get-Content (Join-Path $root 'apps\desktop\src-tauri\tauri.conf.json') -Raw | ConvertFrom-Json).version
$name = "Smara Desktop_${version}_x64-setup.exe"
$installer = Join-Path $root "apps\desktop\src-tauri\target\release\bundle\nsis\$name"
$signature = "$installer.sig"
if (-not (Test-Path $installer) -or -not (Test-Path $signature)) { throw "Build $version first: $name and its .sig are missing." }

$manifest = [ordered]@{ version = $version; file = $name; notes = $Notes; pub_date = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ');
                        signature = (Get-Content $signature -Raw).Trim() }
$temp = Join-Path ([IO.Path]::GetTempPath()) "smara-latest-$version.json"
[IO.File]::WriteAllText($temp, ($manifest | ConvertTo-Json), [Text.UTF8Encoding]::new($false))

$stage = "~/desktop-release-$version"
ssh -i $Key $Server "mkdir -p $stage"
scp -i $Key $installer "${Server}:$stage/"
scp -i $Key $temp "${Server}:$stage/latest.json"
# Into the app's releases volume: the installer first, the manifest last.
ssh -i $Key $Server "docker cp '$stage/$name' smara_app:/app/data/desktop-releases/ && docker cp $stage/latest.json smara_app:/app/data/desktop-releases/latest.json && rm -rf $stage"
if ($LASTEXITCODE -ne 0) { throw 'Copying into the server failed.' }
Remove-Item $temp
$check = Invoke-WebRequest -UseBasicParsing "https://ai.syntarus.com/smara/desktop/update/windows/x86_64/0.0.1"
Write-Host "Published $version. The server now offers: $((($check.Content) | ConvertFrom-Json).version)"
