# Lottery Invest Agent

本项目是一个面向 OpenClaw 的双色球自动化 watcher，用于：

- 初始化并缓存历史开奖数据
- 在购票窗口前生成当期推荐号码
- 支持通过消息即时获取 `1组` 或 `5组` 推荐
- 在开奖后刷新最新一期号码并自动对比是否中奖
- 通过 iMessage 把推荐结果和开奖对比结果发回用户

This project is an OpenClaw-friendly Double Color Ball watcher that:

- bootstraps and caches historical draw data locally
- generates recommended picks before the sales cutoff window
- supports on-demand replies for `1 pick` or `5 picks`
- refreshes the latest draw after the official result is published
- compares issued picks against the draw and sends the result via iMessage

## Features

- 本地缓存优先：默认不在每次请求时联网抓全量历史
- 默认推荐数量为 `5组`，需要时也可显式改成 `1组`
- 支持定时推送和即时取号两种模式
- 开奖后自动对比并给出奖级、命中组数、固定奖预计金额等信息
- 仓库默认不包含个人手机号、本地缓存和运行态状态文件

## Project Layout

- `scripts/ssq_agent.py`: 主入口 / Main entry
- `config.json`: 本地配置 / Local config
- `config.example.json`: 公开模板配置 / Public template config
- `state/runtime.json`: 本地运行状态 / Local runtime state
- `data/history_ssq.json`: 本地历史缓存 / Local history cache
- `skills/ssq-watcher/SKILL.md`: OpenClaw skill 说明
- `tests/test_ssq_agent.py`: 最小必要测试 / Focused tests

## Quick Start

1. 复制 `config.example.json` 为你自己的 `config.json`，填入 `notification.target`。
2. 首次执行 `bootstrap-history` 把历史开奖拉到本地。
3. 用 `snapshot` 或 `reply` 生成推荐号码。
4. 用 `install-cron` 把推送和开奖后刷新任务安装到 OpenClaw。

```bash
python3 ./scripts/ssq_agent.py bootstrap-history
python3 ./scripts/ssq_agent.py snapshot
python3 ./scripts/ssq_agent.py reply --request "来5组当期号码"
python3 ./scripts/ssq_agent.py install-cron --agent-id ssq-watcher
```

## Commands

```bash
python3 ./scripts/ssq_agent.py bootstrap-history
python3 ./scripts/ssq_agent.py refresh-latest
python3 ./scripts/ssq_agent.py compare-latest
python3 ./scripts/ssq_agent.py snapshot
python3 ./scripts/ssq_agent.py snapshot --count 5
python3 ./scripts/ssq_agent.py reply --request "来1组双色球"
python3 ./scripts/ssq_agent.py reply --request "来5组双色球"
python3 ./scripts/ssq_agent.py run-once --send --dry-run
python3 ./scripts/ssq_agent.py send-test --dry-run
python3 ./scripts/ssq_agent.py install-cron --agent-id ssq-watcher
python3 -m unittest tests/test_ssq_agent.py
```

## Data Flow

- `bootstrap-history`: 首次抓取一次历史开奖到 `data/history_ssq.json`
- `snapshot` / `reply` / `run-once`: 默认只读取本地缓存
- `refresh-latest`: 开奖后只刷新最新一期到本地
- `compare-latest`: 对比本地最新一期开奖结果和已发给用户的号码

## Official Refresh Fallbacks

`refresh-latest` 会按以下顺序尝试官方链路：

1. 中国福彩网开奖页 HTML
2. 中国福彩网开奖页当前引用的数据源
3. 旧版中国福彩网开奖接口

如果这些链路都不可用，系统会保留现有本地缓存，不会退回到每次全量抓历史。

## Default Schedule

- 推荐推送：`Asia/Shanghai` 时区下周二、周四、周日 `18:30`
- 开奖刷新：`Asia/Shanghai` 时区下周二、周四、周日 `21:25`

## Notes

- 默认推荐数量是 `5组`
- 即时取号支持“双色球”“来1组”“来5组”“当期号码”等消息
- 每次真正提供给用户的号码都会按期记录到 `state/runtime.json`
- 开奖后会自动生成对比结果，包含开奖号码、奖级分布、最高奖级、固定奖预计金额
- 如果本地 watcher 没跑成功，正确行为是提示暂时无法读取推荐，而不是随机编号码
- 这是娱乐参考工具，不承诺中奖概率或收益
