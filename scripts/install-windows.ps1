param(
    [string]$HubUrl = "http://127.0.0.1:24808",
    [switch]$Hub,
    [switch]$NoStartup,
    [string]$ComputerName = "",
    [string]$PackageSpec = ""
)
$ErrorActionPreference = "Stop"

$ScriptPath = $MyInvocation.MyCommand.Path
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } elseif ($ScriptPath) { Split-Path -Parent $ScriptPath } else { "" }
$ParentDir = if ($ScriptDir) { Split-Path -Parent $ScriptDir } else { "" }
if (-not $PackageSpec) {
    if ($ParentDir -and (Test-Path (Join-Path $ParentDir "pyproject.toml"))) {
        $PackageSpec = $ParentDir
    } else {
        $PackageSpec = "https://github.com/IAFahim/deskflow-tailscale/archive/refs/heads/main.tar.gz"
    }
}

$DataDir = Join-Path $env:LOCALAPPDATA "deskflow-tailscale"
$VenvDir = Join-Path $DataDir "venv"
$RunDir = Join-Path $DataDir "run"
New-Item -ItemType Directory -Force -Path $DataDir, $RunDir | Out-Null

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

# 3. Ensure Python 3 & create isolated venv + install deskflow-tailscale + 4 tiny libs
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyCmd) {
    throw "Python 3 not found in PATH"
}
$pyExe = $pyCmd.Source
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"
$VenvPyW = Join-Path $VenvDir "Scripts\pythonw.exe"
if (-not (Test-Path $VenvPy)) {
    Write-Host "Creating Python venv at $VenvDir..."
    & $pyExe -m venv $VenvDir
}
Write-Host "Installing deskflow-tailscale fleet package..."
& $VenvPy -m pip install --upgrade --no-cache-dir --quiet $PackageSpec
$LauncherPy = if (Test-Path $VenvPyW) { $VenvPyW } else { $VenvPy }

# 4. Best-effort Windows Firewall rules for Tailscale/Deskflow ports
try {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
    if ($isAdmin) {
        New-NetFirewallRule -DisplayName "Deskflow-Fleet-24800" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 24800 -ErrorAction SilentlyContinue | Out-Null
        New-NetFirewallRule -DisplayName "Deskflow-Fleet-24800-UDP" -Direction Inbound -Action Allow -Protocol UDP -LocalPort 24800 -ErrorAction SilentlyContinue | Out-Null
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
    $hubProc = Start-Process -FilePath $LauncherPy -ArgumentList "-m", "deskflow_fleet.hub", "--host", "0.0.0.0", "--port", "24808" -WindowStyle Hidden -PassThru
    Set-Content -Path $hubPidFile -Value $hubProc.Id -Encoding ASCII
}

# 7. Start Agent
$agentPidFile = Join-Path $DataDir "agent.pid"
Stop-ByPidFile $agentPidFile
$agentArgs = @("-m", "deskflow_fleet.agent", "--hub-url", $HubUrl)
if ($ComputerName) { $agentArgs += @("--computer-name", $ComputerName) }
$agentProc = Start-Process -FilePath $LauncherPy -ArgumentList $agentArgs -WindowStyle Hidden -PassThru
Set-Content -Path $agentPidFile -Value $agentProc.Id -Encoding ASCII

# 8. Register per-user logon startup
if (-not $NoStartup) {
    $runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    if (-not (Test-Path $runKey)) { New-Item -Path $runKey -Force | Out-Null }
    Set-ItemProperty -Path $runKey -Name "DeskflowFleetAgent" -Value "`"$LauncherPy`" -m deskflow_fleet.agent --hub-url `"$HubUrl`""
    if ($Hub) {
        Set-ItemProperty -Path $runKey -Name "DeskflowFleetHub" -Value "`"$LauncherPy`" -m deskflow_fleet.hub --host 0.0.0.0 --port 24808"
    }
}

Write-Host "Deskflow Fleet installed and running! Hub URL: $HubUrl"
