# JamMate 实时交互鲁棒性基准

这批用例基于 SPEC 第 4–5 节，复用现有的 `InputEvent`、`StateManager`、Agent 和 `RealtimeSession`。24 个独立会话包含 37 次输入批次。开发集与留出对照各 12 个会话，同一场景家族和仅差一个时间参数的配对都留在同一组。规则基线只复跑了固定用例，未用这批留出标签调整实现。

## 测试内容

| 维度 | 例子 | 主要验收 |
| --- | --- | --- |
| 输入类型和质量 | 中文、英文直接或间接指令；稀疏切分、密集拍点、低对齐置信度、空节奏；带起始休止、重叠或空结果的哼唱 | Strategy 与 Action、目标声部、节奏采用、动机登记及实际输出 |
| 批次与输入间隔 | 同批提交拍手和哼唱，两条事件相隔 150 ms 分开提交，以及等前一份节奏起拍后提交哼唱 | 触发事件完整性、旧候选是否取消、已提交节奏能否保留 |
| 乱序和重复 | 30 ms 内改口但旧结果较慢、重复相同事件、旧采集内容迟到、用户开始新乐句后才收到上一句 | 过期决策是否进队、是否有重复播放回执、迟到素材是否被登记 |
| 交互连续性 | 指代不清后补充声部、同批停奏和继续、停奏期间收到节奏、空闲两小节后再空闲五小节并回应真实格式的哼唱事件 | 等待时保留伴奏，停奏仅由显式开始恢复，主动一次后交回空间 |
| 时间与速度 | 75、96、120 BPM；同一 1.95 秒决策延迟；96 BPM 下决策和准备分别在边界及边界后 1 ms 完成 | 实际起拍的小节、迟到和队列取消、相邻小节样本区间连续 |

用例从第 0 小节播放原有伴奏再接收输入。虚拟时钟为 1 kHz。相同时间点先处理已开始的播放边界，再处理输入，最后处理决策返回和准备完成。JSON 中 `at_ms` 是输入到达时间，`occurred_ms` 可模拟实际事件结束早于送达，`decision_ms` 和 `prepare_ms` 是独立设置的虚拟工时。实际 Agent 调用的墙钟耗时另记为 `agent_wall_ms`。切换结论基于排程后的音符和回执。

低置信或空输入允许合法续奏或明确等待，两种结果都须保持规定的音乐状态。保护声部与未接受候选会对照实际排程音符。回声逐音核对相对起音、音高和时值。稀疏键盘每小节可能只有一组起音，因此验收在四小节窗口比较起音总数。每份已应用计划须恰好有一个起拍回执。

## 运行

在仓库根目录用已安装 JamMate 依赖的 Python 运行。每次选择一个新的输出目录。

```bash
PYTHONPATH=src python examples/jam_robustness/run.py --dry-run
PYTHONPATH=src python examples/jam_robustness/run.py --agent rule --out demo_out/jam_robustness/rule_run
PYTHONPATH=src python examples/jam_robustness/run.py --agent llm --config /path/to/private-config.json \
  --out demo_out/jam_robustness/llm_run
```

`--split dev` 或 `--split holdout` 筛选数据，`--case <id>` 可指定一个或多个场景。加 `--strict` 时任一场景失败都会使命令返回非零退出码。默认输出失败场景及断言名称，便于收集候选模型的全部结果。

- [固定用例与输入条件](cases.json)
- [规则基线汇总](../benchmark_evidence/jam_robustness_20261119/rule_v1/summary.json)
- [规则基线逐会话记录](../benchmark_evidence/jam_robustness_20261119/rule_v1/sessions.jsonl)

规则基线通过 22/24 个会话，开发集 12/12、留出对照 10/12。失败集中在两条中、英文的间接简化指令。保留这两条可量测的失败用于后续对照。逐会话记录保存输入到达和间隔、实际接受事件、计划、截止时间、起拍回执、每小节音符、最终状态与评分。

**范围**。拍手和哼唱为结构化、合成的识别后数据。这批测试覆盖 Agent、状态和虚拟排程。麦克风录音识别、声卡起拍和主观听感需用真实录音及设备单独验收。本次基线调用的是规则 Agent。

## 规则 Agent 与 Jev 的文字决策对照

[24 个固定用例](rule_jev_cases.json) 用同一套虚拟排程、状态提交和音符检查，对照原规则、复用本地解析器的规则、再加 Jev 密度识别的版本。调速、声部开关和段长命中完整本地语法时直接编译计划。单条文字超出规则语法且只涉及一个具名伴奏声部时，可以让 Jev 判断是否需要减少音符密度。原文目标、其他声部的保护条件、两项分类分数和计划校验共同决定能否提交。

在仓库根目录运行，使用新的输出目录。Jev 组选项从环境读取 OHMYGPT_API_KEY。

~~~bash
PYTHONPATH=src:. python examples/jam_robustness/rule_jev.py --dry-run
PYTHONPATH=src:. python examples/jam_robustness/rule_jev.py --out demo_out/jam_robustness/rule_jev_local
PYTHONPATH=src:. python examples/jam_robustness/rule_jev.py --jev --out demo_out/jam_robustness/rule_jev_live
~~~

首轮实验固定 0.90 的 Jev 分数门槛。三组分别通过 10/24、16/24 和 20/24，留出组分别是 7/16、10/16 和 12/16。Jev 返回模型 jev-1.14.0，11 次请求的接口耗时中位数为 869.9 ms。8 个应保持伴奏的用例全部通过。四个漏执行的密度用例均选对动作和声部，动作分数在 0.74–0.86。该门槛是本轮实验条件。

模拟器使用固定决策延时排小节，并另记实际 API 耗时。两个临界用例在 Jev 分数不足时保持伴奏；注入通过校验的候选后分别在第 1、2 小节生效。此脚本使用规则计划承接退出快路径的用例，便于测量分类器的独立增量。
