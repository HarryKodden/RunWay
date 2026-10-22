#!/usr/bin/env bash
set -euo pipefail

# RunWay CLI runner (HTTP/API regression + optional ApacheBench)
# Requires: ab, curl, python3

HOST=""
PORT="8080"
SCHEME="http"
CONCURRENCY=50
REQUESTS=5000
TARGET_RPS=500
TARGET_USERS=100
OUTPUT_DIR="./results"
ENDPOINTS="/health,/config,/api/users/me"
SCENARIO_FILE=""
SCENARIO_USERS=20
SCENARIO_DURATION=30
SCENARIO_ITERATIONS=0
SCENARIO_ENVIRONMENT=""
SCENARIO_EXTRA_ENV_FILE=""
SCENARIO_STRICT=false
SCENARIO_ONLY=false

ORIGINAL_CMD=$(printf '%q ' "$0" "$@")
ORIGINAL_CMD="${ORIGINAL_CMD% }"

usage() {
  cat <<'EOF'
Usage: ./bin/run.sh [options]

Options:
  --host <host>              target host (FQDN, not an IP address or localhost)
  --port <port>              target port (default: 8080)
  --scheme <http|https>      target scheme (default: http)
  --concurrency <n>          HTTP concurrency (default: 50)
  --requests <n>             requests per endpoint (default: 5000)
  --target-rps <n>           target RPS goal (default: 500)
  --target-users <n>         target concurrent users goal (default: 100)
  --endpoints <csv>          comma-separated endpoints (default: /health,/config,/api/users/me)
  --output-dir <path>        result root dir (default: ./results)
  --scenario-file <path>     JSON scenario file for chained API workflows
  --scenario-users <n>       virtual users for scenario execution (default: 20)
  --scenario-duration <sec>  scenario test duration (default: 30)
  --scenario-iterations <n>  max scenario loops per user, 0 = unlimited
  --scenario-environment <n> selected scenario environment name
  --scenario-extra-env <path> JSON object of environment variable overrides
  --scenario-strict          exit non-zero if scenario steps fail (compliance mode)
  --scenario-only            skip ApacheBench probes; do not fail if /health is missing
  --regression               one pass through the scenario/suite: 1 user, 1 iteration, strict
  -h, --help                 show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --scheme) SCHEME="$2"; shift 2 ;;
    --concurrency) CONCURRENCY="$2"; shift 2 ;;
    --requests) REQUESTS="$2"; shift 2 ;;
    --target-rps) TARGET_RPS="$2"; shift 2 ;;
    --target-users) TARGET_USERS="$2"; shift 2 ;;
    --endpoints) ENDPOINTS="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --scenario-file) SCENARIO_FILE="$2"; shift 2 ;;
    --scenario-users) SCENARIO_USERS="$2"; shift 2 ;;
    --scenario-duration) SCENARIO_DURATION="$2"; shift 2 ;;
    --scenario-iterations) SCENARIO_ITERATIONS="$2"; shift 2 ;;
    --scenario-environment) SCENARIO_ENVIRONMENT="$2"; shift 2 ;;
    --scenario-extra-env) SCENARIO_EXTRA_ENV_FILE="$2"; shift 2 ;;
    --scenario-strict) SCENARIO_STRICT=true; shift 1 ;;
    --scenario-only) SCENARIO_ONLY=true; shift 1 ;;
    --regression)
      SCENARIO_ONLY=true
      SCENARIO_STRICT=true
      SCENARIO_USERS=1
      SCENARIO_ITERATIONS=1
      shift 1
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1"; usage; exit 1 ;;
  esac
done

for cmd in ab curl python3; do
  command -v "$cmd" >/dev/null || { echo "Missing dependency: $cmd"; exit 1; }
done

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT_DIR}${PYTHONPATH:+:$PYTHONPATH}"

forbidden_api_host() {
  local host
  host="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  host="${host#[}"
  host="${host%]}"
  case "$host" in
    localhost|127.0.0.1|::1|0.0.0.0|host.docker.internal|ip6-localhost|ip6-loopback|*.localhost)
      return 0
      ;;
  esac
  # Reject IPv4/IPv6 literals; only FQDNs are accepted.
  if [[ "$host" == *:* ]] || [[ "$host" =~ ^[0-9.]+$ ]] || [[ "$host" =~ ^0x[0-9a-f]+$ ]]; then
    return 0
  fi
  return 1
}

