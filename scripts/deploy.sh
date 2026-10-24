#!/usr/bin/env bash
# 一键收尾部署：推 nightly 留痕 → 生产 worktree 门禁（失败即中止，main 不动）→ ff 推 main
# → 生产 git pull → 按改动重启服务 → 健康检查。
# 用法（本机仓库根目录；本机不跑 pytest，门禁在生产 worktree 用生产 venv 执行）：
#   bash scripts/deploy.sh              # 按改动文件自动判断是否重启（src/ config/ → 主服务；paper_trading/ →纸盘面板）
#   bash scripts/deploy.sh --restart    # 强制重启两个服务
#   bash scripts/deploy.sh --no-restart # 跳过重启（仍做健康检查）
# 部署目标从 deploy.local.md 的 KEY=VALUE 块读取（该文件不入库）。
# 执行环境：Windows 上须用 Git for Windows 的 bash（如 "C:\Program Files\Git\bin\bash.exe" scripts/deploy.sh）。
# 注意：PowerShell 里裸敲 bash 进的是 WSL——WSL 的 Linux git 无 GitHub 凭据，push 会卡死在隐形密码提示。
set -uo pipefail

cd "$(dirname "$0")/.."

# 所有 SSH 统一间隔 1 秒：连续建连会触发新版 OpenSSH 的 per-source penalty
#（2026-10-23 实弹症状：kex_exchange_identification: read: Connection reset by peer）。
ssh_do() { sleep 1; ssh "$@"; }

# ---- 读取部署目标 ----
if [ ! -f deploy.local.md ]; then
    echo "错误：缺少 deploy.local.md（部署目标本地配置，KEY=VALUE 块）"; exit 1
fi
DEPLOY_SSH=$(grep -E '^DEPLOY_SSH=' deploy.local.md | head -1 | cut -d= -f2- | tr -d ' \r')
DEPLOY_DIR=$(grep -E '^DEPLOY_DIR=' deploy.local.md | head -1 | cut -d= -f2- | tr -d ' \r')
VERIFY_DIR=$(grep -E '^VERIFY_DIR=' deploy.local.md | head -1 | cut -d= -f2- | tr -d ' \r')
SERVICE=$(grep -E '^SERVICE=' deploy.local.md | head -1 | cut -d= -f2- | tr -d ' \r')
PORT=$(grep -E '^PORT=' deploy.local.md | head -1 | cut -d= -f2- | tr -d ' \r')
PARENT_SERVICE=$(grep -E '^PARENT_SERVICE=' deploy.local.md | head -1 | cut -d= -f2- | tr -d ' \r')
PARENT_PORT=$(grep -E '^PARENT_PORT=' deploy.local.md | head -1 | cut -d= -f2- | tr -d ' \r')
for v in DEPLOY_SSH DEPLOY_DIR VERIFY_DIR SERVICE PORT; do
    if [ -z "${!v}" ]; then echo "错误：deploy.local.md 缺少 $v"; exit 1; fi
done
case "$VERIFY_DIR" in ""|"/"|"$DEPLOY_DIR") echo "错误：VERIFY_DIR 非法：$VERIFY_DIR"; exit 1;; esac

RESTART_MODE=auto
case "${1:-}" in
    "") ;;
    --restart) RESTART_MODE=force ;;
    --no-restart) RESTART_MODE=never ;;
    *) echo "未知参数：$1（--restart / --no-restart）"; exit 1 ;;
esac

