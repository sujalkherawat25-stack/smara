[CmdletBinding()]
param(
    [string]$TaskName = 'Smara Desktop Executor'
)

$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Write-Host "Smara Desktop auto-start was removed. Revoke the desktop in Smara to invalidate its token."

