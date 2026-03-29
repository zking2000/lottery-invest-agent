#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


DRAW_WEEKDAYS = {1, 3, 6}  # Tue, Thu, Sun in Python weekday numbering.
LOTTERY_ID = "1"
JSONP_PATTERN = re.compile(r"^[^(]+\((.*)\)\s*;?\s*$", re.S)
OFFICIAL_FIELD_PATTERNS = {
    "reds": re.compile(r'<div class="ssqRed-dom">\s*\[([0-9,\s]+)\]\s*</div>'),
    "blue": re.compile(r'<div class="ssqBlue-dom">\s*\[([0-9,\s]+)\]\s*</div>'),
    "issue": re.compile(r'<div class="ssqQh-dom">\s*([0-9]{7})\s*</div>'),
    "sales": re.compile(r'<div class="ssqSales-dom">\s*([0-9,]+)\s*</div>'),
    "pool": re.compile(r'<div class="ssqPool-dom">\s*([0-9,]+)\s*</div>'),
    "detail_link": re.compile(r'<div class="ssqXqLink-dom">\s*([^<]+)\s*</div>'),
}
OFFICIAL_JSON_ISSUE_PATTERN = re.compile(r"^\d{7}$")
FIXED_PRIZE_AMOUNTS = {
    "三等奖": 3000,
    "四等奖": 200,
    "五等奖": 10,
    "六等奖": 5,
}


def resolve_project_dir() -> Path:
    env_path = os.environ.get("SSQ_AGENT_HOME")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


PROJECT_DIR = resolve_project_dir()
DEFAULT_CONFIG_PATH = PROJECT_DIR / "config.json"
DEFAULT_STATE_PATH = PROJECT_DIR / "state" / "runtime.json"


def resolve_project_path(path_value: str | Path, *, base_dir: Path | None = None) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return (base_dir or PROJECT_DIR) / path


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def save_json_file(path: Path, payload: Any) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    return json.loads(path.read_text())


def deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = deepcopy(base)
        for key, value in override.items():
            merged[key] = deep_merge(merged.get(key), value)
        return merged
    return deepcopy(override)


def resolve_openclaw_bin() -> str:
    env_value = os.environ.get("OPENCLAW_BIN")
    if env_value:
        return env_value
    discovered = shutil.which("openclaw")
    if discovered:
        return discovered
    return "/opt/homebrew/bin/openclaw"


def build_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    parts: list[str] = []
    for raw in ["/opt/homebrew/bin", "/usr/local/bin", env.get("PATH", "")]:
        if not raw:
            continue
        for item in str(raw).split(":"):
            if item and item not in parts:
                parts.append(item)
    env["PATH"] = ":".join(parts)
    return env


def infer_default_target() -> str | None:
    candidates = [
        Path.home() / ".openclaw" / "openclaw.json",
        Path.home() / ".clawdbot" / "clawdbot.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text())
            allow_from = payload.get("channels", {}).get("imessage", {}).get("allowFrom", [])
            if allow_from:
                return str(allow_from[0])
        except Exception:
            continue
    return None


DEFAULT_CONFIG: dict[str, Any] = {
    "lottery": {
        "name": "双色球",
        "region": "陕西风采",
        "timezone": "Asia/Shanghai",
        "draw_weekdays": [1, 3, 6],
        "sales_close_time": "20:00",
        "draw_time": "21:15",
        "history_count": 180,
    },
    "fetch": {
        "endpoint": "https://jc.zhcw.com/port/client_json.php",
        "page_size": 100,
        "http_timeout_seconds": 20,
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
        "referer": "https://www.zhcw.com/kjxx/ssq/",
        "cache_path": "data/history_ssq.json",
        "official_latest_url": "https://cwlgovcn.com/ygkj/wqkjgg/ssq/",
        "official_api_url": "https://cwlball.com/ssg/result.php?cmode=resultlist",
        "official_legacy_url": "https://www.cwl.gov.cn/cwl_admin/kjxx/findDrawNotice?name=ssq",
        "official_timeout_seconds": 20,
    },
    "recommendation": {
        "count": 5,
        "candidate_pool_size": 2400,
        "candidate_shortlist_size": 12,
        "random_seed": None,
        "max_repeat_reds_with_last_draw": 2,
        "disclaimer": "基于历史分布、规则过滤和 LLM 排序生成，仅供娱乐参考，不构成收益承诺。",
    },
    "notification": {
        "enabled": True,
        "channel": "imessage",
        "target": infer_default_target(),
        "message_prefix": "陕西风采双色球推荐",
        "comparison_prefix": "陕西风采双色球开奖对比",
    },
    "llm": {
        "enabled": True,
        "provider": "yinli",
        "model": "claude-sonnet-4-6",
        "base_url": "",
        "api_key": "",
        "timeout_seconds": 120,
        "temperature": 0.2,
        "max_tokens": 900,
    },
    "cron": {
        "name": "ssq-watcher-window-push",
        "expression": "30 18 * * 0,2,4",
        "timezone": "Asia/Shanghai",
        "message": "请调用双色球 watcher：运行 `/path/to/lottery-invest-agent/run_ssq_agent.sh run-once --send`。若当前不在购买窗口或本窗口已推送，则简短说明原因；不要编造号码。",
        "refresh_name": "ssq-refresh-latest-result",
        "refresh_expression": "15 22 * * 0,2,4",
        "refresh_message": "请调用双色球 watcher：运行 `/path/to/lottery-invest-agent/run_ssq_agent.sh refresh-latest`，在开奖后约1小时刷新最新期开奖结果、自动对比上一期推荐是否中奖，并继续发送下一期5组号码与购买截止时间；若官网尚未更新则简短说明原因。",
    },
}


DEFAULT_STATE: dict[str, Any] = {
    "last_history_fetch_at": None,
    "last_official_refresh_at": None,
    "last_official_refresh_status": "",
    "sent_windows": [],
    "issued_recommendations": {},
    "comparison_reports": {},
    "last_analysis": {},
    "last_message_preview": "",
}


@dataclass
class DrawResult:
    issue: str
    open_date: date
    reds: list[int]
    blue: int
    sale_money: int | None
    prize_pool_money: int | None


@dataclass
class Candidate:
    reds: list[int]
    blue: int
    heuristic_score: float
    features: dict[str, Any]


def now_in_tz(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_hhmm(value: str) -> tuple[int, int]:
    hour_str, minute_str = value.strip().split(":")
    return int(hour_str), int(minute_str)


def parse_int(value: Any) -> int | None:
    if value in ("", None, "-"):
        return None
    try:
        return int(str(value).replace(",", ""))
    except ValueError:
        return None


def issue_to_int(issue: str) -> int:
    return int(issue)


def issue_year(issue: str) -> int:
    return int(issue[:4])


def issue_seq(issue: str) -> int:
    return int(issue[4:])


def format_issue(year_value: int, seq_value: int) -> str:
    return f"{year_value}{seq_value:03d}"


def next_draw_date_after(current: date) -> date:
    probe = current + timedelta(days=1)
    while probe.weekday() not in DRAW_WEEKDAYS:
        probe += timedelta(days=1)
    return probe


def draw_count_between(start_date: date, end_date: date) -> int:
    if end_date <= start_date:
        return 0
    count = 0
    probe = start_date + timedelta(days=1)
    while probe <= end_date:
        if probe.weekday() in DRAW_WEEKDAYS:
            count += 1
        probe += timedelta(days=1)
    return count


def increment_issue(issue: str, steps: int) -> str:
    if steps <= 0:
        return issue
    year_value = issue_year(issue)
    seq_value = issue_seq(issue)
    for _ in range(steps):
        seq_value += 1
        if seq_value > 999:
            year_value += 1
            seq_value = 1
    return format_issue(year_value, seq_value)


def normalize_ball(value: int) -> str:
    return f"{value:02d}"


def format_candidate_line(index: int, candidate: Candidate, reason: str | None = None) -> str:
    reds = " ".join(normalize_ball(number) for number in candidate.reds)
    line = f"{index}. 红球 {reds} | 蓝球 {normalize_ball(candidate.blue)}"
    if reason:
        return f"{line}\n   理由: {reason}"
    return line


def format_draw_numbers(draw: DrawResult) -> str:
    reds = " ".join(normalize_ball(number) for number in draw.reds)
    return f"红球 {reds} | 蓝球 {normalize_ball(draw.blue)}"


def purchase_deadline_for_target(target_date: date, config: dict[str, Any]) -> datetime:
    close_hour, close_minute = parse_hhmm(config["lottery"]["sales_close_time"])
    tz = ZoneInfo(str(config["lottery"]["timezone"]))
    return datetime.combine(target_date, time(close_hour, close_minute), tzinfo=tz)


def format_purchase_deadline(target_date: date, config: dict[str, Any]) -> str:
    deadline = purchase_deadline_for_target(target_date, config)
    return deadline.strftime("%Y-%m-%d %H:%M")


def fetch_jsonp(url: str, *, timeout: int, headers: dict[str, str]) -> Any:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8", errors="ignore").strip()
    match = JSONP_PATTERN.match(payload)
    if not match:
        raise RuntimeError("开奖结果接口返回了无法解析的内容。")
    return json.loads(match.group(1))


def fetch_text(url: str, *, timeout: int, headers: dict[str, str]) -> str:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


def fetch_json(url: str, *, timeout: int, headers: dict[str, str]) -> Any:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="ignore"))


