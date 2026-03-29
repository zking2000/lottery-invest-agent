#!/bin/zsh
set -euo pipefail

if [[ -z "${SSQ_AGENT_HOME:-}" ]]; then
  for candidate in "$HOME/clawd/lottery-invest-agent" "$HOME/Desktop/lottery-invest-agent"; do
    if [[ -e "$candidate/scripts/ssq_agent.py" ]]; then
      export SSQ_AGENT_HOME="$candidate"
      break
    fi
  done
fi

if [[ -z "${SSQ_AGENT_HOME:-}" ]]; then
  echo "SSQ_AGENT_HOME is not set and no project directory was found." >&2
  exit 1
fi

if [[ "$#" -eq 0 ]]; then
  set -- run-once --send
fi

exec /usr/bin/python3 "$SSQ_AGENT_HOME/scripts/ssq_agent.py" \
  --config "$SSQ_AGENT_HOME/config.json" \
  --state "$SSQ_AGENT_HOME/state/runtime.json" \
  "$@"
