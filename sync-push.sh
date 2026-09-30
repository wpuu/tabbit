#!/usr/bin/env bash
# sync-push.sh — 把工作区（本文件所在目录）同步到 GitHub 仓库并推送。给沙箱里的 AI 用；用户本机请用 push-to-github.ps1。
#
#   bash sync-push.sh "提交说明"     工作区 → 仓库（工作区版本优先），提交并推送
#   bash sync-push.sh --pull         仓库 → 工作区（把远端最新文件拷回来；用户在本机改了文件并推送后先跑这个）
#   bash sync-push.sh --diff         只看两边差异，不动任何东西
#
# 凭据：环境变量 GITHUB_TOKEN，或 .secrets/github_token.txt（已在 .gitignore 里，永远不会被提交）。
# 每次都重新 clone 到 /tmp（沙箱的 /tmp 不跨轮保留，所以工作区里不放 .git）。只依赖 git + python3（不用 rsync）。
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

# 同步器（纯 python）：src → dst，跳过排除项；push 模式还会删掉 dst 里 src 没有的文件（.git 除外）
sync_py() {  # $1=src $2=dst $3=mode(push|pull|diff)
python3 - "$1" "$2" "$3" <<'PY'
import filecmp, os, shutil, sys
src, dst, mode = sys.argv[1:4]
EXCLUDE_DIRS = {".git", ".secrets", "uploads", "Unselected files", "__pycache__", ".config", "node_modules", ".venv", ".pytest_cache"}
EXCLUDE_FILES = {".sudo_as_admin_successful"}
def walk(root):
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in EXCLUDE_DIRS]
        for f in files:
            if f in EXCLUDE_FILES: continue
            full = os.path.join(d, f); out[os.path.relpath(full, root)] = full
    return out
s, t = walk(src), walk(dst)
changed = [r for r in sorted(s) if r not in t or not filecmp.cmp(s[r], t[r], shallow=False)]
removed = [r for r in sorted(t) if r not in s]
if mode == "diff":
    for r in changed: print(("  新增  " if r not in t else "  修改  ") + r)
    if mode == "diff":
        for r in removed: print("  仅远端 " + r)
    if not changed and not removed: print("  （两边一致）")
    sys.exit(0)
for r in changed:
    os.makedirs(os.path.dirname(os.path.join(dst, r)) or dst, exist_ok=True)
    shutil.copy2(s[r], os.path.join(dst, r)); print(("  新增  " if r not in t else "  更新  ") + r)
if mode == "push":
    for r in removed:
        os.remove(t[r]); print("  删除  " + r)
if not changed and not (mode == "push" and removed): print("  （没有文件变化）")
PY
}

case "$mode" in
  diff) sync_py "$ROOT" "$WORK/repo" diff; exit 0 ;;
  pull) echo "· 仓库 → 工作区（只覆盖仓库里有的文件）"; sync_py "$WORK/repo" "$ROOT" pull; echo "完成"; exit 0 ;;
esac

echo "· 工作区 → 仓库"
sync_py "$ROOT" "$WORK/repo" push
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