if [[ -n "$HOST" ]]; then
  if forbidden_api_host "$HOST"; then
    echo "API hosts must be an FQDN, not an IP address, localhost, or host.docker.internal" >&2
    exit 1
  fi
  BASE_URL="${SCHEME}://${HOST}:${PORT}"
else
  if [[ "$SCENARIO_ONLY" != "true" ]]; then
    echo "--host is required and must be an FQDN, not an IP address or localhost" >&2
    exit 1
  fi
  BASE_URL=""
fi
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RUN_DIR="${OUTPUT_DIR}/${TIMESTAMP}"
mkdir -p "$RUN_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'
log() { echo -e "${CYAN}[$(date +%H:%M:%S)]${RESET} $*"; }
ok() { echo -e "${GREEN}✔${RESET} $*"; }
warn() { echo -e "${YELLOW}!${RESET} $*"; }
err() { echo -e "${RED}x${RESET} $*"; }

if [[ -n "$BASE_URL" ]]; then
  log "Testing target ${BASE_URL}"
  if curl -sf --connect-timeout 3 --max-time 5 "${BASE_URL}/health" >/dev/null; then
    ok "Target reachable"
  else
    if [[ "$SCENARIO_ONLY" == "true" ]]; then
      warn "Cannot reach ${BASE_URL}/health; continuing in scenario-only mode"
    else
      err "Cannot reach ${BASE_URL}/health"
      exit 1
    fi
  fi
else
  log "No --host given; using the suite environment server URL"
fi

IFS=',' read -r -a endpoint_arr <<< "$ENDPOINTS"

http_json_items=()
health_rps=0
health_p99=0
cu_rps=0
cu_p99=0

run_ab() {
  local endpoint="$1"
  local safe_name
  safe_name=$(echo "$endpoint" | sed 's#^/##; s#[^a-zA-Z0-9]#_#g')
  [[ -z "$safe_name" ]] && safe_name="root"

  local url="${BASE_URL}${endpoint}"
  local outfile="${RUN_DIR}/${safe_name}.txt"

  log "HTTP AB test ${endpoint}"
  ab -n "$REQUESTS" -c "$CONCURRENCY" -q -e "${RUN_DIR}/${safe_name}_percentiles.csv" "$url" > "$outfile" 2>&1 || true

  local rps p50 p99 failed
  rps=$(grep "Requests per second" "$outfile" | awk '{print $4}' || echo 0)
  p50=$(grep "Time per request" "$outfile" | head -1 | awk '{print $4}' || echo 0)
  p99=$(grep "99%" "$outfile" | awk '{print $2}' || echo 0)
  failed=$(grep "Failed requests" "$outfile" | awk '{print $3}' || echo 0)

  [[ "$endpoint" == "/health" ]] && health_rps="$rps" && health_p99="$p99"

  http_json_items+=("{\"endpoint\":\"$endpoint\",\"rps\":$rps,\"p50_ms\":$p50,\"p99_ms\":$p99,\"failed\":$failed}")

  if [[ "$failed" == "0" ]]; then
    ok "${endpoint}: ${rps} req/s (p99=${p99}ms)"
  else
    warn "${endpoint}: failed requests=${failed}"
  fi
}

if [[ "$SCENARIO_ONLY" != "true" ]]; then
  for endpoint in "${endpoint_arr[@]}"; do
    run_ab "$endpoint"
  done

  log "Concurrent-user AB test (${TARGET_USERS} users over /health)"
  ab -n "$REQUESTS" -c "$TARGET_USERS" -q -e "${RUN_DIR}/concurrent_users_percentiles.csv" "${BASE_URL}/health" > "${RUN_DIR}/concurrent_users.txt" 2>&1 || true
  cu_rps=$(grep "Requests per second" "${RUN_DIR}/concurrent_users.txt" | awk '{print $4}' || echo 0)
  cu_p99=$(grep "99%" "${RUN_DIR}/concurrent_users.txt" | awk '{print $2}' || echo 0)
