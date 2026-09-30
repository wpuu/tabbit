# diag-tabbit.ps1 —— 只诊断不动手：看 Tabbit 进程是否带着调试参数、端口有没有在监听
Write-Host "== Tabbit 进程及其命令行 =="
Get-CimInstance Win32_Process -Filter "Name like 'Tabbit%'" | Where-Object { $_.CommandLine -notmatch "--type=" } | Select-Object ProcessId, CommandLine | Format-List
Write-Host "== 正在监听的 92xx 端口 =="
netstat -ano | findstr LISTENING | findstr ":92"
Write-Host "== /json/version =="
try { (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:9223/json/version" -TimeoutSec 2).Content } catch { Write-Host "9223 连不上：$($_.Exception.Message)" -ForegroundColor Yellow }
