#!/usr/bin/env bash
set -uo pipefail

# Sovereign Runner — armored wrapper for cron jobs.
# Usage:  scripts/sovereign_runner.sh <script_path> [args...]
# Output: logs/pulse.log with timestamped entries + data/status-<name>.json

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$PROJECT_ROOT/venv/bin/python3"
LOG_DIR="$PROJECT_ROOT/logs"
STATUS_DIR="$PROJECT_ROOT/data"

mkdir -p "$LOG_DIR" "$STATUS_DIR"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <script_path> [args...]"
  exit 1
fi

TARGET_SCRIPT="$1"
shift
TARGET_NAME="$(basename "$TARGET_SCRIPT" .py)"
LOG_FILE="$LOG_DIR/pulse.log"
STATUS_FILE="$STATUS_DIR/status-${TARGET_NAME}.json"
TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"

# Resolve script relative to project root if not absolute
if [[ "$TARGET_SCRIPT" != /* ]]; then
  TARGET_SCRIPT="$PROJECT_ROOT/$TARGET_SCRIPT"
fi

if [[ ! -f "$TARGET_SCRIPT" ]]; then
  echo "[$TIMESTAMP] FATAL: $TARGET_SCRIPT not found" | tee -a "$LOG_FILE"
  exit 1
fi

PYTHON="$VENV_PYTHON"
if [[ ! -f "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

# Init log entry
echo "" >> "$LOG_FILE"
echo "=== [$TIMESTAMP] SOVEREIGN RUNNER: $TARGET_NAME ===" >> "$LOG_FILE"
echo "  Project: $PROJECT_ROOT" >> "$LOG_FILE"
echo "  Script:  $TARGET_SCRIPT" >> "$LOG_FILE"
echo "  Args:    $*" >> "$LOG_FILE"
echo "  Python:  $PYTHON" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

START_TS="$(date '+%s')"
START_ISO="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# Run script, capture everything
OUTPUT=$("$PYTHON" "$TARGET_SCRIPT" "$@" 2>&1)
EXIT_CODE=$?

END_TS="$(date '+%s')"
DURATION=$((END_TS - START_TS))
END_ISO="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# Write output to log
echo "$OUTPUT" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
echo "--- [$TIMESTAMP] EXIT CODE: $EXIT_CODE (${DURATION}s) ---" >> "$LOG_FILE"

# Write status JSON
cat > "$STATUS_FILE" <<JSONEOF
{
  "name": "$TARGET_NAME",
  "success": $([ "$EXIT_CODE" == "0" ] && echo "true" || echo "false"),
  "exit_code": $EXIT_CODE,
  "duration_sec": $DURATION,
  "started_at": "$START_ISO",
  "finished_at": "$END_ISO",
  "script": "$(basename "$TARGET_SCRIPT")"
}
JSONEOF

# Emit to stdout
echo "[SOVEREIGN] $TARGET_NAME → exit $EXIT_CODE, ${DURATION}s"
echo "$OUTPUT" | head -5
echo "[SOVEREIGN] Full log: $LOG_FILE"
exit "$EXIT_CODE"