else
  log "Scenario-only mode: skipping ApacheBench probes"
fi

scenario_json="null"
scenario_exit=0
if [[ -n "$SCENARIO_FILE" ]]; then
  if [[ ! -f "$SCENARIO_FILE" ]]; then
    err "Scenario file not found: $SCENARIO_FILE"
    exit 1
  fi

  if [[ "$SCENARIO_STRICT" == "true" ]]; then
    log "Regression run using ${SCENARIO_FILE}"
  else
    log "Scenario load test using ${SCENARIO_FILE}"
  fi
  set +e
  strict_args=()
  if [[ "$SCENARIO_STRICT" == "true" ]]; then
    strict_args+=(--fail-fast --strict)
  fi
  python3 "$(dirname "$0")/../tools/scenario_runner.py" \
    --scenario-file "$SCENARIO_FILE" \
    ${BASE_URL:+--base-url "$BASE_URL"} \
    --users "$SCENARIO_USERS" \
    --duration "$SCENARIO_DURATION" \
    --iterations "$SCENARIO_ITERATIONS" \
    ${SCENARIO_ENVIRONMENT:+--environment "$SCENARIO_ENVIRONMENT"} \
    ${SCENARIO_EXTRA_ENV_FILE:+--extra-env-file "$SCENARIO_EXTRA_ENV_FILE"} \
    "${strict_args[@]}" \
    --output "${RUN_DIR}/scenario.json"
  scenario_rc=$?
  set -e

  if [[ -f "${RUN_DIR}/scenario.json" ]]; then
    scenario_json=$(cat "${RUN_DIR}/scenario.json")
    scenario_rps=$(python3 -c "import json; d=json.load(open('${RUN_DIR}/scenario.json')); print(round(d['totals']['rps'], 2))")
    scenario_success=$(python3 -c "import json; d=json.load(open('${RUN_DIR}/scenario.json')); print(round(d['totals']['success_rate'] * 100, 2))")
    if [[ "$scenario_rc" -eq 0 ]]; then
      ok "Scenario test complete: ${scenario_rps} req/s, success=${scenario_success}%"
    else
      err "Scenario test failed: ${scenario_rps} req/s, success=${scenario_success}%"
      scenario_exit="$scenario_rc"
    fi
  else
    warn "Scenario output not generated"
    scenario_exit=1
  fi
fi

summary_file="${RUN_DIR}/summary.json"
command_json=${ORIGINAL_CMD//\\/\\\\}
command_json=${command_json//\"/\\\"}
{
  echo "{"
  echo "  \"timestamp\": \"${TIMESTAMP}\"," 
  echo "  \"target\": \"${BASE_URL}\"," 
  echo "  \"command\": \"${command_json}\"," 
  echo "  \"targets\": {\"rps\": ${TARGET_RPS}, \"users\": ${TARGET_USERS}},"
  echo "  \"config\": {\"concurrency\": ${CONCURRENCY}, \"requests\": ${REQUESTS}},"
  echo "  \"headline\": {\"health_rps\": ${health_rps}, \"health_p99_ms\": ${health_p99}, \"concurrent_rps\": ${cu_rps}, \"concurrent_p99_ms\": ${cu_p99}},"
  echo "  \"http_tests\": [$( ((${#http_json_items[@]})) && { IFS=,; echo "${http_json_items[*]}"; } )],"
  echo "  \"scenario\": ${scenario_json}"
  echo "}"
} > "$summary_file"

ok "Summary written: ${summary_file}"

echo ""
echo "Run complete: ${RUN_DIR}"
if [[ "$SCENARIO_ONLY" == "true" ]]; then
  echo "Key metrics:"
  echo "- Scenario success: ${scenario_success:-n/a}%"
else
  echo "Key metrics:"
  echo "- /health RPS: ${health_rps}"
  echo "- /health p99: ${health_p99} ms"
  echo "- Concurrent RPS (${TARGET_USERS} users): ${cu_rps}"
fi

if [[ "$scenario_exit" -ne 0 && "$SCENARIO_STRICT" == "true" ]]; then
  exit "$scenario_exit"
fi
