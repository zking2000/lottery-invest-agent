# Lottery Invest Agent

这个项目负责陕西风采双色球的本地推荐与 iMessage 推送，集成方式对齐 `clawd` 里的 ETH watcher：

- 首次初始化时抓取一次历史开奖并保存到本地
- 平时推荐只读取本地缓存，不在每次请求时联网抓全量历史
- 每次开奖后再从中国福彩网刷新最新一期写回本地
- 候选号码先由本地规则生成和过滤
- LLM 只在候选集合内做排序和简短解释
- 推送通过 `openclaw message send --channel imessage` 完成

## 目录

- `scripts/ssq_agent.py`: 主入口
- `config.json`: 配置文件
- `state/runtime.json`: 运行状态与去重记录
- `data/history_ssq.json`: 抓取缓存
- `tests/test_ssq_agent.py`: 最小必要测试

## 常用命令

```bash
python3 ./scripts/ssq_agent.py bootstrap-history
python3 ./scripts/ssq_agent.py refresh-latest
python3 ./scripts/ssq_agent.py compare-latest
python3 ./scripts/ssq_agent.py snapshot
python3 ./scripts/ssq_agent.py snapshot --count 1
python3 ./scripts/ssq_agent.py reply --request "来5组双色球"
python3 ./scripts/ssq_agent.py reply --request "来1组当期号码"
python3 ./scripts/ssq_agent.py run-once --send --dry-run
python3 ./scripts/ssq_agent.py send-test --dry-run
python3 ./scripts/ssq_agent.py install-cron --agent-id ssq-watcher
python3 -m unittest tests/test_ssq_agent.py
```

## 说明

- 数据策略是“本地缓存优先”：
  - `bootstrap-history`：首次抓一次历史开奖到 `data/history_ssq.json`
  - `snapshot` / `reply` / `run-once`：默认只读取本地缓存
  - `refresh-latest`：开奖后只刷新最新一期到本地
- `refresh-latest` 会依次尝试多条官方链路：
  - 中国福彩网开奖页 HTML
  - 中国福彩网开奖页当前引用的数据源
  - 旧版中国福彩网开奖接口
- 如果这些官方链路都不可用，就保留现有本地缓存，不会退回到每次全量抓历史
- 默认推送时间配置为 `Asia/Shanghai` 时区下的周二、周四、周日 `18:30`
- 默认开奖结果刷新时间配置为 `Asia/Shanghai` 时区下的周二、周四、周日 `21:25`
- 公开仓库默认不包含个人手机号和本地运行态文件；`notification.target` 需要按你的实际手机号自行配置
- 每次真正提供给你的号码都会按期记录到 `state/runtime.json`，开奖后会自动和最新一期开奖号码对比
- 对比消息会说明：开奖号码、每组命中红球/蓝球数量、是否中奖、命中的最高奖级
- 每个购买窗口最多推送一次，去重记录保存在 `state/runtime.json`
- 支持即时取号：当 OpenClaw 收到诸如“双色球”“来1组”“来5组”“当期号码”这类消息时，可调用 `reply --request "<原消息>"` 直接返回推荐号码
- 如果本地 watcher 没跑成功，正确行为是提示暂时无法读取当期推荐，而不是随机编号码
- 输出是娱乐参考，不承诺中奖概率或收益
