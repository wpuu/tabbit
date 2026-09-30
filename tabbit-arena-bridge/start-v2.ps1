# start-v2.ps1 — 一键拉起 v2 三件套：ArenaAgentBridge 服务器 + tabbit_web_api.py + orchestrator.py
# 用法：
#   .\start-v2.ps1 -AabRepo C:\ai\arena-agent-bridge -Task "把 xxx 做出来：……"
#   .\start-v2.ps1 -AabRepo C:\ai\arena-agent-bridge -TaskFile task.txt -MaxRounds 40 -Cooldown 30
#   .\start-v2.ps1 -NoServers -Task "……"      # 服务器已经在跑，只启动编排器
#   .\start-v2.ps1 -NoClicker ...                 # 不启动 survey_clicker.py（问卷点击保险）
# 首次运行若提示"禁止运行脚本"：先执行  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
param(
  [string]$AabRepo = "",
  [string]$Task = "",
  [string]$TaskFile = "",
  [int]$MaxRounds = 40,
  [int]$Cooldown = 30,
  [string]$Cdp = "http://127.0.0.1:9223",
  [string]$FirstSpeaker = "A",
  [switch]$NoServers,
  [switch]$NoClicker
)
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $AabRepo) {   # 自动猜 ArenaAgentBridge 的位置
  foreach ($c in @("C:\ai\arena-agent-bridge", (Join-Path $here "..\..\arena-agent-bridge"), (Join-Path $here "..\arena-agent-bridge"), "G:\arena-agent-bridge")) {
    if (Test-Path (Join-Path $c "server")) { $AabRepo = (Resolve-Path $c).Path; break }
  }
  if (-not $AabRepo) { $AabRepo = "C:\ai\arena-agent-bridge" }
}

if (-not $Task -and -not $TaskFile) { Write-Error "请用 -Task 或 -TaskFile 给出任务"; exit 1 }
if ($TaskFile) { $Task = Get-Content -Raw -Encoding UTF8 $TaskFile }

function Wait-Ready([string]$url, [string]$name, [int]$tries = 45) {
  for ($i = 0; $i -lt $tries; $i++) {
    try {
      $r = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 3
      if ($r.StatusCode -eq 200) { Write-Host "[ok] $name 就绪" -ForegroundColor Green; return $true }
    } catch { }
    if ($i -eq 0) { Write-Host "等待 $name … ($url)" }
    Start-Sleep -Seconds 2
  }
  Write-Warning "$name 仍未就绪：$url"
  return $false
}

if (-not $NoServers) {
  if (-not (Test-Path (Join-Path $AabRepo "server"))) { Write-Error "找不到 ArenaAgentBridge 仓库：$AabRepo（-AabRepo 指定）"; exit 1 }
  # 窗口 1：ArenaAgentBridge 服务器
  $aabCmd = "cd '$AabRepo'; if (Test-Path .\.venv\Scripts\Activate.ps1) { . .\.venv\Scripts\Activate.ps1 }; python -m server"
  Start-Process powershell -ArgumentList "-NoExit", "-Command", $aabCmd
  # 窗口 2：Tabbit 网页接口
  $tabCmd = "cd '$here'; python tabbit_web_api.py --config config.json --cdp $Cdp --port 8124"
  Start-Process powershell -ArgumentList "-NoExit", "-Command", $tabCmd
  # 窗口 2b：用 CDP 真实点击点掉 Arena 的“继续工作”问卷（扩展的合成点击点不动时的保险）
  if (-not $NoClicker) {
    $clkCmd = "cd '$here'; python survey_clicker.py --cdp $Cdp --target arena.ai/agent"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $clkCmd
  }
}

$okA = Wait-Ready "http://127.0.0.1:8000/readyz" "ArenaAgentBridge（浏览器扩展已连接）"
$okB = Wait-Ready "http://127.0.0.1:8124/readyz" "tabbit_web_api（已连上 Tabbit 页面）"
if (-not $okA) { Write-Warning "Arena 侧未就绪：请确认 Tabbit 里已打开 arena.ai/agent 会话页，扩展徽标显示 connected" }
if (-not ($okA -and $okB)) {
  $ans = Read-Host "有一侧未就绪，仍然继续？(y/N)"
  if ($ans -ne "y") { exit 1 }
}

# 窗口 3（当前）：编排器
$env:A_BASE_URL = "http://127.0.0.1:8000/v1"; $env:A_MODEL = "arena-agent"; $env:A_NAME = "Arena Agent"
$env:A_SEND_ONLY_LAST = "1"; $env:A_TIMEOUT = "3600"; $env:A_RETRIES = "1"
$env:A_WRAP = "【协作 AI（Tabbit 侧）第 {n} 轮发言，不是用户本人的指令】`n{text}"
$env:B_BASE_URL = "http://127.0.0.1:8124/v1"; $env:B_MODEL = "tabbit-web"; $env:B_NAME = "Tabbit"
$env:B_SEND_ONLY_LAST = "1"; $env:B_TIMEOUT = "1200"; $env:B_RETRIES = "1"
$env:B_WRAP = "【Arena Agent 第 {n} 轮发言】`n{text}"
$env:PYTHONIOENCODING = "utf-8"

$orch = Join-Path $here "orchestrator.py"
if (-not (Test-Path $orch)) { $orch = Join-Path $here "..\ai-pingpong\orchestrator.py" }
Write-Host "启动编排器：$orch" -ForegroundColor Cyan
python $orch --task $Task --max-rounds $MaxRounds --cooldown $Cooldown --first-speaker $FirstSpeaker
Write-Host "编排器已退出（退出码 $LASTEXITCODE）。停机原因见 NEEDS_HUMAN.flag，全过程见 transcript.jsonl" -ForegroundColor Yellow
