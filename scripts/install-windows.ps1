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

function Stop-ModuleProcesses([string]$ModulePattern, [string]$PidFile) {
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$ModulePattern*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  if (Test-Path $PidFile) {
    $oldPid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if ($oldPid -match '^\d+$') {
      Stop-Process -Id ([int]$oldPid) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
  }
}

function Start-DetachedService([string]$TaskName, [string]$Exe, [string[]]$ArgList, [bool]$AtLogon, [string]$RegValueName) {
  $argString = ($ArgList | ForEach-Object { if ($_ -match '\s') { "`"$_`"" } else { $_ } }) -join " "
  $runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
  if (Test-Path $runKey) {
    Remove-ItemProperty -Path $runKey -Name $RegValueName -ErrorAction SilentlyContinue
  }
  try {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $user = "$env:USERDOMAIN\$env:USERNAME"
    $action = New-ScheduledTaskAction -Execute $Exe -Argument $argString -WorkingDirectory $DataDir
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
    if ($AtLogon) {
      $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
      Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    } else {
      Register-ScheduledTask -TaskName $TaskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
    }
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Milliseconds 400
    if (-not $AtLogon) {
      Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    return
  } catch {
    $proc = Start-Process -FilePath $Exe -ArgumentList $ArgList -WindowStyle Hidden -PassThru
    if ($AtLogon) {
      if (-not (Test-Path $runKey)) { New-Item -Path $runKey -Force | Out-Null }
      Set-ItemProperty -Path $runKey -Name $RegValueName -Value "`"$Exe`" $argString"
    }
    return $proc.Id
  }
}

# 6. Start Hub if requested
$hubPidFile = Join-Path $DataDir "hub.pid"
Stop-ModuleProcesses "deskflow_fleet.hub" $hubPidFile
if ($Hub) {
  $null = Start-DetachedService "DeskflowFleetHub" $LauncherPy @("-m", "deskflow_fleet.hub", "--host", "0.0.0.0", "--port", "24808") (-not $NoStartup) "DeskflowFleetHub"
  $hp = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*deskflow_fleet.hub*" } | Select-Object -First 1
  if ($hp) { Set-Content -Path $hubPidFile -Value $hp.ProcessId -Encoding ASCII }
}

# 7. Start Agent
$agentPidFile = Join-Path $DataDir "agent.pid"
Stop-ModuleProcesses "deskflow_fleet.agent" $agentPidFile
$agentArgs = @("-m", "deskflow_fleet.agent", "--hub-url", $HubUrl.TrimEnd("/"))
if ($ComputerName) { $agentArgs += @("--computer-name", $ComputerName) }
$null = Start-DetachedService "DeskflowFleetAgent" $LauncherPy $agentArgs (-not $NoStartup) "DeskflowFleetAgent"
$ap = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*deskflow_fleet.agent*" } | Select-Object -First 1
if ($ap) { Set-Content -Path $agentPidFile -Value $ap.ProcessId -Encoding ASCII }

Write-Host "Deskflow Fleet installed and running! Hub URL: $HubUrl"
