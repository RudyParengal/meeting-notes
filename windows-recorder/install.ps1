# Meeting Recorder — one-shot Windows installer
# Run in PowerShell (no admin needed):
#   powershell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = "Stop"
$AppDir = "$env:LOCALAPPDATA\MeetingRecorder"
$VenvDir = "$AppDir\venv"

Write-Host "=== Meeting Recorder Installer ===" -ForegroundColor Cyan

# 1. Check Python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "ERROR: Python not found. Install Python 3.11+ from python.org first." -ForegroundColor Red
    exit 1
}
$version = & python --version 2>&1
Write-Host "Found: $version"

# 2. Create app dir and copy files
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
Copy-Item -Force -Path "$PSScriptRoot\recorder.py" -Destination "$AppDir\recorder.py"
Copy-Item -Force -Path "$PSScriptRoot\requirements.txt" -Destination "$AppDir\requirements.txt"

# 3. Create venv and install deps
if (-not (Test-Path $VenvDir)) {
    Write-Host "Creating virtual environment..."
    & python -m venv $VenvDir
}
Write-Host "Installing Python packages (this takes 3-5 minutes)..."
& "$VenvDir\Scripts\pip.exe" install --quiet --upgrade pip
& "$VenvDir\Scripts\pip.exe" install --quiet -r "$AppDir\requirements.txt"

# 4. Create startup shortcut
$StartupDir = [Environment]::GetFolderPath("Startup")
$ShortcutPath = "$StartupDir\MeetingRecorder.lnk"
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "$VenvDir\Scripts\pythonw.exe"
$Shortcut.Arguments = "`"$AppDir\recorder.py`""
$Shortcut.WorkingDirectory = $AppDir
$Shortcut.WindowStyle = 7  # minimized
$Shortcut.Description = "Meeting Recorder tray app"
$Shortcut.Save()
Write-Host "Created startup shortcut: $ShortcutPath"

# 5. Create Start Menu shortcut
$StartMenu = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
$SMPath = "$StartMenu\MeetingRecorder.lnk"
$Shortcut2 = $WshShell.CreateShortcut($SMPath)
$Shortcut2.TargetPath = "$VenvDir\Scripts\pythonw.exe"
$Shortcut2.Arguments = "`"$AppDir\recorder.py`""
$Shortcut2.WorkingDirectory = $AppDir
$Shortcut2.Save()

# 6. Prompt for API key
Write-Host ""
Write-Host "=== Configuration ===" -ForegroundColor Cyan
$apiKey = Read-Host "Enter your Meeting Notes API key (from VPS .env file, starts with mn_)"
$apiUrl = Read-Host "API URL (press Enter for default https://notes.example.com)"
if (-not $apiUrl) { $apiUrl = "https://notes.example.com" }

$configPath = "$env:APPDATA\MeetingRecorder\config.json"
New-Item -ItemType Directory -Force -Path "$env:APPDATA\MeetingRecorder" | Out-Null
$config = @{
    api_url = $apiUrl
    api_key = $apiKey
    whisper_model = "distil-large-v3"
    whisper_compute_type = "int8"
    whisper_device = "cpu"
    sample_rate = 16000
    chunk_seconds = 30
    auto_detect = $false
    auto_detect_processes = @("Zoom.exe", "Teams.exe", "ms-teams.exe", "zoom.exe")
    auto_detect_browser_meeting = $true
    meeting_type_default = "auto"
    notify_on_complete = $true
}
$config | ConvertTo-Json -Depth 5 | Set-Content -Path $configPath -Encoding UTF8
Write-Host "Config saved to: $configPath"

Write-Host ""
Write-Host "=== Installation complete ===" -ForegroundColor Green
Write-Host "To start now, run:"
Write-Host "  & `"$VenvDir\Scripts\pythonw.exe`" `"$AppDir\recorder.py`"" -ForegroundColor Yellow
Write-Host "It will launch automatically next time you log in (tray icon)."
Write-Host "First launch downloads the Whisper model (~1.5 GB) — wait 2-3 min."
