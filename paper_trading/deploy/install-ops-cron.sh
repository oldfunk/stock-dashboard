#!/usr/bin/env bash
# M4 运维自动化：每日账本备份 + 巡检（只读）安装/检查。
#
# 用法：
#   bash deploy/install-ops-cron.sh --check
#   bash deploy/install-ops-cron.sh --install
#   bash deploy/install-ops-cron.sh --install /home/nec/paper-trading
#
# 只增删带 `pt-ops-` 标记的两行；不碰其它 cron。备份保留 20 份（约 20 天）。
set -euo pipefail

MODE="${1:---check}"
ROOT="${2:-/home/nec/paper-trading}"
MARK_BACKUP="pt-ops-backup"
MARK_CHECK="pt-ops-check"
LINE_BACKUP="10 21 * * * cd ${ROOT} && mkdir -p backups logs && venv/bin/python -m paper_trading.cli ledger backup --out-dir backups --keep 20 --json >> logs/backup.log 2>&1 # ${MARK_BACKUP}"
LINE_CHECK="20 21 * * * cd ${ROOT} && venv/bin/python -m paper_trading.cli ops check --json >> logs/ops.log 2>&1 # ${MARK_CHECK}"

show() {
  echo "root: ${ROOT}"
  crontab -l 2>/dev/null | grep -F "${MARK_BACKUP}" || echo "  [missing] backup"
  crontab -l 2>/dev/null | grep -F "${MARK_CHECK}" || echo "  [missing] ops check"
}

case "${MODE}" in
  --check)
    rc=0
    crontab -l 2>/dev/null | grep -qF "${MARK_BACKUP}" || rc=1
    crontab -l 2>/dev/null | grep -qF "${MARK_CHECK}" || rc=1
    show
    exit "${rc}"
    ;;
  --install)
    test -d "${ROOT}" || { echo "no such dir: ${ROOT}" >&2; exit 2; }
    mkdir -p "${ROOT}/backups" "${ROOT}/logs"
    tmp="$(mktemp)"
    crontab -l 2>/dev/null | grep -vF "${MARK_BACKUP}" | grep -vF "${MARK_CHECK}" > "${tmp}" || true
    {
      echo "${LINE_BACKUP}"
      echo "${LINE_CHECK}"
    } >> "${tmp}"
    crontab "${tmp}"
    rm -f "${tmp}"
    show
    ;;
  *)
    echo "usage: $0 [--check|--install] [root]" >&2
    exit 2
    ;;
esac
