#!/usr/bin/env bash
# Replacement for the old _private/sweep_models.sh — compares several
# embedding backbones against the coverage bench without keeping
# local model stashes under _private/.
#
# For each candidate:
#   1. fetch_embedding_model.py → /tmp/selena-sweep/<name>/
#   2. point intent.embedding_model_dir at it (via SELENA_INTENT_EMBEDDING_MODEL_DIR env, no config edit)
#   3. run the coverage bench inside the selena-core container
#   4. archive the result JSON under tests/experiments/results/sweep/<name>.json
#
# Usage:
#   bash tests/experiments/sweep_embedding_models.sh
#   bash tests/experiments/sweep_embedding_models.sh all-MiniLM-L12-v2 multilingual-e5-small
#
# Requires a running selena-core container.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SWEEP_DIR="${SWEEP_DIR:-/tmp/selena-sweep}"
RESULTS_DIR="${RESULTS_DIR:-$REPO/tests/experiments/results/sweep}"
mkdir -p "$SWEEP_DIR" "$RESULTS_DIR"

# Catalog: <short-name> <hf-repo-id> [onnx-subpath]
declare -A CATALOG=(
  [all-MiniLM-L6-v2]="sentence-transformers/all-MiniLM-L6-v2"
  [all-MiniLM-L12-v2]="sentence-transformers/all-MiniLM-L12-v2"
  [all-mpnet-base-v2]="sentence-transformers/all-mpnet-base-v2"
  [paraphrase-multilingual-MiniLM-L12-v2]="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
  [multilingual-e5-small]="intfloat/multilingual-e5-small"
  [multilingual-e5-base]="intfloat/multilingual-e5-base"
)

if [ $# -gt 0 ]; then
  CANDIDATES=("$@")
else
  CANDIDATES=("${!CATALOG[@]}")
fi

for name in "${CANDIDATES[@]}"; do
  repo="${CATALOG[$name]:-}"
  if [ -z "$repo" ]; then
    echo "unknown candidate: $name (known: ${!CATALOG[*]})" >&2
    continue
  fi
  dir="$SWEEP_DIR/$name"
  echo "=== $name ($repo) ==="
  python3 "$HERE/fetch_embedding_model.py" "$repo" "$dir"

  echo "  running coverage bench"
  sudo docker compose -f "$REPO/docker-compose.yml" exec -T \
      -e SELENA_INTENT_EMBEDDING_MODEL_DIR="$dir" \
      core python3 /opt/selena-core/tests/experiments/run_coverage_bench.py

  # The bench writes to tests/experiments/results/coverage_bench_results.json; snapshot it.
  SRC="$REPO/tests/experiments/results/coverage_bench_results.json"
  if [ -f "$SRC" ]; then
    cp "$SRC" "$RESULTS_DIR/$name.json"
    echo "  -> $RESULTS_DIR/$name.json"
  fi
done

echo "all candidates done. Compare with:"
echo "  python3 $REPO/tests/experiments/compare_rounds.py $RESULTS_DIR/<a>.json $RESULTS_DIR/<b>.json"
