---
name: ssq-watcher
description: 在双色球购买窗口内抓取最近开奖数据，基于本地规则与 LLM 排序生成 1 组或 5 组参考号码，并通过 OpenClaw 的 iMessage 通道主动推送。
metadata: {"openclaw":{"requires":{"bins":["python3","openclaw"]}}}
---

# SSQ Watcher

这个 skill 的实际代码位于独立项目目录中。优先通过 `SSQ_AGENT_HOME` 或项目根目录相对路径调用，不要依赖固定绝对路径。默认推荐返回 `5组`，如果用户明确提到 `1组` 再切换数量。

## 常用命令

```bash
cd "$SSQ_AGENT_HOME"
python3 ./scripts/ssq_agent.py bootstrap-history
python3 ./scripts/ssq_agent.py refresh-latest
python3 ./scripts/ssq_agent.py compare-latest
python3 ./scripts/ssq_agent.py snapshot
python3 ./scripts/ssq_agent.py snapshot --count 5
python3 ./scripts/ssq_agent.py reply --request "来5组双色球"
python3 ./scripts/ssq_agent.py reply --request "来1组当期号码"
python3 ./scripts/ssq_agent.py run-once --send --dry-run
python3 ./scripts/ssq_agent.py send-test
python3 ./scripts/ssq_agent.py install-cron --agent-id ssq-watcher
```

## 路径

- 建议先设置：`SSQ_AGENT_HOME=/path/to/lottery-invest-agent`
- 配置文件：`$SSQ_AGENT_HOME/config.json`
- 状态文件：`$SSQ_AGENT_HOME/state/runtime.json`

## 说明

- 先基于本地脚本输出结果，不要凭空编造号码。
- 历史数据默认走本地缓存；首次初始化时抓一次历史开奖，后续只在开奖后刷新最新一期。
- 刷新最新一期时，会依次尝试多条官方链路；如果全部失败，就保持现有本地缓存不变。
- 每次真正提供给用户的号码会按期记录；开奖后刷新成功后，会自动生成对比结果并通过 iMessage 发回。
- 开奖对比消息会包含：开奖号码、每组命中红球/蓝球数量、奖级分布、最高奖级、固定奖预计金额。
- 候选过滤和去重由本地脚本完成，保持稳定和低 token 成本。
- LLM 只负责在候选集合中做排序和简短解释，不宣称高中奖率或收益承诺。
- 主动提醒通过 `openclaw message send --channel imessage --target <handle> --message "..."` 触发。
- 如果用户通过消息临时来要号码，优先运行 `python3 ./scripts/ssq_agent.py reply --request "<用户原话>"`，根据消息里的“1组/5组”自动决定返回数量。
- 如果本地脚本没成功执行，就明确说当前暂时无法读取当期推荐；不要自己编造号码。
