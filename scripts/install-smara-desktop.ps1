[CmdletBinding()]
param([Parameter(Mandatory)][string]$InstallerPath)

$ErrorActionPreference = 'Stop'
# Compatibility entry point only: never register the retired Python executor.
& (Join-Path $PSScriptRoot 'install-smara-native.ps1') -InstallerPath $InstallerPath

