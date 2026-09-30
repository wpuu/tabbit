<#
push-to-github.ps1 — 把这个文件夹提交并推送到 https://github.com/wpuu/tabbit
用法（在本文件所在目录）：
    .\push-to-github.ps1                       # 提交所有改动并推送
    .\push-to-github.ps1 -Message "说明文字"   # 自定义提交说明
    .\push-to-github.ps1 -Remote https://github.com/<你>/<仓库>.git   # 换仓库
首次运行会 git init，并在推送时弹出浏览器登录 GitHub（Git for Windows 自带的凭据管理器）。
之后每次运行 = git add . + git commit + git push。
#>
param(
  [string]$Message = "",
  [string]$Remote = "https://github.com/wpuu/tabbit.git",
  [string]$Branch = "main"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Write-Host "没有安装 git。请先装 Git for Windows：https://git-scm.com/download/win" -ForegroundColor Red
  exit 1
}

if (-not (Test-Path ".git")) {
  git init -b $Branch | Out-Null
  Write-Host "已初始化 git 仓库（分支 $Branch）"
}
if (-not (git config user.email)) {
  git config user.email "wpuu@users.noreply.github.com"
  git config user.name  "wpuu"
  Write-Host "已设置默认提交者（可用 git config user.email 改）"
}

# 下载文件夹时 .gitignore 这类隐藏文件可能丢失——没有就补一份，免得把对话记录/抓取结果推上公开仓库
if (-not (Test-Path ".gitignore")) {
  @"
transcript*.jsonl
NEEDS_HUMAN.flag
human_inbox.txt
probe-*.json
*-done.html
*-working.html
arena-survey.html
dump.html
.env
*.local.json
uploads/
__pycache__/
*.pyc
.venv/
.pytest_cache/
node_modules/
dist/
arena-agent-bridge/
.config/
.sudo_as_admin_successful
Unselected files/
doctor.json
*.bundle
"@ | Set-Content -Encoding UTF8 ".gitignore"
  Write-Host "已补上 .gitignore"
}

$existing = git remote get-url origin 2>$null
if (-not $existing) {
  git remote add origin $Remote
} elseif ($existing -ne $Remote) {
  git remote set-url origin $Remote
}

git add -A
if (-not (git diff --cached --quiet)) {
  if (-not $Message) { $Message = "update " + (Get-Date -Format "yyyy-MM-dd HH:mm") }
  git commit -m $Message | Out-Null
  Write-Host "已提交：$Message"
} else {
  Write-Host "没有新的改动需要提交"
}

# 远端若已有内容（例如网页上建仓时勾了 README），先合并进来再推
git fetch origin $Branch 2>$null
if ($LASTEXITCODE -eq 0) {
  git merge --allow-unrelated-histories -m "merge remote $Branch" "origin/$Branch" 2>$null | Out-Null
}
git push -u origin $Branch
Write-Host "完成：$Remote" -ForegroundColor Green