def draw_to_dict(draw: DrawResult) -> dict[str, Any]:
    return {
        "issue": draw.issue,
        "open_date": draw.open_date.isoformat(),
        "reds": draw.reds,
        "blue": draw.blue,
        "sale_money": draw.sale_money,
        "prize_pool_money": draw.prize_pool_money,
    }


def draw_from_dict(item: dict[str, Any]) -> DrawResult:
    return DrawResult(
        issue=str(item["issue"]),
        open_date=date.fromisoformat(str(item["open_date"])),
        reds=[int(number) for number in item["reds"]],
        blue=int(item["blue"]),
        sale_money=parse_int(item.get("sale_money")),
        prize_pool_money=parse_int(item.get("prize_pool_money")),
    )


def draw_to_comparison_dict(draw: DrawResult) -> dict[str, Any]:
    payload = draw_to_dict(draw)
    payload["numbers_text"] = format_draw_numbers(draw)
    return payload


def save_history_cache(config: dict[str, Any], history: list[DrawResult], fetched_at: str | None = None) -> None:
    fetch_config = config["fetch"]
    cache_path = resolve_project_path(fetch_config["cache_path"])
    save_json_file(
        cache_path,
        {
            "fetched_at": fetched_at or utc_now_iso(),
            "records": [draw_to_dict(draw) for draw in history],
        },
    )


def load_history_cache(config: dict[str, Any]) -> tuple[list[DrawResult], dict[str, Any]]:
    fetch_config = config["fetch"]
    cache_path = resolve_project_path(fetch_config["cache_path"])
    payload = load_json_file(cache_path, {"fetched_at": None, "records": []})
    records = [draw_from_dict(item) for item in payload.get("records", [])]
    ordered = sorted(records, key=lambda item: issue_to_int(item.issue), reverse=True)
    return ordered, payload


def fetch_history_from_remote(config: dict[str, Any], state: dict[str, Any]) -> list[DrawResult]:
    fetch_config = config["fetch"]
    lottery_config = config["lottery"]
    target_count = int(lottery_config["history_count"])
    page_size = max(1, min(int(fetch_config["page_size"]), 100))
    endpoint = str(fetch_config["endpoint"])
    timeout = int(fetch_config["http_timeout_seconds"])
    headers = {
        "User-Agent": str(fetch_config["user_agent"]),
        "Referer": str(fetch_config["referer"]),
    }

    results: list[DrawResult] = []
    page_num = 1
    total_pages = 1
    while page_num <= total_pages and len(results) < target_count:
        query = urllib.parse.urlencode(
            {
                "transactionType": "10001001",
                "lotteryId": LOTTERY_ID,
                "type": "0",
                "pageNum": str(page_num),
                "pageSize": str(page_size),
                "issueCount": str(target_count),
                "callback": "cb",
                "tt": f"{random.random():.6f}",
            }
        )
        payload = fetch_jsonp(f"{endpoint}?{query}", timeout=timeout, headers=headers)
        if payload.get("resCode") != "000000":
            raise RuntimeError(f"开奖结果接口返回错误: {payload.get('message', 'unknown')}")
        total_pages = int(payload.get("pages", "1"))
        for item in payload.get("data", []):
            reds = [int(number) for number in str(item["frontWinningNum"]).split()]
            blue = int(str(item["backWinningNum"]).split()[0])
            results.append(
                DrawResult(
                    issue=str(item["issue"]),
                    open_date=datetime.strptime(item["openTime"], "%Y-%m-%d").date(),
                    reds=sorted(reds),
                    blue=blue,
                    sale_money=parse_int(item.get("saleMoney")),
                    prize_pool_money=parse_int(item.get("prizePoolMoney")),
                )
            )
        page_num += 1

    unique_by_issue: dict[str, DrawResult] = {}
    for item in results:
        unique_by_issue[item.issue] = item
    ordered = sorted(unique_by_issue.values(), key=lambda item: issue_to_int(item.issue), reverse=True)
    save_history_cache(config, ordered)
    state["last_history_fetch_at"] = utc_now_iso()
    return ordered


def extract_official_latest_draw(html: str) -> DrawResult:
    matched: dict[str, str] = {}
    for key, pattern in OFFICIAL_FIELD_PATTERNS.items():
        match = pattern.search(html)
        if not match:
            raise RuntimeError(f"官网开奖页缺少字段: {key}")
        matched[key] = match.group(1).strip()

    reds = [int(item.strip()) for item in matched["reds"].split(",") if item.strip()]
    blue_values = [int(item.strip()) for item in matched["blue"].split(",") if item.strip()]
    if len(reds) != 6 or len(blue_values) != 1:
        raise RuntimeError("官网开奖页返回的双色球号码格式异常。")

    detail_link = matched["detail_link"]
    date_match = re.search(r"/c/(\d{4})/(\d{2})/(\d{2})/", detail_link)
    if not date_match:
        raise RuntimeError("无法从官网开奖详情链接中解析开奖日期。")
    open_date = date(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)))

    return DrawResult(
        issue=matched["issue"],
        open_date=open_date,
        reds=sorted(reds),
        blue=blue_values[0],
        sale_money=parse_int(matched["sales"]),
        prize_pool_money=parse_int(matched["pool"]),
    )


