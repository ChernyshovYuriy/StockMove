#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CLI=(python -m market_info_layer.cli)

SEC_ROUTINE_LIMIT=500
PROCESS_FORM_TYPE="8-K"
PROCESS_LIMIT=50
LOOKBACK_DAYS=730
PROCESSED_OUTPUT_NAME="processed-today-clean-text"
BACKFILL_OUTPUT_NAME="backfill-review-clean-text"
EXPORT_DIR=""
INCLUDE_RAW_DOCUMENTS=0
SKIP_MACRO=0
SKIP_HALTS=0
SKIP_PRICES=0
DRY_RUN=0

usage() {
  cat <<'USAGE'
Run the Market Info Layer end-to-end verification workflow.

Usage:
  scripts/run_system_verification.sh [options]

Default workflow:
  init-db -> load-watchlist -> collect-sec -> sec-routine -> optional collectors
  -> process-sec-filings -> daily-brief reports -> export-debug --include-db

Options:
  --sec-routine-limit N        SEC routine per-form processing limit (default: 500)
  --process-form-type TYPE     Form type for explicit processing step (default: 8-K)
  --process-limit N            Explicit processing limit (default: 50)
  --lookback-days N            Lookback report window (default: 730)
  --processed-output-name NAME Processed-today report output name
  --backfill-output-name NAME  Lookback report output name
  --export-dir DIR             Debug export directory (default: app export directory)
  --include-raw-documents      Include full raw filing document fields in debug export
  --skip-macro                 Skip collect-macro
  --skip-halts                 Skip collect-halts
  --skip-prices                Skip collect-prices
  --dry-run                    Print commands without executing them
  -h, --help                   Show this help

Examples:
  scripts/run_system_verification.sh
  scripts/run_system_verification.sh --process-form-type 4 --process-limit 25 --skip-prices
  scripts/run_system_verification.sh --export-dir export/openai-debug --include-raw-documents
USAGE
}

option_value() {
  local option="$1"
  if [[ $# -lt 2 || "$2" == --* ]]; then
    echo "${option} requires a value" >&2
    exit 2
  fi
  printf '%s' "$2"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sec-routine-limit) SEC_ROUTINE_LIMIT="$(option_value "$1" "${2-}")"; shift 2 ;;
    --process-form-type) PROCESS_FORM_TYPE="$(option_value "$1" "${2-}")"; shift 2 ;;
    --process-limit) PROCESS_LIMIT="$(option_value "$1" "${2-}")"; shift 2 ;;
    --lookback-days) LOOKBACK_DAYS="$(option_value "$1" "${2-}")"; shift 2 ;;
    --processed-output-name) PROCESSED_OUTPUT_NAME="$(option_value "$1" "${2-}")"; shift 2 ;;
    --backfill-output-name) BACKFILL_OUTPUT_NAME="$(option_value "$1" "${2-}")"; shift 2 ;;
    --export-dir) EXPORT_DIR="$(option_value "$1" "${2-}")"; shift 2 ;;
    --include-raw-documents) INCLUDE_RAW_DOCUMENTS=1; shift ;;
    --skip-macro) SKIP_MACRO=1; shift ;;
    --skip-halts) SKIP_HALTS=1; shift ;;
    --skip-prices) SKIP_PRICES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

require_integer() {
  local name="$1"
  local value="$2"
  if ! [[ "$value" =~ ^[0-9]+$ ]]; then
    echo "${name} must be a non-negative integer, got: ${value}" >&2
    exit 2
  fi
}

require_integer "--sec-routine-limit" "$SEC_ROUTINE_LIMIT"
require_integer "--process-limit" "$PROCESS_LIMIT"
require_integer "--lookback-days" "$LOOKBACK_DAYS"

log() {
  printf '[%(%Y-%m-%dT%H:%M:%SZ)T] %s\n' -1 "$*"
}

quote_command() {
  printf '%q ' "$@"
}

