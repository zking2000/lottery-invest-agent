# Lottery Invest Agent

[中文说明](./README.zh-CN.md)

This project is an OpenClaw-friendly Double Color Ball watcher that:

- bootstraps and caches historical draw data locally
- generates recommended picks before the sales cutoff window
- supports on-demand replies for `1 pick` or `5 picks`
- refreshes the latest draw after the official result is published
- compares issued picks against the draw and sends the result via iMessage

## Features

- Local-cache-first workflow that avoids refetching full history on every request
- Default recommendation size of `5 picks`, with optional explicit `1 pick` requests
- Both scheduled pushes and on-demand reply flows
- Automatic post-draw comparison within about one hour after draw time, plus the next issue's 5 picks and purchase deadline
- Public repo layout that excludes personal phone numbers, local cache, and runtime state

## Project Layout

- `scripts/ssq_agent.py`: main entry point
- `config.json`: local private config
- `config.example.json`: public template config
- `state/runtime.json`: local runtime state
- `data/history_ssq.json`: local draw cache
- `skills/ssq-watcher/SKILL.md`: OpenClaw skill notes
- `tests/test_ssq_agent.py`: focused unit tests

## Quick Start

1. Copy `config.example.json` to `config.json` and fill in `notification.target`.
2. Run `bootstrap-history` once to save the historical draws locally.
3. Use `snapshot` or `reply` to generate picks.
4. Use `install-cron` to install the push and refresh jobs into OpenClaw.

```bash
python3 ./scripts/ssq_agent.py bootstrap-history
python3 ./scripts/ssq_agent.py snapshot
python3 ./scripts/ssq_agent.py reply --request "give me 5 picks for this issue"
python3 ./scripts/ssq_agent.py install-cron --agent-id ssq-watcher
```

## Commands

```bash
python3 ./scripts/ssq_agent.py bootstrap-history
python3 ./scripts/ssq_agent.py refresh-latest
python3 ./scripts/ssq_agent.py compare-latest
python3 ./scripts/ssq_agent.py snapshot
python3 ./scripts/ssq_agent.py snapshot --count 5
python3 ./scripts/ssq_agent.py reply --request "give me 1 pick"
python3 ./scripts/ssq_agent.py reply --request "give me 5 picks"
python3 ./scripts/ssq_agent.py run-once --send --dry-run
python3 ./scripts/ssq_agent.py send-test --dry-run
python3 ./scripts/ssq_agent.py install-cron --agent-id ssq-watcher
python3 -m unittest tests/test_ssq_agent.py
```

## Data Flow

- `bootstrap-history`: fetches historical draws once into `data/history_ssq.json`
- `snapshot` / `reply` / `run-once`: read from local cache by default
- `refresh-latest`: updates the latest official draw, compares the issued picks, and sends the next issue's 5 picks
- `compare-latest`: compares the newest official result against issued picks

## Official Refresh Fallbacks

`refresh-latest` tries the following official sources in order:

1. China Welfare Lottery draw page HTML
2. The current data source referenced by that page
3. The legacy China Welfare Lottery draw API

If all of them fail, the existing local cache is preserved instead of falling back to a full-history fetch.

## Default Schedule

- Recommendation push: `18:30` on Tuesday, Thursday, and Sunday in `Asia/Shanghai`
- Result refresh and next-issue follow-up: `22:15` on Tuesday, Thursday, and Sunday in `Asia/Shanghai`

## Notes

- The default recommendation size is `5 picks`
- On-demand requests support messages such as "双色球", "来1组", "来5组", and "当期号码"
- Every issued recommendation is recorded into `state/runtime.json` by issue
- Post-draw comparison messages include winning numbers, prize breakdown, highest prize, and estimated fixed-prize total
- The post-draw flow also sends the next issue's 5 picks and the purchase deadline
- If the local watcher fails, the correct behavior is to say the current recommendation is unavailable instead of inventing numbers
- This is an entertainment reference tool and does not promise winning probability or returns
