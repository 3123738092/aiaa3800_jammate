# Jev 在已有文字基准上的旁路实验

2026-11-19。输入沿用三个现有 benchmark 的原文和顺序，共 27 个会话、88 轮。Jev 通过 OhMyGPT 调用，176 次分类请求均返回 `jev-1.14.0`。它只生成旁路分类日志和候选操作。主流程使用原 Agent、BandPlan、Kevin / FluidSynth 与状态提交结果。

## 实验条件

| 数据 | 轮数 | 主流程记录 |
| --- | ---: | --- |
| [首批文字基准](../examples/text_benchmark/cases.json) | 18 | 已有 GPT-5.4 Mini 逐轮结果，18/18 |
| [扩展文字基准](../examples/text_benchmark/extended_cases.json) | 62 | 这次新跑 GPT-5.4 Mini，56/62。按独立会话分三个批次，组内逐轮执行 |
| [提示词写法对照](../examples/text_benchmark/prompt_methods.json) | 8 | 已有 GPT-6 Luna 逐轮结果，7/8 |

每轮成对调用。A 只提供原文，B 在相同的提问和候选下，额外提供当轮之前的精简音乐状态及最多六条本会话历史。历史包含先前请求、计划动作、是否提交以及提交前后的状态。当前轮的 Agent 决策、实际产物和验收标签只用于本地比较。

两组都询问整轮路由、速度语义及原文数字候选、目标声部、小节数、播放控制和六类独立操作。独立操作包括停用声部、启用声部、新增声部、力度、疏密与动机。路由标签是本次预定的快路径范围。全字段命中只要求该轮有标注的字段全部正确。速度、小节数与声部的标注来自原有验收或明确的文字语义，操作指标只统计原基准明确要求的正例。

## 分类结果

| 指标 | 只读文本 | 文本 + 状态和历史 |
| --- | ---: | ---: |
| 整轮路由正确 | 59/88 | 64/88 |
| 本轮所有标注字段正确 | 49/88 | 59/88 |
| 速度指令和数字候选 | 19/20 | 20/20 |
| 目标声部范围 | 49/59 | 54/59 |
| 小节数 | 5/5 | 5/5 |
| 全局播放控制 | 6/6 | 6/6 |
| 已标注的必要操作 | 27/28 | 28/28 |
| 单次 Jev 请求 p50 / p95 | 854 / 919 ms | 857 / 954 ms |

成对看，43 轮两个条件都对，23 轮两个条件都错。加上下文后 16 轮转为正确，也有 6 轮从正确转为错误。61 个后续轮里，14 轮转为正确，6 轮转为错误。8 轮提示词写法对照在两种条件下都被交给完整 Agent。

## 快路径试放行

| 条件 | Jev 选快路径 | 路由超出预定快路径范围 | 本轮所有标注字段正确 |
| --- | ---: | ---: | ---: |
| 只读文本，无置信度门槛 | 19 | 0 | 18 |
| 加上下文，无置信度门槛 | 43 | 11 | 30 |
| 加上下文，路由置信度 ≥ 0.80 | 15 | 2 | 13 |
| 加上下文，路由置信度 ≥ 0.90 | 9 | 1 | 8 |
| 加上下文，路由置信度 ≥ 0.95 | 6 | 0 | 6 |

上下文提高了候选覆盖率，也让 Jev 对一些历史引用判断得过于积极。例如「把刚才关掉的加回来」被以约 0.91 的置信度选为快路径。本次策略将这种隐式历史引用留给完整 Agent，因为快路径尚未实现绑定到唯一历史声部的执行器。「恢复鼓点次数到默认密度」也被选为快路径，但当前执行器只支持降低密度。

已有文字基准里的几个边界值得继续验收：

- 「设为 203 BPM，超范围就别往下生成」在两种旁路条件下都被识别为不支持。独立主流程仍生成并推进了 4 小节，速度没有变成 203 BPM。
- 「速度改到 112 BPM，再加小号」在两种条件下都交给完整 Agent，并正确选出 112 的候选。全句不能只用调速候选执行，否则会丢掉小号的能力反馈。
- 「从以前叫『银线』的段落找回速度」在两个条件下都被送往完整 Agent，预期是先询问速度。模型拿到的是最近六轮记录，无法看到已离开窗口的命名段落。

旁路确认了速度、声部、段长和部分操作可以结构化。生产放行仍需要完整候选、参数边界和状态校验，以及对历史引用和其他分句的覆盖检查。现有 88 轮用于适配和诊断，阈值应在另留数据上验证。约 0.85 秒是网关分类请求耗时。扩展组原 Agent 阶段中位数为 3.51 秒。旁路未执行它的候选，不能据此报告替换后的端到端加速。

## 日志与复现

- [旁路逐轮 JSONL](../examples/benchmark_evidence/jev_text_shadow_20261119/shadow/turns.jsonl) · [汇总](../examples/benchmark_evidence/jev_text_shadow_20261119/shadow/summary.json)
- 扩展主流程的 [第 1 批](../examples/benchmark_evidence/jev_text_shadow_20261119/mainline_extended_1/summary.json)、[第 2 批](../examples/benchmark_evidence/jev_text_shadow_20261119/mainline_extended_2/summary.json)、[第 3 批](../examples/benchmark_evidence/jev_text_shadow_20261119/mainline_extended_3/summary.json)
- 日志含原文、可见状态及历史、每道题的选择与概率、候选操作、耗时和主流程对照。汇总记录输入与原始追踪文件的 SHA-256。

在仓库根目录执行，设置环境变量 `OHMYGPT_API_KEY`，并使用新输出目录。

```bash
python examples/text_benchmark/jev_shadow.py --jobs 6 \
  --trace examples/benchmark_evidence/text_benchmark_20261118/mini_baseline/turns.jsonl \
  --trace examples/benchmark_evidence/text_benchmark_20261118/prompt_methods/luna/turns.jsonl \
  --trace examples/benchmark_evidence/jev_text_shadow_20261119/mainline_extended_1/turns.jsonl \
  --trace examples/benchmark_evidence/jev_text_shadow_20261119/mainline_extended_2/turns.jsonl \
  --trace examples/benchmark_evidence/jev_text_shadow_20261119/mainline_extended_3/turns.jsonl \
  --output demo_out/jev_text_shadow_replay
```

使用 `--dry-run` 可以只检查原文与对照日志是否逐轮匹配。
