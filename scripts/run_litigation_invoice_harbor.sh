#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/data1/rjj/fanno-work"
TASK_PATH="${PROJECT_ROOT}/build/tasks/legal.harvey-lab.litigation-invoice-review.v1/harbor"
JOBS_DIR="${PROJECT_ROOT}/jobs"

if [[ ! -f "${TASK_PATH}/task.toml" ]]; then
  printf '%s\n' \
    "Generated Harbor task is missing: ${TASK_PATH}" \
    "Build the canonical bundle and Harbor package before running this launcher." \
    "See docs/task-bundles.md for the source-of-truth build commands." >&2
  exit 5
fi

# Keep credentials outside the script and out of Git. Values already exported
# by the caller take precedence over entries in .env.
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
  existing_openai_key="${OPENAI_API_KEY:-}"
  existing_deepseek_key="${DEEPSEEK_API_KEY:-}"
  existing_model_key="${MODEL_API_KEY:-}"
  set -a
  # shellcheck disable=SC1091
  source "${PROJECT_ROOT}/.env"
  set +a
  [[ -n "${existing_openai_key}" ]] && OPENAI_API_KEY="${existing_openai_key}"
  [[ -n "${existing_deepseek_key}" ]] && DEEPSEEK_API_KEY="${existing_deepseek_key}"
  [[ -n "${existing_model_key}" ]] && MODEL_API_KEY="${existing_model_key}"
fi

DEEPSEEK_URL="${DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
RUNTIME_CONFIG_DIR="${PROJECT_ROOT}/.harbor"
NETWORK_OVERRIDE="${RUNTIME_CONFIG_DIR}/deepseek-host.override.yaml"

# Harbor's Codex adapter resolves credentials through OPENAI_API_KEY even when
# Codex itself is configured with a third-party Responses API provider.
if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then
    export OPENAI_API_KEY="${DEEPSEEK_API_KEY}"
  elif [[ -n "${MODEL_API_KEY:-}" ]]; then
    export OPENAI_API_KEY="${MODEL_API_KEY}"
  else
    printf '%s\n' \
      "Missing model credential." \
      "Export one of OPENAI_API_KEY, DEEPSEEK_API_KEY, or MODEL_API_KEY." >&2
    exit 2
  fi
fi

if [[ "${FANNO_SKIP_API_PREFLIGHT:-0}" != "1" ]]; then
  status="$({
    curl --silent --show-error \
      --connect-timeout 10 \
      --max-time 30 \
      --output /dev/null \
      --write-out '%{http_code}' \
      --header "Authorization: Bearer ${OPENAI_API_KEY}" \
      "${DEEPSEEK_URL%/}/models"
  } || true)"
  if [[ "${status}" != "200" ]]; then
    printf 'DeepSeek preflight failed: GET /models returned HTTP %s\n' \
      "${status:-unreachable}" >&2
    exit 3
  fi
fi

deepseek_ip="$({
  getent ahostsv4 api.deepseek.com \
    | awk '$1 ~ /^([0-9]{1,3}\.){3}[0-9]{1,3}$/ { print $1; exit }'
} || true)"
if [[ -z "${deepseek_ip}" ]]; then
  printf '%s\n' \
    "Unable to resolve api.deepseek.com on the host." \
    "The Harbor task container cannot be given a temporary host mapping." >&2
  exit 4
fi

mkdir -p "${JOBS_DIR}" "${RUNTIME_CONFIG_DIR}"
cat >"${NETWORK_OVERRIDE}" <<EOF
services:
  main:
    command:
      - bash
      - -lc
      - |
        printf '%s\\n' '${deepseek_ip} api.deepseek.com' >> /etc/hosts
        exec python -m fanno_legal.runtime.entrypoint
EOF

cd "${PROJECT_ROOT}"

exec harbor run \
  --path "${TASK_PATH}" \
  --agent codex \
  --model deepseek-flash \
  --agent-kwarg 'config={"model_provider":"deepseek","preferred_auth_method":"apikey","forced_login_method":"api","model_reasoning_effort":"high","web_search":"disabled","show_raw_agent_reasoning":true,"model_providers":{"deepseek":{"name":"deepseek","base_url":"https://api.deepseek.com/","env_key":"OPENAI_API_KEY","wire_api":"responses","requires_openai_auth":false,"supports_websockets":false}}}' \
  --allow-agent-host api.deepseek.com \
  --extra-docker-compose "${NETWORK_OVERRIDE}" \
  --jobs-dir "${JOBS_DIR}" \
  --n-concurrent 1 \
  --debug \
  "$@"
