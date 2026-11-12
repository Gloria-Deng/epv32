#!/usr/bin/env bash
# results/pnr/bram_template_arms/run_all.sh [timeout_per_arm]
# 顺序驱动：一臂一作业、各自限时，挂死一臂不拖累其它；各臂日志落各臂旁，驱动 stdout 由调用方存 run_all.log。
# 臂顺序刻意：先跑便宜/信息量大的。
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
TMO="${1:-900}"
for arm in ram_bram_32k ram_bram_256k ram_bram_256k_nobe ram_async_32k ram_async_256k; do
  echo "=== DRIVER-ARM-START $arm $(date -u +%FT%TZ) ==="
  bash "$HERE/run_arm.sh" "$arm" "$TMO"
  echo "=== DRIVER-ARM-END   $arm $(date -u +%FT%TZ) ==="
done
echo "DRIVER_ALL_DONE $(date -u +%FT%TZ)"
