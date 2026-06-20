#!/usr/bin/env bash
# 3.10 L3 Preflight — 重启后自检
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && source .env && set +a

fail=0

echo "=== 1. PDF ==="
test -f papers/patchcore.pdf && echo "  ✅ papers/patchcore.pdf ($(stat -c%s papers/patchcore.pdf) bytes)" || { echo "  ❌ missing"; fail=1; }

echo "=== 2. Repository ==="
if cd workspace/repos/patchcore-inspection 2>/dev/null; then
  sha=$(git rev-parse HEAD)
  if [ "$sha" = "fcaa92f124fb1ad74a7acf56726decd4b27cbcad" ]; then
    echo "  ✅ commit $sha"
  else
    echo "  ❌ wrong commit: $sha"
    fail=1
  fi
  cd "$OLDPWD"
else
  echo "  ❌ not cloned"
  fail=1
fi

echo "=== 3. MVTec Dataset ==="
if [ -n "${AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT:-}" ]; then
  ok=0
  for sub in bottle/train/good bottle/test bottle/ground_truth; do
    if [ -d "$AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT/$sub" ]; then
      echo "  ✅ $sub"
    else
      echo "  ❌ $sub missing"
      ok=1
    fi
  done
  [ $ok -eq 1 ] && fail=1
else
  echo "  ⏳ AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT not set"
  fail=1
fi

echo "=== 4. GPU ==="
if nvidia-smi 2>/dev/null > /dev/null; then
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1
  echo "  ✅ GPU OK"
else
  echo "  ❌ nvidia-smi failed"
  fail=1
fi

echo "=== 5. DeepSeek Provider ==="
echo "  DEEPSEEK_BASE_URL=${DEEPSEEK_BASE_URL:-not set}"
if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
  echo "  ✅ DEEPSEEK_API_KEY set (${#DEEPSEEK_API_KEY} chars)"
else
  echo "  ❌ DEEPSEEK_API_KEY not set"
  fail=1
fi

echo "=== 6. Verify ==="
bash scripts/verify.sh 2>&1 | tail -3

echo ""
if [ $fail -eq 0 ]; then
  echo "✅ All L3 preflight checks passed"
else
  echo "❌ Some checks failed — fix above and re-run"
fi
exit $fail
