#!/usr/bin/env bash
# results/pnr/bram_template_arms/run_all.sh [timeout_per_arm]
# Sequential driver: one job per arm, each with its own wall-clock bound, so a
# hung arm cannot stop the others. Per-arm logs land next to each arm; the
# driver's own stdout is captured to results/pnr/bram_template_arms/run_all.log by the caller.
# Arm order is deliberate: the cheap/informative ones first.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
TMO="${1:-900}"
for arm in ram_bram_32k ram_bram_256k ram_bram_256k_nobe ram_async_32k ram_async_256k; do
  echo "=== DRIVER-ARM-START $arm $(date -u +%FT%TZ) ==="
  bash "$HERE/run_arm.sh" "$arm" "$TMO"
  echo "=== DRIVER-ARM-END   $arm $(date -u +%FT%TZ) ==="
done
echo "DRIVER_ALL_DONE $(date -u +%FT%TZ)"
