# start-tabbit.ps1 —— 带调试端口启动 Tabbit（先彻底退出旧实例；默认配置打不开端口时自动改用独立配置目录）
# 用法：powershell -ExecutionPolicy Bypass -File .\start-tabbit.ps1
#      可选参数：-Port 9223  -Exe "C:\Program Files\Tabbit\Application\Tabbit Browser.exe"  -Profile "C:\tabbit-bridge"
#      不给 -Exe 时会自动在常见位置 / 开始菜单快捷方式里找 Tabbit Browser.exe
param(
  [string]$Exe     = "",
  [int]   $Port    = 9223,
  [string]$Profile = (Join-Path $env:LOCALAPPDATA "tabbit-bridge-profile")
)
$ErrorActionPreference = "SilentlyContinue"

function Find-Tabbit {
  $cands = @(
    "$env:ProgramFiles\Tabbit\Application\Tabbit Browser.exe",
    "${env:ProgramFiles(x86)}\Tabbit\Application\Tabbit Browser.exe",
    "$env:LOCALAPPDATA\Tabbit\Application\Tabbit Browser.exe",
    "$env:LOCALAPPDATA\Tabbit Browser\Application\Tabbit Browser.exe",
    "$env:LOCALAPPDATA\Programs\Tabbit\Tabbit Browser.exe",
    "D:\Program Files\Tabbit\Application\Tabbit Browser.exe"
  )
  foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return $c } }
  # 正在运行的 Tabbit 进程
  $p = Get-Process | Where-Object { $_.ProcessName -like "Tabbit*" -and $_.Path } | Select-Object -First 1
  if ($p) { return $p.Path }
  # 开始菜单 / 桌面快捷方式
  $sh = New-Object -ComObject WScript.Shell
  $dirs = @("$env:APPDATA\Microsoft\Windows\Start Menu\Programs", "$env:ProgramData\Microsoft\Windows\Start Menu\Programs", [Environment]::GetFolderPath("Desktop"))
  foreach ($d in $dirs) {
    foreach ($lnk in Get-ChildItem -Path $d -Recurse -Filter "*Tabbit*.lnk" -ErrorAction SilentlyContinue) {
      $t = $sh.CreateShortcut($lnk.FullName).TargetPath
      if ($t -and (Test-Path $t) -and $t -like "*.exe") { return $t }
    }
  }
  return $null
}
if (-not $Exe) { $Exe = Find-Tabbit }
if ($Exe) { Write-Host "Tabbit：$Exe" }

function Test-Port($p) {
  try { return ((Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$p/json/version" -TimeoutSec 2).StatusCode -eq 200) } catch { return $false }
}
function Stop-Tabbit {
  Get-Process | Where-Object { $_.ProcessName -like "Tabbit*" -or $_.Path -eq $Exe } | Stop-Process -Force
  Start-Sleep -Seconds 2
  $left = Get-Process | Where-Object { $_.ProcessName -like "Tabbit*" }
  if ($left) { Write-Host "   [!] 仍有 Tabbit 进程未退出：$($left.Id -join ', ')，再杀一次…" -ForegroundColor Yellow; $left | Stop-Process -Force; Start-Sleep -Seconds 2 }
}
function Wait-Port($p, $sec) { for ($i = 0; $i -lt $sec; $i++) { Start-Sleep -Seconds 1; if (Test-Port $p) { return $true } }; return $false }

if (-not (Test-Path $Exe)) { Write-Host "找不到 $Exe，请用 -Exe 指定 Tabbit Browser.exe 的路径" -ForegroundColor Red; exit 1 }

if (Test-Port $Port) { Write-Host "端口 $Port 已经是开着的，不用重启。" -ForegroundColor Green; exit 0 }

Write-Host "1) 退出所有 Tabbit 进程…"
Stop-Tabbit

Write-Host "2) 用【默认配置】+ --remote-debugging-port=$Port 启动…"
Start-Process -FilePath $Exe -ArgumentList "--remote-debugging-port=$Port"
$ok = Wait-Port $Port 15

if (-not $ok) {
  Write-Host "   默认配置下端口没打开（Chromium 136+ 会忽略对默认配置的调试参数）。" -ForegroundColor Yellow
  Write-Host "3) 改用独立配置目录 $Profile 重新启动（首次需在里面重新登录 Tabbit / Arena）…"
  Stop-Tabbit
  New-Item -ItemType Directory -Force -Path $Profile | Out-Null
  Start-Process -FilePath $Exe -ArgumentList "--remote-debugging-port=$Port", "--user-data-dir=`"$Profile`""
  $ok = Wait-Port $Port 15
}

if ($ok) {
  $v = (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/json/version").Content | ConvertFrom-Json
  Write-Host ""
  Write-Host "✅ 调试端口已就绪：$($v.Browser)" -ForegroundColor Green
  Write-Host "   验证地址：http://127.0.0.1:$Port/json/version"
  Write-Host "   下一步：  python bridge.py list --cdp http://127.0.0.1:$Port"
} else {
  Write-Host ""
  Write-Host "❌ 两种方式都没打开端口。请把下面的输出整段发给我：" -ForegroundColor Red
  Get-CimInstance Win32_Process -Filter "Name like 'Tabbit%'" | Select-Object ProcessId, CommandLine | Format-List
  netstat -ano | findstr LISTENING | findstr ":92"
}
