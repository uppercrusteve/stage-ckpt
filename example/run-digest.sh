#!/usr/bin/env bash
# 最小可跑例子：三步的假阶段，第二步会失败一次，重跑就能从第二步续上。
#   bash example/run-digest.sh
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

export CKPT_ROOT="$(mktemp -d)"
OUT="$CKPT_ROOT/out"
mkdir -p "$OUT"
STAGE=demo

run_step() {
  local step=$1
  echo "  -> 干活: $step"
  case $step in
    one) printf 'a\n' > "$OUT/one.txt"; export CKPT_EVIDENCE="$OUT/one.txt" ;;
    two)
      # 用文件存在与否模拟「上一轮断在这里」
      if [ ! -f "$OUT/.two_tried" ]; then
        : > "$OUT/.two_tried"
        echo "  -> 断网，第二步失败"
        return 1
      fi
      printf 'b\n' > "$OUT/two.txt"; export CKPT_EVIDENCE="$OUT/two.txt" ;;
    three) printf 'c\n' > "$OUT/three.txt"; export CKPT_EVIDENCE="$OUT/three.txt" ;;
  esac
}

source ./ckpt.sh
ckpt_boot "$STAGE" one two three

echo "第一轮："
ckpt_run "$STAGE" run_step
echo "第一轮退出码 $?（预期 1，卡在 two）"

echo "第二轮："
ckpt_run "$STAGE" run_step
echo "第二轮退出码 $?（预期 0，从 two 续完）"

echo "账本："
python ckpt.py status "$STAGE" --root "$CKPT_ROOT"
rm -rf "$CKPT_ROOT"