def extract_official_latest_draw_from_api(payload: Any) -> DrawResult:
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("官网 API 返回为空。")

    best_item: dict[str, Any] | None = None
    best_issue = ""
    for item in payload:
        issue = str(item.get("code") or item.get("issue") or "").strip()
        if not OFFICIAL_JSON_ISSUE_PATTERN.match(issue):
            continue
        if issue > best_issue:
            best_issue = issue
            best_item = item
    if not best_item:
        raise RuntimeError("官网 API 中未找到有效的双色球期号。")

    reds_raw = str(best_item.get("red") or best_item.get("redBall") or "").strip()
    blue_raw = str(best_item.get("blue") or best_item.get("blueBall") or "").strip()
    if not reds_raw or not blue_raw:
        raise RuntimeError("官网 API 缺少开奖球号字段。")

    reds = [int(part) for part in re.split(r"[\s,|]+", reds_raw) if part]
    blue_values = [int(part) for part in re.split(r"[\s,|]+", blue_raw) if part]
    if len(reds) != 6 or len(blue_values) != 1:
        raise RuntimeError("官网 API 返回的双色球号码格式异常。")

    open_date_text = str(
        best_item.get("date")
        or best_item.get("openTime")
        or best_item.get("kaijiangriqi")
        or ""
    ).strip()
    open_date_match = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})", open_date_text)
    if not open_date_match:
        raise RuntimeError("官网 API 缺少可解析的开奖日期。")
    open_date = date(
        int(open_date_match.group(1)),
        int(open_date_match.group(2)),
        int(open_date_match.group(3)),
    )

    return DrawResult(
        issue=best_issue,
        open_date=open_date,
        reds=sorted(reds),
        blue=blue_values[0],
        sale_money=parse_int(best_item.get("sales") or best_item.get("saleMoney")),
        prize_pool_money=parse_int(best_item.get("pool") or best_item.get("prizePoolMoney")),
    )


def fetch_latest_draw_candidates_from_official_sources(config: dict[str, Any]) -> tuple[list[tuple[DrawResult, str]], list[str]]:
    fetch_config = config["fetch"]
    timeout = int(fetch_config.get("official_timeout_seconds", fetch_config["http_timeout_seconds"]))
    common_headers = {
        "User-Agent": str(fetch_config["user_agent"]),
        "Referer": "https://cwlgovcn.com/ygkj/wqkjgg/",
    }

    candidates: list[tuple[DrawResult, str]] = []
    errors: list[str] = []

    html_url = str(fetch_config["official_latest_url"])
    try:
        html = fetch_text(html_url, timeout=timeout, headers=common_headers)
        draw = extract_official_latest_draw(html)
        candidates.append((draw, f"official_html:{html_url}"))
    except Exception as exc:
        errors.append(f"official_html={exc}")

    api_url = str(fetch_config.get("official_api_url", "")).strip()
    if api_url:
        try:
            payload = fetch_json(api_url, timeout=timeout, headers=common_headers)
            draw = extract_official_latest_draw_from_api(payload)
            candidates.append((draw, f"official_api:{api_url}"))
        except Exception as exc:
            errors.append(f"official_api={exc}")

    legacy_url = str(fetch_config.get("official_legacy_url", "")).strip()
    if legacy_url:
        legacy_headers = dict(common_headers)
        legacy_headers["Referer"] = "https://www.cwl.gov.cn/"
        try:
            payload = fetch_json(legacy_url, timeout=timeout, headers=legacy_headers)
            draw = extract_official_latest_draw_from_api(payload)
            candidates.append((draw, f"official_legacy:{legacy_url}"))
        except Exception as exc:
            errors.append(f"official_legacy={exc}")

    return candidates, errors


def refresh_latest_from_official(config: dict[str, Any], state: dict[str, Any]) -> tuple[list[DrawResult], bool, str]:
    history, _cache_meta = load_history_cache(config)
    candidates, errors = fetch_latest_draw_candidates_from_official_sources(config)
    state["last_official_refresh_at"] = utc_now_iso()

    if not candidates:
        error_text = "；".join(errors) or "未找到可用的官方开奖结果链路。"
        state["last_official_refresh_status"] = f"all_failed: {error_text}"
        raise RuntimeError(error_text)

    candidates.sort(key=lambda item: issue_to_int(item[0].issue), reverse=True)
    latest_official, source_label = candidates[0]

    if not history:
        history = [latest_official]
        save_history_cache(config, history)
        state["last_history_fetch_at"] = utc_now_iso()
        state["last_official_refresh_status"] = f"{source_label}: initialized {latest_official.issue}"
        return history, True, f"已通过 {source_label} 从官网初始化本地缓存，最新期号 {latest_official.issue}。"

    latest_local = history[0]
    if issue_to_int(latest_official.issue) < issue_to_int(latest_local.issue):
        source_notes = ", ".join(f"{label.split(':', 1)[0]}={draw.issue}" for draw, label in candidates)
        state["last_official_refresh_status"] = f"stale_all: {source_notes}"
        return history, False, f"所有官方链路返回的期号都早于本地缓存 {latest_local.issue}，未更新。"
    if latest_official.issue == latest_local.issue:
        history[0] = latest_official
        save_history_cache(config, history)
        state["last_history_fetch_at"] = utc_now_iso()
        state["last_official_refresh_status"] = f"{source_label}: replaced {latest_official.issue}"
        return history, False, f"官网最新期号 {latest_official.issue} 已存在，本地缓存已覆盖更新。"

    history.insert(0, latest_official)
    max_count = int(config["lottery"]["history_count"])
    history = history[:max_count]
    save_history_cache(config, history)
    state["last_history_fetch_at"] = utc_now_iso()
    state["last_official_refresh_status"] = f"{source_label}: appended {latest_official.issue}"
    return history, True, f"已从官网追加最新一期 {latest_official.issue} 到本地缓存。"


def determine_prize_level(red_matches: int, blue_match: bool) -> str | None:
    if red_matches == 6 and blue_match:
        return "一等奖"
    if red_matches == 6:
        return "二等奖"
    if red_matches == 5 and blue_match:
        return "三等奖"
    if red_matches == 5 or (red_matches == 4 and blue_match):
        return "四等奖"
    if red_matches == 4 or (red_matches == 3 and blue_match):
        return "五等奖"
    if blue_match:
        return "六等奖"
    return None


def prize_amount_label(prize: str | None) -> str:
    if not prize:
        return "未中奖"
    amount = FIXED_PRIZE_AMOUNTS.get(prize)
    if amount is None:
        return f"{prize}（浮动奖金）"
    return f"{prize}（预计 {amount} 元）"


