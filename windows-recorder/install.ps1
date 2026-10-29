<#
.SYNOPSIS
  Installs the Meeting Recorder on Windows.

.DESCRIPTION
  Creates a private Python environment next to this script, installs the
  dependencies, writes your config, and (optionally) makes the recorder start
  when you log in. No administrator rights needed. Safe to run again.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1

.EXAMPLE
  # Fully unattended - what an AI agent or a deployment script should use:
  powershell -ExecutionPolicy Bypass -File .\install.ps1 `
      -ApiUrl http://127.0.0.1:8766 -ApiKey $env:MN_KEY `
      -NonInteractive -SpeakerSeparation -AutoDetect

.PARAMETER ApiUrl
  Where your Meeting Notes server is. Default http://127.0.0.1:8766 (same computer).

.PARAMETER ApiKey
  The server's MEETING_NOTES_API_KEY. If omitted you are asked, with the typing hidden.
  Prefer passing it through an environment variable so it never appears in a command line.

.PARAMETER SpeakerSeparation
  Also install the (optional) speaker-separation models - 36 MB, checksum-verified.

.PARAMETER AutoDetect
  Start recording automatically when Zoom / Teams / Webex opens. OFF by default:
  recording people without them knowing is illegal in many places. Only enable it
  if everyone you meet with is aware that calls are recorded.

.PARAMETER LiveTranscript
  Show a small always-on-top window with the transcript as people speak.

.PARAMETER NoAutoStart
  Do not add a Startup shortcut (the recorder will not launch at login).

.PARAMETER NoStart
  Install only; do not launch it now.

.PARAMETER NonInteractive
  Never prompt. Fails with a clear message if something required is missing.
#>
[CmdletBinding()]
param(
    [string]$ApiUrl = "http://127.0.0.1:8766",
    [string]$ApiKey = $env:MN_API_KEY,
    [switch]$SpeakerSeparation,
    [switch]$AutoDetect,
    [switch]$LiveTranscript,
    [switch]$NoAutoStart,
    [switch]$NoStart,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
$Here    = $PSScriptRoot
$Venv    = Join-Path $Here "venv"
$VenvPy  = Join-Path $Venv "Scripts\python.exe"
$Home_   = if ($env:MEETING_RECORDER_HOME) { $env:MEETING_RECORDER_HOME } else { Join-Path $env:USERPROFILE "MeetingRecorder" }
$Config  = Join-Path $Home_ "config.json"

function Step($n, $msg) { Write-Host ""; Write-Host "[$n] $msg" -ForegroundColor Cyan }
function Ok($msg)       { Write-Host "    OK  $msg" -ForegroundColor Green }
function Warn($msg)     { Write-Host "    !!  $msg" -ForegroundColor Yellow }
function Fail($msg)     { Write-Host ""; Write-Host "FAILED: $msg" -ForegroundColor Red; exit 1 }

Write-Host "Meeting Notes - recorder install" -ForegroundColor White
Write-Host "  folder : $Here"
Write-Host "  data   : $Home_"

# ---------------------------------------------------------------- 1. Python ---
Step 1 "Finding Python 3.10 or newer"
$py = $null
foreach ($cand in @(@("py", "-3"), @("python"), @("python3"))) {
    try {
        $exe = $cand[0]; $rest = @($cand | Select-Object -Skip 1)
        $v = & $exe @rest -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) {
            $parts = $v.Trim().Split(".")
            if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 10) { $py = @($exe) + $rest; Ok "Python $v ($exe)"; break }
        }
    } catch { }
}
if (-not $py) {
    Fail "Python 3.10+ was not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this again."
}

# ------------------------------------------------------------------- 2. venv ---
Step 2 "Creating a private environment (nothing is installed system-wide)"
if (-not (Test-Path $VenvPy)) {
    & $py[0] @($py | Select-Object -Skip 1) -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Fail "Could not create the virtual environment." }
}
Ok $Venv