run_step() {
  local title="$1"
  shift
  STEP_INDEX=$((STEP_INDEX + 1))
  log "(${STEP_INDEX}/${TOTAL_STEPS}) START ${title}"
  printf '  $ '
  quote_command "$@"
  printf '\n'

  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "(${STEP_INDEX}/${TOTAL_STEPS}) DRY-RUN ${title}"
    return 0
  fi

  local start elapsed
  start=$SECONDS
  "$@"
  elapsed=$((SECONDS - start))
  log "(${STEP_INDEX}/${TOTAL_STEPS}) DONE ${title} (${elapsed}s)"
}

on_error() {
  local exit_code=$?
  log "FAILED at step ${STEP_INDEX}/${TOTAL_STEPS} with exit code ${exit_code}"
  exit "$exit_code"
}
trap on_error ERR

cd "$PROJECT_DIR"

TOTAL_STEPS=0
TOTAL_STEPS=$((TOTAL_STEPS + 1)) # init-db
TOTAL_STEPS=$((TOTAL_STEPS + 1)) # load-watchlist
TOTAL_STEPS=$((TOTAL_STEPS + 1)) # collect-sec
TOTAL_STEPS=$((TOTAL_STEPS + 1)) # sec-routine
[[ "$SKIP_MACRO" -eq 1 ]] || TOTAL_STEPS=$((TOTAL_STEPS + 1))
[[ "$SKIP_HALTS" -eq 1 ]] || TOTAL_STEPS=$((TOTAL_STEPS + 1))
[[ "$SKIP_PRICES" -eq 1 ]] || TOTAL_STEPS=$((TOTAL_STEPS + 1))
TOTAL_STEPS=$((TOTAL_STEPS + 1)) # process-sec-filings
TOTAL_STEPS=$((TOTAL_STEPS + 1)) # processed-today daily-brief
TOTAL_STEPS=$((TOTAL_STEPS + 1)) # lookback daily-brief
TOTAL_STEPS=$((TOTAL_STEPS + 1)) # export-debug
STEP_INDEX=0

log "Market Info Layer verification workflow starting in ${PROJECT_DIR}"
log "Progress will be printed before and after each command. The final command prints the debug ZIP path."

run_step "Initialize database" "${CLI[@]}" init-db
run_step "Load watchlist" "${CLI[@]}" load-watchlist
run_step "Collect SEC metadata" "${CLI[@]}" collect-sec
run_step "Run SEC routine" "${CLI[@]}" sec-routine --limit-per-form "$SEC_ROUTINE_LIMIT"

if [[ "$SKIP_MACRO" -ne 1 ]]; then
  run_step "Collect macro observations" "${CLI[@]}" collect-macro
fi
if [[ "$SKIP_HALTS" -ne 1 ]]; then
  run_step "Collect trading halts" "${CLI[@]}" collect-halts
fi
if [[ "$SKIP_PRICES" -ne 1 ]]; then
  run_step "Collect prices" "${CLI[@]}" collect-prices
fi

run_step "Process SEC filings" "${CLI[@]}" process-sec-filings --form-type "$PROCESS_FORM_TYPE" --limit "$PROCESS_LIMIT"
run_step "Generate processed-today daily brief" \
  "${CLI[@]}" daily-brief --processed-today --include-low --output-name "$PROCESSED_OUTPUT_NAME"
run_step "Generate lookback daily brief" \
  "${CLI[@]}" daily-brief --lookback-days "$LOOKBACK_DAYS" --include-low --output-name "$BACKFILL_OUTPUT_NAME"

EXPORT_COMMAND=("${CLI[@]}" export-debug --include-db)
if [[ -n "$EXPORT_DIR" ]]; then
  EXPORT_COMMAND+=(--output-dir "$EXPORT_DIR")
fi
if [[ "$INCLUDE_RAW_DOCUMENTS" -eq 1 ]]; then
  EXPORT_COMMAND+=(--include-raw-documents)
fi
run_step "Create debug export ZIP" "${EXPORT_COMMAND[@]}"

log "Verification workflow complete. Supply the ZIP path printed by export-debug to OpenAI for analysis."
