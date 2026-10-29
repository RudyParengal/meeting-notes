<#
.SYNOPSIS
  Removes the Meeting Recorder's start-at-login entry and stops it.

.DESCRIPTION
  Your meetings, settings and audio backups are NOT deleted: they live in
  %USERPROFILE%\MeetingRecorder and %USERPROFILE%\MeetingArchive and stay where
  they are. Delete those folders yourself if you want them gone.
  The recorder will not be stopped while a meeting is being recorded.

.PARAMETER Force
  Stop it even if a recording looks active. You lose the rest of that meeting.
#>
[CmdletBinding()]
param([switch]$Force)

$ErrorActionPreference = "Stop"
$Here   = $PSScriptRoot
$Home_  = if ($env:MEETING_RECORDER_HOME) { $env:MEETING_RECORDER_HOME } else { Join-Path $env:USERPROFILE "MeetingRecorder" }

# A recording is live if any spooled audio file was written in the last two minutes.
$live = $false
$spool = Join-Path $Home_ "spool"
if (Test-Path $spool) {
    $live = [bool](Get-ChildItem $spool -Recurse -Filter *.pcm -ErrorAction SilentlyContinue |
                   Where-Object { $_.LastWriteTime -gt (Get-Date).AddMinutes(-2) })
}
if ($live -and -not $Force) {
    Write-Host "A meeting is being recorded right now. Not stopping it." -ForegroundColor Yellow
    Write-Host "Finish the meeting and run this again (or use -Force to stop anyway)."
    exit 2
}

$lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "MeetingRecorder.lnk"
if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host "removed the start-at-login shortcut" }

# Stop only processes that belong to THIS folder - never every python on the machine.
$mine = Get-CimInstance Win32_Process |
        Where-Object { $_.Name -match "^pythonw?\.exe$" -and $_.CommandLine -like "*$Here*" -and $_.ProcessId -ne $PID }
foreach ($p in $mine) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "stopped process $($p.ProcessId)" }

Write-Host ""
Write-Host "Uninstalled. Your data is untouched:" -ForegroundColor Green
Write-Host "  $Home_"
Write-Host "  $(Join-Path $env:USERPROFILE 'MeetingArchive')"
