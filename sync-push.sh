#!/usr/bin/env bash
# sync-push.sh — 把工作区（本文件所在目录）同步到 GitHub 仓库并推送。给沙箱里的 AI 用；用户本机请用 push-to-github.ps1。
#
#   ./sync-push.sh "提交说明"        工作区 → 仓库（工作区版本优先），提交并推送
#   ./sync-push.sh --pull            仓库 → 工作区（把远端最新文件拷回来；用户在本机改了文件并推送后先跑这个）
#   ./sync-push.sh --diff            只看两边差异，不动任何东西
#
# 凭据：环境变量 GITHUB_TOKEN，或 .secrets/github_token.txt（已在 .gitignore 里，永远不会被提交）。
# 每次都重新 clone 到 /tmp（沙箱的 /tmp 不跨轮保留，所以工作区里不放 .git）。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_URL="${REPO_URL:-https://github.com/wpuu/tabbit.git}"
BRANCH="${BRANCH:-main}"
TOKEN="${GITHUB_TOKEN:-}"
[ -z "$TOKEN" ] && [ -f "$ROOT/.secrets/github_token.txt" ] && TOKEN="$(tr -d '\r\n' < "$ROOT/.secrets/github_token.txt")"
[ -z "$TOKEN" ] && { echo "没有 token：设 GITHUB_TOKEN 或写入 .secrets/github_token.txt"; exit 1; }
AUTH_URL="${REPO_URL/https:\/\//https://x-access-token:${TOKEN}@}"
WORK="$(mktemp -d /tmp/sync-push.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

mode="push"; msg=""
case "${1:-}" in
  --pull) mode="pull" ;;
  --diff) mode="diff" ;;
  *) msg="${1:-}" ;;
esac

echo "· clone $REPO_URL ($BRANCH)"
if ! git clone -q --branch "$BRANCH" "$AUTH_URL" "$WORK/repo" 2>/dev/null; then
  git init -q -b "$BRANCH" "$WORK/repo"          # 远端还是空仓库
  git -C "$WORK/repo" remote add origin "$AUTH_URL"
fi
git -C "$WORK/repo" config user.name  "wpuu"
git -C "$WORK/repo" config user.email "wpuu@users.noreply.github.com"

# 同步时永远排除：.git、凭据；其余交给仓库里的 .gitignore
RSYNC_EX=(--exclude .git --exclude .secrets --exclude uploads --exclude 'Unselected files' --exclude __pycache__ --exclude .config --exclude .sudo_as_admin_successful)

if [ "$mode" = "diff" ]; then
  rsync -rcn --delete --itemize-changes "${RSYNC_EX[@]}" "$ROOT/" "$WORK/repo/" | grep -v '^\.d' || echo "（两边一致）"
  exit 0
fi

if [ "$mode" = "pull" ]; then
  echo "· 仓库 → 工作区（只覆盖仓库里有的文件，不删工作区其它文件）"
  rsync -rc --itemize-changes --exclude .git "$WORK/repo/" "$ROOT/" | grep -v '^\.d' || true
  echo "完成"
  exit 0
fi

echo "· 工作区 → 仓库"
rsync -rc --delete "${RSYNC_EX[@]}" "$ROOT/" "$WORK/repo/"
cd "$WORK/repo"
git add -A
if git diff --cached --quiet; then
  echo "没有改动需要提交"; exit 0
fi
# 最后一道保险：绝不提交像 token 的东西
if git diff --cached | grep -Eq 'github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9]{20,}'; then
  echo "!! 暂存内容里有疑似密钥，已中止"; git diff --cached --stat; exit 2
fi
[ -z "$msg" ] && msg="update $(date '+%Y-%m-%d %H:%M')"
git commit -q -m "$msg"
git --no-pager show --stat --oneline HEAD | head -40
git push -q -u origin "$BRANCH"
echo "已推送 → ${REPO_URL%.git}/commit/$(git rev-parse --short HEAD)"
