param(
    [string]$HubUrl = "http://127.0.0.1:24808",
    [switch]$Hub,
    [switch]$NoStartup,
    [string]$ComputerName = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$DataDir = Join-Path $env:LOCALAPPDATA "deskflow-tailscale"
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

# 1. Check Tailscale
$ts = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $ts -and (Test-Path "C:\Program Files\Tailscale\tailscale.exe")) {
    $ts = "C:\Program Files\Tailscale\tailscale.exe"
}
if (-not $ts) {
    Write-Warning "Tailscale CLI not found. Install & sign in to Tailscale first."
}

# 2. Check / install Deskflow
$dfCore = "C:\Program Files\Deskflow\deskflow-core.exe"
if (-not (Test-Path $dfCore)) {
    Write-Host "Installing Deskflow via winget..."
    winget install -e --id Deskflow.Deskflow --accept-package-agreements --accept-source-agreements
}
if (-not (Test-Path $dfCore)) {
    throw "Deskflow core binary not found at $dfCore"
}

# 3. Ensure Python is available
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyCmd) {
    throw "Python 3 not found in PATH."
}
$pyExe = $pyCmd.Source
$pywExe = Join-Path (Split-Path -Parent $pyExe) "pythonw.exe"
if (-not (Test-Path $pywExe)) { $pywExe = $pyExe }

# 4. Best-effort Windows Firewall rules for Tailscale/Deskflow ports
try {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
    if ($isAdmin) {
        New-NetFirewallRule -DisplayName "Deskflow-Fleet-24800" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 24800 -ErrorAction SilentlyContinue | Out-Null
        if ($Hub) {
            New-NetFirewallRule -DisplayName "Deskflow-Fleet-Hub-24808" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 24808 -ErrorAction SilentlyContinue | Out-Null
        }
    }
} catch {}

# 5. Write agent config
$cfg = @{
    hub_url = $HubUrl.TrimEnd("/")
    computer_name = $ComputerName
} | ConvertTo-Json
Set-Content -Path (Join-Path $DataDir "config.json") -Value $cfg -Encoding UTF8

function Stop-ByPidFile($PidFile) {
    if (Test-Path $PidFile) {
        $oldPid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
        if ($oldPid -match '^\d+$') {
            Stop-Process -Id ([int]$oldPid) -Force -ErrorAction SilentlyContinue
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
}

# 6. Start Hub if requested
if ($Hub) {
    $hubPidFile = Join-Path $DataDir "hub.pid"
    Stop-ByPidFile $hubPidFile
    $hubScript = Join-Path $RepoRoot "src\hub.py"
    $hubProc = Start-Process -FilePath $pywExe -ArgumentList "`"$hubScript`" --host 0.0.0.0 --port 24808" -WindowStyle Hidden -PassThru
    Set-Content -Path $hubPidFile -Value $hubProc.Id -Encoding ASCII
}

# 7. Start Agent
$agentPidFile = Join-Path $DataDir "agent.pid"
Stop-ByPidFile $agentPidFile
$agentScript = Join-Path $RepoRoot "src\agent.py"
$agentProc = Start-Process -FilePath $pywExe -ArgumentList "`"$agentScript`" --hub-url `"$HubUrl`"" -WindowStyle Hidden -PassThru
Set-Content -Path $agentPidFile -Value $agentProc.Id -Encoding ASCII

# 8. Register per-user logon startup
if (-not $NoStartup) {
    $runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    if (-not (Test-Path $runKey)) { New-Item -Path $runKey -Force | Out-Null }
    Set-ItemProperty -Path $runKey -Name "DeskflowFleetAgent" -Value "`"$pywExe`" `"$agentScript`" --hub-url `"$HubUrl`""
    if ($Hub) {
        $hubScript = Join-Path $RepoRoot "src\hub.py"
        Set-ItemProperty -Path $runKey -Name "DeskflowFleetHub" -Value "`"$pywExe`" `"$hubScript`" --host 0.0.0.0 --port 24808"
    }
}

Write-Host "Deskflow Fleet installed and running! Hub URL: $HubUrl"