# ---- 0. 前置检查 ----
if [ -n "$(git status --porcelain)" ]; then echo "错误：工作区不干净，先 commit 或 stash"; exit 1; fi
BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$BRANCH" = "main" ]; then echo "错误：当前在 main 上，迭代须在 nightly/* 分支"; exit 1; fi

git fetch origin
if ! git merge-base --is-ancestor origin/main HEAD; then
    echo "错误：当前分支未基于最新 origin/main，先 git fetch origin && git rebase origin/main"; exit 1
fi
AHEAD=$(git rev-list --count origin/main..HEAD)
BASE_MAIN=$(git rev-parse --short origin/main)
TIP=$(git rev-parse --short HEAD)
echo "=== deploy.sh ==="
echo "分支 $BRANCH @ $TIP；origin/main @ $BASE_MAIN；待合入 $AHEAD commit"

if [ "$AHEAD" -eq 0 ]; then
    echo "无新 commit，只做生产同步与健康检查"
fi

# 重启判定（在推 main 前计算，此刻 origin/main 仍是合并前状态）
NEED_MAIN=0; NEED_PARENT=0
if [ "$AHEAD" -gt 0 ]; then
    CHANGED=$(git diff --name-only origin/main..HEAD)
    if printf '%s\n' "$CHANGED" | grep -qE '^(src|config)/'; then NEED_MAIN=1; fi
    if printf '%s\n' "$CHANGED" | grep -qE '^paper_trading/'; then NEED_PARENT=1; fi
fi
if [ "$RESTART_MODE" = force ]; then NEED_MAIN=1; NEED_PARENT=1; fi
if [ "$RESTART_MODE" = never ]; then NEED_MAIN=0; NEED_PARENT=0; fi

# ---- 1. 推 nightly 留痕 ----
if [ "$AHEAD" -gt 0 ]; then
    echo "--- 推送留痕分支 $BRANCH ---"
    if ! git push origin "HEAD:refs/heads/$BRANCH"; then
        echo "错误：推送 $BRANCH 失败（若因 rebase 后非 ff，需人工处理；main 禁 force-push）"; exit 1
    fi
fi

# ---- 2. 生产 worktree 门禁（失败即中止：main 未动、服务未重启）----
if [ "$AHEAD" -gt 0 ]; then
    echo "--- 生产 worktree 门禁（detached @ $TIP）---"
    if ! ssh_do "$DEPLOY_SSH" "cd $DEPLOY_DIR && git fetch origin && git worktree prune"; then
        echo "错误：生产 fetch 失败"; exit 1
    fi
    if ssh_do "$DEPLOY_SSH" "test -e $VERIFY_DIR/.git" 2>/dev/null; then
        if ! ssh_do "$DEPLOY_SSH" "cd $VERIFY_DIR && git reset --hard -q && git clean -fdx -q && git checkout -q --detach origin/$BRANCH"; then
            echo "错误：更新验证 worktree 失败"; exit 1
        fi
    else
        if ! ssh_do "$DEPLOY_SSH" "cd $DEPLOY_DIR && git worktree remove --force $VERIFY_DIR 2>/dev/null; rm -rf $VERIFY_DIR && git worktree add --detach $VERIFY_DIR origin/$BRANCH"; then
            echo "错误：创建验证 worktree 失败"; exit 1
        fi
    fi
    GATE_OUT=$(ssh_do "$DEPLOY_SSH" "cd $VERIFY_DIR && PYTHON=$DEPLOY_DIR/.venv/bin/python bash scripts/gate.sh" 2>&1)
    GATE_RC=$?
    # 分层门禁：只取**母项目**通过数（不能抓第一个 "N passed"，那可能是 vendor 的 219）
    PASSED=$(printf '%s\n' "$GATE_OUT" | grep -oE '母项目 [0-9]+ passed' | head -1 | awk '{print $2}')
    if [ -z "$PASSED" ]; then
        PASSED=$(printf '%s\n' "$GATE_OUT" | grep -oE '[0-9]+ passed' | head -1 | awk '{print $1}')
    fi
    if [ $GATE_RC -ne 0 ]; then
        printf '%s\n' "$GATE_OUT" | tail -40
        echo "!!! 门禁失败（exit $GATE_RC）：中止——main 未动、生产未重启、留痕分支已推"
        exit 1
    fi
    printf '%s\n' "$GATE_OUT" | tail -3
    echo "门禁通过：母项目 ${PASSED:-?} passed（vendor 单独记录）"
fi

# ---- 3. ff 推 main ----
if [ "$AHEAD" -gt 0 ]; then
    echo "--- 合并 main（ff push HEAD:main）---"
    if ! git push origin "HEAD:main"; then
        echo "错误：推 main 失败（多半 origin/main 有新提交，fetch 后 rebase 重跑）"; exit 1
    fi
    git fetch origin
fi

# ---- 4. 生产同步 ----
echo "--- 生产同步 ---"
PROD_BRANCH=$(ssh_do "$DEPLOY_SSH" "cd $DEPLOY_DIR && git rev-parse --abbrev-ref HEAD")
if [ "$PROD_BRANCH" != "main" ]; then echo "错误：生产当前不在 main（$PROD_BRANCH），人工检查"; exit 1; fi
if ! ssh_do "$DEPLOY_SSH" "cd $DEPLOY_DIR && git pull --ff-only origin main && git log --oneline -1"; then
    echo "错误：生产 pull 失败"; exit 1
fi

# ---- 5. 按需重启 ----
if [ "$NEED_MAIN" -eq 1 ]; then
    echo "--- 重启 $SERVICE ---"
    if ! ssh_do "$DEPLOY_SSH" "sudo systemctl restart $SERVICE"; then echo "错误：重启 $SERVICE 失败"; exit 1; fi
fi
if [ "$NEED_PARENT" -eq 1 ] && [ -n "$PARENT_SERVICE" ]; then
    echo "--- 重启 $PARENT_SERVICE ---"
    if ! ssh_do "$DEPLOY_SSH" "sudo systemctl restart $PARENT_SERVICE"; then echo "错误：重启 $PARENT_SERVICE 失败"; exit 1; fi
fi
if [ "$NEED_MAIN" -eq 1 ] || [ "$NEED_PARENT" -eq 1 ]; then
    echo "重启后等 12s 再验（重启验证 >=10s 的历史教训）"; sleep 12
fi

# ---- 6. 健康检查（单次 SSH 聚合探测）----
# 2026-10-23 实弹教训：每轮 4 条 SSH 连续建连会触发 sshd 限流（表现为
# kex_exchange_identification: read: Connection reset by peer），健康检查误报失败。
# 改为每轮只开一条连接，所有 SSH 间隔 1 秒（ssh_do）。
echo "--- 健康检查 ---"
ok=0
for i in 1 2 3 4 5 6; do
    if [ -n "$PARENT_SERVICE" ] && [ -n "$PARENT_PORT" ]; then
        probe=$(ssh_do "$DEPLOY_SSH" "systemctl is-active $SERVICE; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:$PORT/; systemctl is-active $PARENT_SERVICE; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:$PARENT_PORT/") || probe=""
        main_state=$(printf '%s\n' "$probe" | sed -n '1p')
        main_http=$(printf '%s\n' "$probe" | sed -n '2p')
        parent_state=$(printf '%s\n' "$probe" | sed -n '3p')
        parent_http=$(printf '%s\n' "$probe" | sed -n '4p')
    else
        probe=$(ssh_do "$DEPLOY_SSH" "systemctl is-active $SERVICE; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:$PORT/") || probe=""
        main_state=$(printf '%s\n' "$probe" | sed -n '1p')
        main_http=$(printf '%s\n' "$probe" | sed -n '2p')
        parent_state=skip; parent_http=skip
    fi
    echo "[$i] $SERVICE=$main_state http:$main_http / $PARENT_SERVICE=$parent_state http:$parent_http"
    if [ "$main_state" = active ] && [ "$main_http" = 200 ] && { [ "$parent_state" = skip ] || { [ "$parent_state" = active ] && [ "$parent_http" = 200 ]; }; }; then
        ok=1; break
    fi
    sleep 5
done
if [ "$ok" -ne 1 ]; then echo "!!! 健康检查未通过：人工介入"; exit 1; fi

# ---- 7. 收尾提示 ----
echo ""
echo "=== 部署完成 ==="
echo "main: $(git rev-parse --short origin/main)；生产同步完成；服务健康"
if [ -n "${PASSED:-}" ]; then
    echo "基线提示：生产 worktree gate ${PASSED} passed。若高于 scripts/gate-baseline.txt 记录值，随手更新该文件随下批提交。"
fi
echo "提醒：handoff.md / iteration-log.md 按 AGENTS 规则收尾。"
