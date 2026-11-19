# 文字创作基准扩展

这批新增 [62 条文本输入](extended_cases.json)，分为 14 个会话。每组从 96 BPM 和默认编制开始，组内按顺序延续历史和音乐状态。用例包含中文口语、英语、中英夹杂、改口、否定、指代和带明确约束的画面描述。原始文本已与项目已有的功能用例、提示写法用例和端到端验收用例排查重复。

9 组回归用例共 37 轮，覆盖当前契约可以表达的操作。5 组边界诊断共 25 轮，其中 15 轮建立对照状态或检查后续恢复，另外 10 轮检查部分执行、能力限制和澄清。各组的 `tier` 标记供报告筛选。

| 会话 | 轮数 | 重点 |
| --- | ---: | --- |
| `tempo_revision` | 4 | 同一句里改口、相对 BPM、恢复起始速度 |
| `tempo_bounds` | 4 | 60 和 180 BPM 边界、203 BPM 不能静默替换、下一轮正常恢复 |
| `negation_and_mix` | 4 | 区分力度和打点、按声部静音、延续时保持静音 |
| `selective_history_restore` | 5 | 找回指定历史力度，保留后来改的密度和其他声部状态 |
| `cross_language_reference` | 4 | 英文指令、中英夹杂、跨语言指代和撤回 |
| `segment_lengths` | 4 | 依次生成 1、2、8、3 小节，核对计划与下一小节位置 |
| `select_nonrecent_motif` | 4 | 先建立 A、B 两句，再按名字分别复奏；与指定原句的 MIDI 音高和音符起点比对 |
| `stopped_state_and_tempo` | 4 | 停奏期间预设 BPM，恢复时沿用静音声部与新速度 |
| `guitar_program_and_composition` | 4 | 新增木吉他、同轮静音吉他并引入主旋律、恢复同一吉他、只改该轨力度 |
| `separate_supported_from_unavailable` | 4 | 在同一句里执行可用操作，报告小号、手风琴或换调的限制 |
| `clarification_repair` | 4 | 两个可能对象、解释指代后的实际改动、遇到冲突时等待且不推进小节 |
| `bounded_history_lookup` | 9 | 把命名段落移出六轮可见历史窗口，要求找不到时询问具体速度 |
| `imagery_with_objective_direction` | 4 | 旧电梯、开门的光、空档案室和余响，用可测的声部改动验收 |
| `specific_control_limits` | 4 | 单独控制底鼓、恢复鼓点密度和添加混响的能力边界 |

例如，在旋律 A/B 组，验收会同时检查复奏后的 `motif` ID 和该轮导出的实际音符。复奏了最近一句 B，即便生成正常、音符数和 A 一样，也会因为引用或音高起点不匹配而失败。在澄清组，`next_bar` 需要和请求前一样，能捕捉模型正确提出问题但系统仍生成了一段的情况。

## 运行

使用已有的 provider 配置与 FluidSynth，整个批次可直接传入已有 [运行器](run.py)。

```text
python examples/text_benchmark/run.py --cases examples/text_benchmark/extended_cases.json --model gpt-6-luna --fluidsynth "<FluidSynth路径>" --output demo_out/text_benchmark/extended_luna
```

先做小批试跑时，可以重复传入 `--scenario`，由运行器按用例顺序执行所选会话。加入 `--save-audio` 时会一并保留 WAV 与 MIDI。

```text
python examples/text_benchmark/run.py --cases examples/text_benchmark/extended_cases.json --model gpt-6-luna --scenario select_nonrecent_motif --scenario clarification_repair --fluidsynth "<FluidSynth路径>" --output demo_out/text_benchmark/extended_smoke_luna
```

逐轮输出使用 `turns.jsonl`，评分汇总使用 `summary.json`。断言可引用 `before.<字段>`、`previous.<字段>` 和同组早先的 `turn.<用例ID>.<字段>`。其中 `previous` 指上一份成功产生的媒体，`turn` 指指定轮次。木吉他的 General MIDI 程序号使用从 0 开始的编号 25。

本批目前是预先定义的测试数据。结构校验和离线验证的范围独立于模型通过率，Luna 与 Mini 在扩展批次上的实测分数待记录。

已离线执行四组共 21 轮的参考计划，验证片段长度、跨轮复奏、吉他声部与可见历史边界。检查了错误旋律、错误 MIDI 音色和澄清时推进小节均会被评分拒绝。已有 Luna、Mini 与提示写法日志重新评分后保留原分数。
