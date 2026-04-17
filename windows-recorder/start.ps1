$pythonw = "$env:USERPROFILE\Claude code\meeting-recorder\venv\Scripts\pythonw.exe"
$script = "$env:USERPROFILE\Claude code\meeting-recorder\recorder.py"
$workdir = "$env:USERPROFILE\Claude code\meeting-recorder"
Start-Process -FilePath $pythonw -ArgumentList "`"$script`"" -WorkingDirectory $workdir
Start-Sleep -Seconds 4
Write-Host "Python processes:"
Get-Process python* -ErrorAction SilentlyContinue | Select-Object Id, ProcessName
Write-Host "`nRecent log:"
Get-Content "$env:APPDATA\MeetingRecorder\recorder.log" -Tail 20 -ErrorAction SilentlyContinue