Step 3 "Installing dependencies (a few minutes the first time)"
& $VenvPy -m pip install --disable-pip-version-check --quiet --upgrade pip
& $VenvPy -m pip install --disable-pip-version-check --quiet -r (Join-Path $Here "requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "Installing requirements.txt failed. To see the full error, run: $VenvPy -m pip install -r requirements.txt" }
Ok "recorder dependencies"

if ($SpeakerSeparation) {
    & $VenvPy -m pip install --disable-pip-version-check --quiet -r (Join-Path $Here "requirements-diarize.txt")
    if ($LASTEXITCODE -ne 0) { Fail "Installing speaker-separation dependencies failed." }
    & $VenvPy (Join-Path $Here "fetch_models.py")
    if ($LASTEXITCODE -ne 0) { Fail "Downloading the speaker-separation models failed (see message above)." }
    Ok "speaker separation"
}

# ------------------------------------------------------------------ 4. config ---
Step 4 "Writing your settings"
New-Item -ItemType Directory -Force -Path $Home_ | Out-Null

if (Test-Path $Config) {
    Ok "kept your existing $Config (not overwritten)"
} else {
    # Same computer as the server? Take the key from its backend\.env, so nobody has to
    # copy a secret around (and it never appears on screen or in a command line).
    $ServerEnv = Join-Path (Split-Path $Here) "backend\.env"
    if (-not $ApiKey -and $ApiUrl -match "^http://(127\.|localhost|\[::1\])" -and (Test-Path $ServerEnv)) {
        $m = Select-String -Path $ServerEnv -Pattern '^\s*MEETING_NOTES_API_KEY\s*=\s*(\S+)' | Select-Object -First 1
        if ($m) { $ApiKey = $m.Matches[0].Groups[1].Value; Ok "using the API key from $ServerEnv (not shown)" }
    }
    if (-not $ApiKey) {
        if ($NonInteractive) { Fail "No API key. Pass -ApiKey, or set the MN_API_KEY environment variable." }
        Write-Host "    Paste your server's MEETING_NOTES_API_KEY (typing is hidden):"
        $sec = Read-Host -AsSecureString
        $ApiKey = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    }
    if ($ApiKey.Length -lt 16) { Fail "That API key is too short to be real. Copy MEETING_NOTES_API_KEY from the server's backend/.env." }

    $cfg = [ordered]@{
        api_url        = $ApiUrl
        api_key        = $ApiKey
        spool_enabled  = $true             # audio is written to disk as it is captured: a crash loses nothing
        auto_detect    = [bool]$AutoDetect
        diarize_others = [bool]$SpeakerSeparation
        live_transcript = [bool]$LiveTranscript
    }
    # UTF-8 *without* a byte-order mark. Windows PowerShell's own -Encoding UTF8 adds one,
    # which some JSON readers reject.
    [IO.File]::WriteAllText($Config, ($cfg | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    Ok "wrote $Config"
}

if ($ApiUrl.StartsWith("http://") -and $ApiUrl -notmatch "^http://(127\.|localhost|\[::1\])") {
    Warn "api_url is plain http:// to another computer. The recorder will refuse to use it unless you use https:// (docs\INSTALL.md) or set allow_insecure_http."
}

# ---------------------------------------------------------------- 5. startup ---
$Launcher = Join-Path $Here "start-supervisor.vbs"
$Lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "MeetingRecorder.lnk"
Step 5 "Starting at login"
if ($NoAutoStart) {
    Warn "skipped (-NoAutoStart). Start it yourself with: explorer.exe `"$Launcher`""
} else {
    $sh = New-Object -ComObject WScript.Shell
    $sc = $sh.CreateShortcut($Lnk)
    $sc.TargetPath = "$env:WINDIR\System32\wscript.exe"
    $sc.Arguments = "`"$Launcher`""
    $sc.WorkingDirectory = $Here
    $sc.Description = "Meeting Notes recorder"
    $sc.Save()
    Ok "shortcut: $Lnk"
}

# ------------------------------------------------------------------- 6. start ---
Step 6 "Launching"
if ($NoStart) {
    Warn "not started (-NoStart)"
} else {
    # Explorer starts it the same way Windows does at login - outside whatever
    # terminal, editor or app you happen to be running this from.
    Start-Process explorer.exe -ArgumentList "`"$Launcher`""
    Start-Sleep -Seconds 12
    $log = Join-Path $Home_ "recorder.log"
    if ((Test-Path $log) -and (Select-String -Path $log -Pattern "startup: complete" -Quiet)) {
        Ok "the recorder is running"
    } else {
        Warn "no 'startup: complete' line in $log yet. Run the doctor:"
        Write-Host "      $VenvPy `"$(Join-Path (Split-Path $Here) 'scripts\doctor.py')`" --recorder"
    }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host @"

  Look for the blue circle in the system tray (bottom-right of the taskbar).
  Windows 11 hides new icons: click the ^ arrow, and drag it out to keep it visible
  (Settings > Personalization > Taskbar > Other system tray icons).

  Check everything at any time:
      $VenvPy "$(Join-Path (Split-Path $Here) 'scripts\doctor.py')" --recorder

  Recording laws: tell the people you meet with that calls are recorded.
"@