def compare_selected_numbers_against_draw(
    selected: list[dict[str, Any]], draw: DrawResult
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    winning_entries = 0
    highest_prize_rank = 99
    highest_prize_name = ""
    fixed_prize_total = 0
    floating_prize_entries = 0
    prize_breakdown: Counter[str] = Counter()
    prize_rank = {
        "一等奖": 1,
        "二等奖": 2,
        "三等奖": 3,
        "四等奖": 4,
        "五等奖": 5,
        "六等奖": 6,
    }

    winning_reds = set(draw.reds)
    for index, entry in enumerate(selected, start=1):
        reds = [int(number) for number in entry["reds"]]
        blue = int(entry["blue"])
        red_match_count = len(set(reds).intersection(winning_reds))
        blue_match = blue == draw.blue
        prize = determine_prize_level(red_match_count, blue_match)
        if prize:
            winning_entries += 1
            prize_breakdown[prize] += 1
            current_rank = prize_rank[prize]
            if current_rank < highest_prize_rank:
                highest_prize_rank = current_rank
                highest_prize_name = prize
            fixed_amount = FIXED_PRIZE_AMOUNTS.get(prize)
            if fixed_amount is None:
                floating_prize_entries += 1
            else:
                fixed_prize_total += fixed_amount
        comparisons.append(
            {
                "index": index,
                "reds": reds,
                "blue": blue,
                "red_match_count": red_match_count,
                "blue_match": blue_match,
                "prize": prize,
                "prize_amount_label": prize_amount_label(prize),
                "numbers_text": f"红球 {' '.join(normalize_ball(number) for number in reds)} | 蓝球 {normalize_ball(blue)}",
            }
        )

    summary = {
        "total_entries": len(selected),
        "winning_entries": winning_entries,
        "highest_prize": highest_prize_name or "未中奖",
        "is_winner": winning_entries > 0,
        "prize_breakdown": dict(prize_breakdown),
        "fixed_prize_total": fixed_prize_total,
        "floating_prize_entries": floating_prize_entries,
    }
    return comparisons, summary


def build_comparison_message(
    config: dict[str, Any],
    issue: str,
    draw: DrawResult,
    issued_record: dict[str, Any],
    comparisons: list[dict[str, Any]],
    summary: dict[str, Any],
) -> str:
    source_text = {
        "push": "定时主动推送",
        "reply": "即时消息取号",
        "post-draw-followup": "开奖后自动续推",
        "legacy-last-analysis": "历史记录迁移",
    }.get(str(issued_record.get("source", "")), str(issued_record.get("source", "")) or "未知来源")

    lines = [
        f"{config['notification']['comparison_prefix']}（第 {issue} 期）",
        f"开奖号码: {format_draw_numbers(draw)}",
        f"对比来源: {source_text}",
        f"记录时间: {issued_record.get('issued_at', '')}",
        "",
    ]
    for comparison in comparisons:
        prize_text = comparison["prize_amount_label"]
        lines.append(
            f"{comparison['index']}. {comparison['numbers_text']} -> 命中红球 {comparison['red_match_count']} 个，"
            f"蓝球 {'命中' if comparison['blue_match'] else '未中'}，结果：{prize_text}"
        )

    prize_breakdown = summary.get("prize_breakdown", {})
    breakdown_text = (
        "，".join(f"{name}{count}组" for name, count in prize_breakdown.items())
        if prize_breakdown
        else "无"
    )
    fixed_total = int(summary.get("fixed_prize_total", 0))
    floating_entries = int(summary.get("floating_prize_entries", 0))
    lines.extend(
        [
            "",
            f"本次对比的是你收到的第 {issue} 期推荐号码，共 {summary['total_entries']} 组。",
            f"结果汇总: {'有中奖' if summary['is_winner'] else '未中奖'}；命中 {summary['winning_entries']} 组；最高奖级：{summary['highest_prize']}",
            f"奖级分布: {breakdown_text}",
            f"固定奖预计合计: {fixed_total} 元",
            f"浮动奖命中组数: {floating_entries}",
        ]
    )
    return "\n".join(lines)


def record_issued_recommendation(
    state: dict[str, Any],
    result: dict[str, Any],
    *,
    source: str,
    shared_via_message: bool,
    request_text: str | None = None,
) -> None:
    issued_recommendations = dict(state.get("issued_recommendations", {}))
    issue = str(result["target_issue"])
    issued_recommendations[issue] = {
        "issue": issue,
        "target_draw_date": result["target_draw_date"],
        "issued_at": utc_now_iso(),
        "source": source,
        "shared_via_message": shared_via_message,
        "request_text": request_text or "",
        "selected": deepcopy(result["selected"]),
        "summary": result["summary"],
        "message": result["message"],
    }
    if len(issued_recommendations) > 24:
        ordered_issues = sorted(issued_recommendations.keys(), reverse=True)
        issued_recommendations = {key: issued_recommendations[key] for key in ordered_issues[:24]}
    state["issued_recommendations"] = issued_recommendations


def process_latest_draw_comparison(
    config: dict[str, Any],
    state: dict[str, Any],
    latest_draw: DrawResult,
    *,
    send_notification: bool,
) -> tuple[dict[str, Any] | None, bool]:
    issued_record = dict(state.get("issued_recommendations", {})).get(latest_draw.issue)
    if not issued_record:
        return None, False

    existing_report = dict(state.get("comparison_reports", {})).get(latest_draw.issue)
    if existing_report and existing_report.get("draw_issue") == latest_draw.issue and existing_report.get("notified_at"):
        return existing_report, False

    comparisons, summary = compare_selected_numbers_against_draw(issued_record.get("selected", []), latest_draw)
    message = build_comparison_message(config, latest_draw.issue, latest_draw, issued_record, comparisons, summary)
    report = {
        "draw_issue": latest_draw.issue,
        "draw": draw_to_comparison_dict(latest_draw),
        "issued_at": issued_record.get("issued_at"),
        "source": issued_record.get("source", ""),
        "comparisons": comparisons,
        "summary": summary,
        "message": message,
        "generated_at": utc_now_iso(),
        "notified_at": None,
    }

    did_send = False
    if send_notification and config["notification"]["enabled"]:
        send_message_via_openclaw(config, message)
        report["notified_at"] = utc_now_iso()
        did_send = True

    comparison_reports = dict(state.get("comparison_reports", {}))
    comparison_reports[latest_draw.issue] = report
    if len(comparison_reports) > 24:
        ordered_issues = sorted(comparison_reports.keys(), reverse=True)
        comparison_reports = {key: comparison_reports[key] for key in ordered_issues[:24]}
    state["comparison_reports"] = comparison_reports
    return report, did_send


def get_history_for_analysis(config: dict[str, Any], state: dict[str, Any]) -> list[DrawResult]:
    history, _cache_meta = load_history_cache(config)
    required_count = int(config["lottery"]["history_count"])
    if len(history) >= required_count:
        return history[:required_count]
    fetched = fetch_history_from_remote(config, state)
    return fetched[:required_count]


def build_stats(history: list[DrawResult]) -> dict[str, Any]:
    red_counter: Counter[int] = Counter()
    blue_counter: Counter[int] = Counter()
    sum_values: list[int] = []
    span_values: list[int] = []
    odd_even_patterns: Counter[str] = Counter()
    zone_patterns: Counter[str] = Counter()
    repeat_with_prev: Counter[int] = Counter()

    red_last_seen = {number: None for number in range(1, 34)}
    blue_last_seen = {number: None for number in range(1, 17)}

    for index, draw in enumerate(history):
        red_counter.update(draw.reds)
        blue_counter.update([draw.blue])
        sum_values.append(sum(draw.reds))
        span_values.append(max(draw.reds) - min(draw.reds))
        odd_even_patterns[f"{sum(number % 2 for number in draw.reds)}:{6 - sum(number % 2 for number in draw.reds)}"] += 1

        zones = (
            sum(1 for number in draw.reds if 1 <= number <= 11),
            sum(1 for number in draw.reds if 12 <= number <= 22),
            sum(1 for number in draw.reds if 23 <= number <= 33),
        )
        zone_patterns["-".join(str(value) for value in zones)] += 1

        for number in draw.reds:
            if red_last_seen[number] is None:
                red_last_seen[number] = index
        if blue_last_seen[draw.blue] is None:
            blue_last_seen[draw.blue] = index

        if index + 1 < len(history):
            previous = set(history[index + 1].reds)
            repeat_with_prev[len(previous.intersection(draw.reds))] += 1

    for number in red_last_seen:
        if red_last_seen[number] is None:
            red_last_seen[number] = len(history)
    for number in blue_last_seen:
        if blue_last_seen[number] is None:
            blue_last_seen[number] = len(history)

    return {
        "red_counter": red_counter,
        "blue_counter": blue_counter,
        "red_last_seen": red_last_seen,
        "blue_last_seen": blue_last_seen,
        "sum_mean": sum(sum_values) / len(sum_values),
        "sum_std": max(1.0, statistics_std(sum_values)),
        "span_mean": sum(span_values) / len(span_values),
        "span_std": max(1.0, statistics_std(span_values)),
        "odd_even_patterns": odd_even_patterns,
        "zone_patterns": zone_patterns,
        "repeat_with_prev": repeat_with_prev,
    }


def statistics_std(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def consecutive_groups(reds: list[int]) -> list[list[int]]:
    groups: list[list[int]] = []
    current: list[int] = []
    for number in sorted(reds):
        if not current or number == current[-1] + 1:
            current.append(number)
            continue
        groups.append(current)
        current = [number]
    if current:
        groups.append(current)
    return groups


def weighted_sample_without_replacement(
    rng: random.Random, population: list[int], weights: list[float], k: int
) -> list[int]:
    available = list(population)
    current_weights = list(weights)
    chosen: list[int] = []
    for _ in range(k):
        total_weight = sum(current_weights)
        if total_weight <= 0:
            break
        target = rng.random() * total_weight
        cursor = 0.0
        picked_index = 0
        for index, weight in enumerate(current_weights):
            cursor += weight
            if cursor >= target:
                picked_index = index
                break
        chosen.append(available.pop(picked_index))
        current_weights.pop(picked_index)
    return chosen


def blue_weight(stats: dict[str, Any], number: int) -> float:
    frequency = stats["blue_counter"][number]
    recency = stats["blue_last_seen"][number]
    return 1.0 + recency * 0.28 + max(0.0, 6.0 - frequency) * 0.12


def red_weight(stats: dict[str, Any], number: int) -> float:
    frequency = stats["red_counter"][number]
    recency = stats["red_last_seen"][number]
    medium_bias = 1.0 / (1.0 + abs(frequency - 18.0))
    return 1.0 + recency * 0.22 + medium_bias * 4.5


def candidate_features(reds: list[int]) -> dict[str, Any]:
    odd_count = sum(number % 2 for number in reds)
    zones = (
        sum(1 for number in reds if 1 <= number <= 11),
        sum(1 for number in reds if 12 <= number <= 22),
        sum(1 for number in reds if 23 <= number <= 33),
    )
    groups = consecutive_groups(reds)
    return {
        "sum": sum(reds),
        "span": max(reds) - min(reds),
        "odd_count": odd_count,
        "even_count": 6 - odd_count,
        "zones": zones,
        "consecutive_groups": groups,
        "max_consecutive_length": max(len(group) for group in groups),
        "tail_counts": Counter(number % 10 for number in reds),
    }


def passes_candidate_rules(
    reds: list[int],
    blue: int,
    history: list[DrawResult],
    config: dict[str, Any],
) -> bool:
    features = candidate_features(reds)
    if not (80 <= features["sum"] <= 150):
        return False
    if not (16 <= features["span"] <= 30):
        return False
    if features["odd_count"] not in {2, 3, 4}:
        return False
    if max(features["zones"]) > 3 or min(features["zones"]) < 1:
        return False
    if features["max_consecutive_length"] > 2:
        return False
    if max(features["tail_counts"].values()) > 2:
        return False
    if blue < 1 or blue > 16:
        return False

    last_draw = history[0]
    if len(set(reds).intersection(last_draw.reds)) > int(config["recommendation"]["max_repeat_reds_with_last_draw"]):
        return False
    for draw in history[:30]:
        if draw.reds == reds and draw.blue == blue:
            return False
    return True


def z_score(value: float, mean: float, std_value: float) -> float:
    if std_value <= 0:
        return 0.0
    return abs(value - mean) / std_value


def candidate_score(
    reds: list[int],
    blue: int,
    history: list[DrawResult],
    stats: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    features = candidate_features(reds)
    red_score = sum(red_weight(stats, number) for number in reds)
    blue_score = blue_weight(stats, blue)
    sum_penalty = z_score(features["sum"], stats["sum_mean"], stats["sum_std"])
    span_penalty = z_score(features["span"], stats["span_mean"], stats["span_std"])
    odd_pattern_key = f"{features['odd_count']}:{features['even_count']}"
    zone_pattern_key = "-".join(str(item) for item in features["zones"])
    pattern_bonus = stats["odd_even_patterns"][odd_pattern_key] * 0.25 + stats["zone_patterns"][zone_pattern_key] * 0.35
    recent_penalty = 0.0
    for draw in history[:5]:
        overlap = len(set(reds).intersection(draw.reds))
        if overlap >= 3:
            recent_penalty += overlap * 2.4

    score = red_score + blue_score + pattern_bonus - (sum_penalty * 4.2 + span_penalty * 3.8 + recent_penalty)
    features["odd_pattern_key"] = odd_pattern_key
    features["zone_pattern_key"] = zone_pattern_key
    return score, features


def build_candidates(history: list[DrawResult], stats: dict[str, Any], config: dict[str, Any]) -> list[Candidate]:
    recommendation_config = config["recommendation"]
    seed_value = recommendation_config.get("random_seed")
    rng = random.Random(seed_value)

    population_red = list(range(1, 34))
    population_blue = list(range(1, 17))
    red_weights = [red_weight(stats, number) for number in population_red]
    blue_weights = [blue_weight(stats, number) for number in population_blue]

    unique_candidates: dict[tuple[tuple[int, ...], int], Candidate] = {}
    target_pool = int(recommendation_config["candidate_pool_size"])
    attempts = max(target_pool * 3, 1200)

    for _ in range(attempts):
        reds = sorted(weighted_sample_without_replacement(rng, population_red, red_weights, 6))
        blue = weighted_sample_without_replacement(rng, population_blue, blue_weights, 1)[0]
        if not passes_candidate_rules(reds, blue, history, config):
            continue
        score, features = candidate_score(reds, blue, history, stats)
        key = (tuple(reds), blue)
        previous = unique_candidates.get(key)
        if previous is None or score > previous.heuristic_score:
            unique_candidates[key] = Candidate(reds=reds, blue=blue, heuristic_score=score, features=features)
        if len(unique_candidates) >= target_pool:
            break

    ordered = sorted(unique_candidates.values(), key=lambda item: item.heuristic_score, reverse=True)
    shortlist_size = int(recommendation_config["candidate_shortlist_size"])
    return ordered[:shortlist_size]


def safe_json_loads(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def discover_llm_config(config: dict[str, Any]) -> tuple[str, str, str]:
    llm_config = config["llm"]
    if llm_config.get("base_url") and llm_config.get("api_key"):
        return (
            str(llm_config["base_url"]).rstrip("/"),
            str(llm_config["api_key"]),
            str(llm_config["model"]),
        )

    openclaw_config = Path.home() / ".openclaw" / "openclaw.json"
    if not openclaw_config.exists():
        raise RuntimeError("未找到 OpenClaw 模型配置，无法启用 LLM 排序。")
    payload = json.loads(openclaw_config.read_text())
    provider_name = str(llm_config["provider"])
    provider = payload.get("models", {}).get("providers", {}).get(provider_name)
    if not provider:
        raise RuntimeError(f"OpenClaw 中未配置 LLM provider: {provider_name}")
    return str(provider["baseUrl"]).rstrip("/"), str(provider["apiKey"]), str(llm_config["model"])


def call_llm_for_ranking(
    candidates: list[Candidate], history: list[DrawResult], stats: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    llm_config = config["llm"]
    base_url, api_key, model_name = discover_llm_config(config)
    endpoint = f"{base_url}/chat/completions"
    selection_count = int(config["recommendation"]["count"])

    candidate_lines = []
    for index, candidate in enumerate(candidates, start=1):
        candidate_lines.append(
            {
                "id": index,
                "reds": candidate.reds,
                "blue": candidate.blue,
                "heuristic_score": round(candidate.heuristic_score, 3),
                "sum": candidate.features["sum"],
                "span": candidate.features["span"],
                "odd_even": f"{candidate.features['odd_count']}:{candidate.features['even_count']}",
                "zones": list(candidate.features["zones"]),
                "last_draw_overlap": len(set(candidate.reds).intersection(history[0].reds)),
            }
        )

    prompt = {
        "task": "你是一个谨慎的彩票推荐排序器。请基于候选号码、最近开奖走势和组合分布，从候选列表里选出最均衡的参考组合。",
        "constraints": [
            f"必须只从候选列表里选择，不能自造号码。",
            f"输出 {selection_count} 组组合。",
            "不要宣称高中奖率、必中或收益承诺。",
            "理由简短，聚焦于分布均衡、冷热搭配、和值跨度与最近重号控制。",
            "返回严格 JSON。",
        ],
        "latest_draw": {
            "issue": history[0].issue,
            "open_date": history[0].open_date.isoformat(),
            "reds": history[0].reds,
            "blue": history[0].blue,
        },
        "stats": {
            "history_count": len(history),
            "sum_mean": round(stats["sum_mean"], 2),
            "sum_std": round(stats["sum_std"], 2),
            "span_mean": round(stats["span_mean"], 2),
            "common_odd_even": stats["odd_even_patterns"].most_common(5),
            "common_zones": stats["zone_patterns"].most_common(5),
        },
        "candidates": candidate_lines,
        "output_schema": {
            "selected": [
                {
                    "id": 1,
                    "reason": "一句中文理由"
                }
            ],
            "summary": "一句整体摘要",
        },
    }

    body = {
        "model": model_name,
        "temperature": float(llm_config["temperature"]),
        "max_tokens": int(llm_config["max_tokens"]),
        "messages": [
            {"role": "system", "content": "你只输出 JSON，不要输出 Markdown。"},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    timeout = int(llm_config["timeout_seconds"])
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = payload["choices"][0]["message"]["content"]
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text", "")))
        content = "".join(text_parts)
    return safe_json_loads(str(content))


def select_final_candidates(
    candidates: list[Candidate], history: list[DrawResult], stats: dict[str, Any], config: dict[str, Any]
) -> tuple[list[tuple[Candidate, str]], str]:
    selection_count = max(1, int(config["recommendation"]["count"]))
    if not config["llm"]["enabled"]:
        return (
            [(candidate, "按本地规则评分靠前，分布均衡且与最近开奖重号受控。") for candidate in candidates[:selection_count]],
            "本次未启用 LLM，按本地规则评分输出。",
        )

    try:
        ranking = call_llm_for_ranking(candidates, history, stats, config)
        reason_by_id = {
            int(item["id"]): str(item["reason"]).strip()
            for item in ranking.get("selected", [])
            if str(item.get("id", "")).isdigit()
        }
        selected: list[tuple[Candidate, str]] = []
        for item_id, reason in reason_by_id.items():
            if 1 <= item_id <= len(candidates):
                selected.append((candidates[item_id - 1], reason or "LLM 认为该组分布更均衡。"))
        if len(selected) < selection_count:
            existing = {tuple(item[0].reds) + (item[0].blue,) for item in selected}
            for candidate in candidates:
                key = tuple(candidate.reds) + (candidate.blue,)
                if key in existing:
                    continue
                selected.append((candidate, "补足候选列表，保持冷热与跨度的平衡。"))
                if len(selected) >= selection_count:
                    break
        summary = str(ranking.get("summary", "结合本地规则与 LLM 排序输出参考组合。")).strip()
        return selected[:selection_count], summary
    except Exception as exc:
        fallback = [(candidate, "LLM 排序不可用，按本地规则评分输出。") for candidate in candidates[:selection_count]]
        return fallback, f"LLM 排序失败，已回退到本地规则: {exc}"


def compute_target_issue(now_local: datetime, history: list[DrawResult], config: dict[str, Any]) -> tuple[str, date]:
    sales_close_hour, sales_close_minute = parse_hhmm(config["lottery"]["sales_close_time"])
    latest = history[0]
    latest_issue = latest.issue
    latest_date = latest.open_date

    today = now_local.date()
    sales_close_today = datetime.combine(today, time(sales_close_hour, sales_close_minute), tzinfo=now_local.tzinfo)

    if today.weekday() in DRAW_WEEKDAYS and now_local < sales_close_today and today > latest_date:
        target_date = today
    elif today.weekday() in DRAW_WEEKDAYS and now_local < sales_close_today and today == latest_date:
        target_date = next_draw_date_after(today)
    else:
        target_date = next_draw_date_after(max(today, latest_date))

    steps = draw_count_between(latest_date, target_date)
    if steps == 0 and target_date == latest_date:
        return latest_issue, target_date
    return increment_issue(latest_issue, steps), target_date


def in_purchase_window(now_local: datetime, config: dict[str, Any]) -> bool:
    if now_local.weekday() not in DRAW_WEEKDAYS:
        return False
    close_hour, close_minute = parse_hhmm(config["lottery"]["sales_close_time"])
    close_dt = datetime.combine(now_local.date(), time(close_hour, close_minute), tzinfo=now_local.tzinfo)
    return now_local < close_dt


def build_message(
    target_issue: str,
    target_date: date,
    selected: list[tuple[Candidate, str]],
    summary: str,
    config: dict[str, Any],
) -> str:
    lines = [
        f"{config['notification']['message_prefix']}（第 {target_issue} 期，开奖日 {target_date.isoformat()}）",
        f"购买截止: {format_purchase_deadline(target_date, config)}",
        "",
    ]
    for index, (candidate, reason) in enumerate(selected, start=1):
        lines.append(format_candidate_line(index, candidate, reason))
    lines.extend(
        [
            "",
            f"摘要: {summary}",
            f"说明: {config['recommendation']['disclaimer']}",
        ]
    )
    return "\n".join(lines)


def send_message_via_openclaw(config: dict[str, Any], message: str) -> subprocess.CompletedProcess[str]:
    target = str(config["notification"].get("target") or "").strip()
    if not target:
        raise RuntimeError("notification.target 未配置，无法发送 iMessage。")
    channel = str(config["notification"]["channel"])
    command = [
        resolve_openclaw_bin(),
        "message",
        "send",
        "--channel",
        channel,
        "--target",
        target,
        "--message",
        message,
    ]
    return subprocess.run(
        command,
        env=build_subprocess_env(),
        text=True,
        capture_output=True,
        check=True,
    )


def analyze_with_history(config: dict[str, Any], state: dict[str, Any], history: list[DrawResult]) -> dict[str, Any]:
    if not history:
        raise RuntimeError("未获取到历史开奖数据。")
    stats = build_stats(history)
    candidates = build_candidates(history, stats, config)
    if not candidates:
        raise RuntimeError("未生成可用候选号码，请放宽规则后重试。")
    selected, summary = select_final_candidates(candidates, history, stats, config)
    target_issue, target_date = compute_target_issue(now_in_tz(config["lottery"]["timezone"]), history, config)
    message = build_message(target_issue, target_date, selected, summary, config)

    result = {
        "generated_at": utc_now_iso(),
        "target_issue": target_issue,
        "target_draw_date": target_date.isoformat(),
        "latest_draw_issue": history[0].issue,
        "latest_draw_date": history[0].open_date.isoformat(),
        "selected": [
            {
                "reds": candidate.reds,
                "blue": candidate.blue,
                "reason": reason,
                "heuristic_score": round(candidate.heuristic_score, 3),
                "features": {
                    "sum": candidate.features["sum"],
                    "span": candidate.features["span"],
                    "odd_even": f"{candidate.features['odd_count']}:{candidate.features['even_count']}",
                    "zones": list(candidate.features["zones"]),
                },
            }
            for candidate, reason in selected
        ],
        "summary": summary,
        "message": message,
    }
    state["last_analysis"] = result
    state["last_message_preview"] = message
    return result


def analyze(config: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    history = get_history_for_analysis(config, state)
    return analyze_with_history(config, state, history)


def sent_window_key(issue: str, target_date: date) -> str:
    return f"{issue}@{target_date.isoformat()}"


def has_sent_window(state: dict[str, Any], key: str) -> bool:
    return key in state.get("sent_windows", [])


def mark_sent_window(state: dict[str, Any], key: str) -> None:
    sent_windows = list(state.get("sent_windows", []))
    if key not in sent_windows:
        sent_windows.append(key)
    state["sent_windows"] = sent_windows[-24:]


def parse_request_count(text: str, default_count: int) -> int:
    lowered = text.strip().lower()
    if not lowered:
        return default_count
    one_markers = ["1组", "一组", "1 注", "一注", "单组", "一套"]
    five_markers = ["5组", "五组", "5 注", "五注", "五套"]
    if any(marker in lowered for marker in one_markers):
        return 1
    if any(marker in lowered for marker in five_markers):
        return 5
    match = re.search(r"([15])\s*组", lowered)
    if match:
        return int(match.group(1))
    return default_count


def handle_snapshot(args: argparse.Namespace, config: dict[str, Any], state: dict[str, Any]) -> int:
    if args.count is not None:
        config["recommendation"]["count"] = args.count
    result = analyze(config, state)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(result["message"])
    return 0


def handle_run_once(args: argparse.Namespace, config: dict[str, Any], state: dict[str, Any]) -> int:
    if args.count is not None:
        config["recommendation"]["count"] = args.count
    now_local = now_in_tz(config["lottery"]["timezone"])
    if not args.force and not in_purchase_window(now_local, config):
        print("当前不在双色球购买窗口内，跳过推送。")
        return 0

    result = analyze(config, state)
    window_key = sent_window_key(result["target_issue"], date.fromisoformat(result["target_draw_date"]))
    if not args.force and has_sent_window(state, window_key):
        print(f"当前窗口已推送过：{window_key}")
        return 0

    print(result["message"])
    if args.send and not args.dry_run and config["notification"]["enabled"]:
        completed = send_message_via_openclaw(config, result["message"])
        record_issued_recommendation(state, result, source="push", shared_via_message=True)
        mark_sent_window(state, window_key)
        print(completed.stdout.strip())
    elif args.send and args.dry_run:
        print("dry-run 模式：未实际发送消息。")
    else:
        print("未启用发送，仅输出推荐结果。")
    return 0


def handle_send_test(args: argparse.Namespace, config: dict[str, Any], _state: dict[str, Any]) -> int:
    message = args.message or "双色球 watcher 测试消息：iMessage 通道可用。"
    if args.dry_run:
        print(message)
        print("dry-run 模式：未实际发送测试消息。")
        return 0
    completed = send_message_via_openclaw(config, message)
    print(completed.stdout.strip())
    return 0


def handle_bootstrap_history(_args: argparse.Namespace, config: dict[str, Any], state: dict[str, Any]) -> int:
    history = fetch_history_from_remote(config, state)
    print(f"已初始化本地历史缓存，共 {len(history)} 期，最新期号 {history[0].issue}。")
    return 0


def send_post_draw_followup_recommendation(
    config: dict[str, Any],
    state: dict[str, Any],
    history: list[DrawResult],
    *,
    send_notification: bool,
) -> tuple[dict[str, Any] | None, bool]:
    followup_config = deep_merge(config, {})
    followup_config["recommendation"]["count"] = 5
    result = analyze_with_history(followup_config, state, history)
    window_key = sent_window_key(result["target_issue"], date.fromisoformat(result["target_draw_date"]))
    if has_sent_window(state, window_key):
        return result, False

    did_send = False
    if send_notification and followup_config["notification"]["enabled"]:
        send_message_via_openclaw(followup_config, result["message"])
        did_send = True
    record_issued_recommendation(
        state,
        result,
        source="post-draw-followup",
        shared_via_message=did_send,
    )
    mark_sent_window(state, window_key)
    return result, did_send


def handle_refresh_latest(_args: argparse.Namespace, config: dict[str, Any], state: dict[str, Any]) -> int:
    history, changed, message = refresh_latest_from_official(config, state)
    print(message)
    print(f"当前本地最新期号: {history[0].issue}")
    report, did_send = process_latest_draw_comparison(
        config,
        state,
        history[0],
        send_notification=True,
    )
    if report:
        print("已生成开奖号码对比结果。")
        if did_send:
            print("已通过 iMessage 发送对比结果。")
        else:
            print("对比结果已存在或本次未发送。")
    followup_result, followup_sent = send_post_draw_followup_recommendation(
        config,
        state,
        history,
        send_notification=True,
    )
    if followup_result:
        print(f"已生成下一期推荐：{followup_result['target_issue']}")
        if followup_sent:
            print("已通过 iMessage 发送下一期 5 组推荐。")
        else:
            print("下一期推荐已存在或本次未发送。")
    return 0 if history else 1


def handle_reply(args: argparse.Namespace, config: dict[str, Any], state: dict[str, Any]) -> int:
    request_text = args.request.strip()
    requested_count = parse_request_count(request_text, int(config["recommendation"]["count"]))
    config["recommendation"]["count"] = requested_count
    result = analyze(config, state)
    record_issued_recommendation(
        state,
        result,
        source="reply",
        shared_via_message=True,
        request_text=request_text,
    )
    print(result["message"])
    return 0


def handle_compare_latest(_args: argparse.Namespace, config: dict[str, Any], state: dict[str, Any]) -> int:
    history = get_history_for_analysis(config, state)
    if not history:
        raise RuntimeError("本地没有可用开奖数据，无法对比。")
    report, did_send = process_latest_draw_comparison(
        config,
        state,
        history[0],
        send_notification=True,
    )
    if not report:
        print(f"本地最新期号 {history[0].issue} 没有可对比的已提供号码记录。")
        return 0
    print(report["message"])
    if did_send:
        print("已通过 iMessage 发送对比结果。")
    return 0


def handle_install_cron(args: argparse.Namespace, config: dict[str, Any], _state: dict[str, Any]) -> int:
    cron_config = config["cron"]
    push_command = [
        resolve_openclaw_bin(),
        "cron",
        "add",
        "--name",
        str(cron_config["name"]),
        "--agent",
        str(args.agent_id),
        "--cron",
        str(cron_config["expression"]),
        "--tz",
        str(cron_config["timezone"]),
        "--message",
        str(cron_config["message"]),
        "--thinking",
        "off",
        "--light-context",
        "--no-deliver",
        "--exact",
        "--json",
    ]
    refresh_command = [
        resolve_openclaw_bin(),
        "cron",
        "add",
        "--name",
        str(cron_config["refresh_name"]),
        "--agent",
        str(args.agent_id),
        "--cron",
        str(cron_config["refresh_expression"]),
        "--tz",
        str(cron_config["timezone"]),
        "--message",
        str(cron_config["refresh_message"]),
        "--thinking",
        "off",
        "--light-context",
        "--no-deliver",
        "--exact",
        "--json",
    ]
    completed_push = subprocess.run(
        push_command,
        env=build_subprocess_env(),
        text=True,
        capture_output=True,
        check=True,
    )
    completed_refresh = subprocess.run(
        refresh_command,
        env=build_subprocess_env(),
        text=True,
        capture_output=True,
        check=True,
    )
    print(completed_push.stdout.strip())
    print(completed_refresh.stdout.strip())
    return 0


def handle_print_schedule(_args: argparse.Namespace, config: dict[str, Any], _state: dict[str, Any]) -> int:
    print(json.dumps(config["cron"], indent=2, ensure_ascii=False))
    return 0


def load_config(path: Path) -> dict[str, Any]:
    return deep_merge(DEFAULT_CONFIG, load_json_file(path, {}))


def load_state(path: Path) -> dict[str, Any]:
    state = deep_merge(DEFAULT_STATE, load_json_file(path, {}))
    issued_recommendations = dict(state.get("issued_recommendations", {}))
    last_analysis = state.get("last_analysis", {})
    target_issue = str(last_analysis.get("target_issue") or "").strip()
    if target_issue and target_issue not in issued_recommendations and last_analysis.get("selected"):
        issued_recommendations[target_issue] = {
            "issue": target_issue,
            "target_draw_date": last_analysis.get("target_draw_date", ""),
            "issued_at": last_analysis.get("generated_at", utc_now_iso()),
            "source": "legacy-last-analysis",
            "shared_via_message": True,
            "request_text": "",
            "selected": deepcopy(last_analysis.get("selected", [])),
            "summary": last_analysis.get("summary", ""),
            "message": last_analysis.get("message", ""),
        }
        state["issued_recommendations"] = issued_recommendations
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    save_json_file(path, state)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="陕西风采双色球推荐与 iMessage 推送 agent")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH))

    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser("snapshot", help="生成当期推荐号码")
    snapshot.add_argument("--count", type=int, choices=[1, 5], help="输出 1 组或 5 组号码")
    snapshot.add_argument("--json", action="store_true", help="输出 JSON")

    run_once = subparsers.add_parser("run-once", help="执行一次窗口检查，可选择发送")
    run_once.add_argument("--count", type=int, choices=[1, 5], help="输出 1 组或 5 组号码")
    run_once.add_argument("--send", action="store_true", help="满足条件时发送 iMessage")
    run_once.add_argument("--dry-run", action="store_true", help="只演练不实际发送")
    run_once.add_argument("--force", action="store_true", help="忽略窗口与去重限制")

    send_test = subparsers.add_parser("send-test", help="发送测试 iMessage")
    send_test.add_argument("--message", default="", help="自定义测试消息")
    send_test.add_argument("--dry-run", action="store_true", help="只输出不发送")

    subparsers.add_parser("bootstrap-history", help="抓取一次历史开奖并保存到本地缓存")
    subparsers.add_parser("refresh-latest", help="开奖后从官网拉取最新一期并写入本地缓存")
    subparsers.add_parser("compare-latest", help="对比本地最新一期开奖与已提供号码，并发送结果")

    reply = subparsers.add_parser("reply", help="按消息内容即时返回推荐号码")
    reply.add_argument("--request", required=True, help="用户发送的原始消息文本")

    install_cron = subparsers.add_parser("install-cron", help="安装 OpenClaw cron 任务")
    install_cron.add_argument("--agent-id", default="ssq-watcher", help="OpenClaw agent id")

    subparsers.add_parser("print-schedule", help="打印建议的 cron 配置")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config_path = resolve_project_path(args.config)
    state_path = resolve_project_path(args.state)
    config = load_config(config_path)
    state = load_state(state_path)

    try:
        if args.command == "snapshot":
            exit_code = handle_snapshot(args, config, state)
        elif args.command == "run-once":
            exit_code = handle_run_once(args, config, state)
        elif args.command == "send-test":
            exit_code = handle_send_test(args, config, state)
        elif args.command == "bootstrap-history":
            exit_code = handle_bootstrap_history(args, config, state)
        elif args.command == "refresh-latest":
            exit_code = handle_refresh_latest(args, config, state)
        elif args.command == "compare-latest":
            exit_code = handle_compare_latest(args, config, state)
        elif args.command == "reply":
            exit_code = handle_reply(args, config, state)
        elif args.command == "install-cron":
            exit_code = handle_install_cron(args, config, state)
        elif args.command == "print-schedule":
            exit_code = handle_print_schedule(args, config, state)
        else:
            parser.error(f"unknown command: {args.command}")
            return 2
    except subprocess.CalledProcessError as exc:
        output = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        print(f"命令执行失败: {output}", file=sys.stderr)
        return exc.returncode or 1
    except (RuntimeError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        print(f"执行失败: {exc}", file=sys.stderr)
        return 1
    finally:
        save_state(state_path, state)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
