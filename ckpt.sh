#!/usr/bin/env bash
# 把 stage-ckpt 接进 gate.sh 的续跑壳。
#
# 用法（配合 lib/gate.sh）：
#   source lib/gate.sh; source forge/<D>/stage-ckpt/ckpt.sh
#   window_ok "$S" || exit 0
#   gate_check "$S" || exit 0
#   ckpt_boot "$S" fetch merge dedupe      # 有账就用旧账，没账才建
#   ckpt_run  "$S" run_step                # run_step <step> 干活，完成后设 CKPT_EVIDENCE
#
# 与 already_done 的区别：already_done 只认「整天做完」，断在中途要整晚重来；
# 这里断在哪步就从哪步续，且已完成的步要交证据，证据失真会被打回。
set -uo pipefail
CKPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CKPT_ARGS=()
[ -n "${CKPT_ROOT:-}" ] && CKPT_ARGS=(--root "$CKPT_ROOT")
ckpt_() { python "$CKPT_DIR/ckpt.py" "${CKPT_ARGS[@]}" "$@"; }

ckpt_boot() {
  local stage=$1; shift
  if ckpt_ next "$stage" >/dev/null 2>&1; then return 0; fi
  ckpt_ status "$stage" >/dev/null 2>&1 && return 0
  local args=()
  for s in "$@"; do args+=(--step "$s"); done
  ckpt_ plan "$stage" "${args[@]}"
}

ckpt_run() {
  local stage=$1 runner=$2 step rc
  while :; do
    step=$(ckpt_ next "$stage"); rc=$?
    case $rc in
      1) echo "[ckpt] $stage 已完成，跳过"; return 0 ;;
      0) ;;
      *) echo "[ckpt] $stage 账本不可用"; return 2 ;;
    esac
    echo "[ckpt] $stage/$step 开始"
    if ! "$runner" "$step"; then
      echo "[ckpt] $stage/$step 失败，账已留，下次从这步续"
      return 1
    fi
    if [ -z "${CKPT_EVIDENCE:-}" ]; then
      echo "[ckpt] $stage/$step 完成但没设 CKPT_EVIDENCE，记为未完成"
      return 1
    fi
    # shellcheck disable=SC2086
    ckpt_ done "$stage" "$step" --evidence $CKPT_EVIDENCE || return 2
    unset CKPT_EVIDENCE
  done
}
