# JamMate 文字创作测试结果

测试日期为 2026 年 11 月 18 日。本文汇总文字创作的多轮功能基准、提示写法对照、早期风格摸底，以及已经定位的实现问题。各轮均通过文字创作服务调用 Agent，由规则编排生成音符，经 Kevin / FluidSynth 输出。验收依据为 Agent 的结构化计划、执行后的音乐状态和实际 MIDI/WAV。

## 总览

| 批次 | 实测模型 | 结果 |
| --- | --- | --- |
| 18 条多轮功能基准 | GPT-6 Luna、GPT-5.4 Mini | Luna 初跑 17/18，Mini 18/18 |
| 功能修复后定向回归 | GPT-6 Luna | 「速度、力度、疏密」5/5。复用这次实测的日志重新评分，未额外调用模型 |
| 两个新题材 × 四种提示写法 | GPT-6 Luna | 8/8 输出 MIDI 和 WAV。纸鸢的按声部指挥有一项未完成，旋律结束处的留白长度无法精确指定 |
| 早期风格摸底 | GPT-6 Luna、GPT-5.4 Mini | 16 轮中 14 轮输出 MIDI/WAV。「阴森地狱」两次均无音频，原因各异 |

三组测试和一次定向回归共保留 **65 条逐轮记录、71 次模型回复**。其中包括校验失败后返回的修正提案。通过率按各批次自己的验收条件计算。开放式用例的主要证据是实际采取的编排动作和生成文件，风格贴合度留给试听评估。

## 1. 多轮功能基准

五组测试各自启动一个作品，同组内逐轮沿用状态。下表列出每条输入的验收结果。原文和完整断言可直接复用。[18 条完整输入](../examples/text_benchmark/cases.json) · [功能基准与调试细节](TEXT_AGENT_BENCHMARK.md)

| 输入意图 | Luna 初跑 | Mini 初跑 | 实际观察 |
| --- | --- | --- | --- |
| 鼓更有力，速度别动 | 通过 | 通过 | 鼓力度提高，速度保持 |
| 稍微快一点，加一个其他乐器 | 通过 | 通过 | 80 → 84 BPM，MIDI 出现木吉他声部 |
| 停掉刚加进来的那个乐器 | 通过 | 通过 | 指代解析到木吉他，鼓、贝斯、键盘继续 |
| 把刚才关掉的加回来 | 通过 | 通过 | 木吉他恢复 |
| 保留现有编制，再加一把小提琴 | 能力边界通过 | 能力边界通过 | 反馈小提琴暂未支持，没有替换现有乐器 |
| 速度设为 110 BPM | 通过 | 通过 | 达到目标速度，声部设置保持 |
| 鼓更有冲击力，节奏型和速度保持 | 通过 | 通过 | 鼓力度上升，鼓点起点数和速度保持 |
| 鼓打稀疏些，力度和速度保持 | 通过 | 通过 | 鼓打点减少，力度设置与速度保持 |
| 鼓音量恢复到一开始，保留稀疏节奏 | 通过 | 通过 | 初始力度恢复，鼓点疏密和速度保持 |
| 就这样接着演一段 | 后端拦截，修复后通过 | 通过 | Luna 的空改动计划已经有效，修复后能继续生成 |
| 停的是鼓，贝斯和键盘继续 | 通过 | 通过 | 只有鼓静音 |
| 全队停下来 | 通过 | 通过 | 播放状态关闭，MIDI 无音符 |
| 保持停奏，先别开始 | 通过 | 通过 | 保持停奏，MIDI 无音符 |
| 乐队继续，鼓继续休息 | 通过 | 通过 | 贝斯和键盘恢复，鼓仍静音 |
| 先来一句短主旋律 | 通过 | 通过 | 生成并登记了主旋律 |
| 原样再弹一遍主旋律 | 通过 | 通过 | MIDI 音高和起点与上一轮匹配 |
| 速度升到 96 BPM，再换成 C 小调 | 部分执行通过 | 部分执行通过 | 提速完成，反馈改调未完成 |
| 把那个再调一点，其他别动 | 澄清通过 | 澄清通过 | 两者都问清对象，保持音乐状态。Mini 同时生成了一段原样伴奏 |

### 已修复的问题

- **空改动计划无法继续生成。** Luna 把「就这样接着演」输出成空 `actions`、空 `transport`、空 `unresolved`。Agent 接受了该计划，文字服务却将它误判为无法执行。服务现在允许这种计划继续渲染。含未完成项的空计划保留原有反馈。修复后用 Luna 重跑，原样续段通过。[后端改动](../scripts/reference_worker.py) · [自动化回归](../tests/test_reference_bridge.py)
- **恢复音量的评分误报。** 回归时 `0.7 - 0.2` 存成 `0.49999999999999994`。评分原本要求精确等于 `0.5`，现对归一化音量和疏密采用 `1e-9` 的绝对容差。保留原始输出，重新评分后该组为 5/5。[原回归日志](../examples/benchmark_evidence/text_benchmark_20261118/luna_after_empty_plan_fix/turns.jsonl) · [重评分汇总](../examples/benchmark_evidence/text_benchmark_20261118/luna_after_fix_rescored/summary.json)

## 2. 提示写法对照

使用自拟的「午后纸鸢」和「末班车后的空站台」。每条从相同的 96 BPM、C 大调和默认编制出发，各生成四小节。对照主要看提示具体触发了什么编排变化。[8 条完整输入](../examples/text_benchmark/prompt_methods.json) · [逐轮分析和全部试听](TEXT_PROMPT_METHODS.md)

| 提示写法 | 午后纸鸢 | 凌晨空站台 |
| --- | --- | --- |
| 情绪短句 | 「轻巧、疏朗、俏皮」使伴奏由 44 个音符降至 33 个，未加入旋律 | 「安静、空旷、慢慢逼近」使全队变轻、变稀，贝斯力度没有渐强 |
| 纯场景描写 | 纸尾在风停时抖动，实际加入四个主奏音，伴奏照旧 | 隧道深处有东西慢慢靠近，实际加入三个主奏音，贝斯力度保持。 |
| 目标加反例 | 「别做成游行，留空隙给短念头」同时触发伴奏变稀和短旋律。 | 「别靠加速或重鼓，压力从低处慢慢起来」触发贝斯逐小节渐强，MIDI 平均力度 102、109、116、124。 |
| 按声部指挥 | 鼓从 24 个音减到 20 个，原力度设置保持，加入四个主奏音。尾部留白长度仍无法精确指定 | 「鼓轻一点，贝斯四小节逐步变响，其他保持」全部执行。贝斯 MIDI 平均力度 105、114、123、127。 |

两次纯场景描写都触发了短旋律。加入「音乐怎么变」和「哪种处理不合适」后，Luna 分别落实了为旋律留空、让低音渐强这两个意图。按声部指挥适合核对确切的动作和执行结果。

## 3. 早期风格摸底

这批包括 5 条意象、1 条中性对照和 2 条带明确约束的变体。两种模型各跑 8 条。表中是五种意象触发的实际变化。[完整输入](../examples/benchmark_evidence/text_benchmark_20261118/style/cases.json)

| 意象 | GPT-6 Luna | GPT-5.4 Mini |
| --- | --- | --- |
| 诗情画意 | 简化全队 | 简化全队 |
| 烟雨江南 | 简化全队 | 新增短旋律 |
| 阴森地狱 | 告知音色和和声限制，未生成片段 | 两次提案同时要求增强和保留同一声部，被校验拒绝，未生成片段 |
| 东南亚雨后海岛集市 | 新增短旋律，同时说明地域风格无法直接指定 | 新增短旋律 |
| 北欧雪原 | 简化全队、增强键盘、加入短旋律，同时说明调式和音色限制 | 简化全队 |

带明确约束的江南变体里，两种模型都减少鼓点并加入短旋律。带明确约束的北欧变体里，两种模型都改到 72 BPM，并把鼓调轻、调稀。Luna 还反馈无法直接设定「冷冽」音色。这些输出验证了可控编排动作，地域风格没有做听感评定。

## 4. 待验证能力与验收方式

| 待验证能力 | 实现与验收 |
| --- | --- |
| 新增木吉他之外的乐器 | 让 Agent 从可用乐器目录选择，与 JamState 的独立声部、生成器和 MIDI 音色对应。验证新轨出音，后续能单独静音与恢复 |
| 按段换调或换调式 | 用 BandPlan 表达目标调、调式和生效小节。核对新段的和声及音符，并确认上一段的产物未被重写 |
| 模糊指代时先澄清 | 添加一个明确的待确认响应，让用户补充指令。验收等待澄清期间不推进新小节 |
| 精确控制短旋律的留白 | 给短句指定结束边界，检查 MIDI 中最后一个主奏音的结束时刻 |
| 减少计划中的声部保护冲突 | 明确指出哪条动作与哪个保留项冲突，重测「阴森地狱」场景中的模型修正行为 |

## 5. 结果文件与复跑

| 批次 | 原始记录 | 汇总 |
| --- | --- | --- |
| Luna 功能初跑 | [逐轮 JSONL](../examples/benchmark_evidence/text_benchmark_20261118/luna_baseline/turns.jsonl) | [JSON](../examples/benchmark_evidence/text_benchmark_20261118/luna_baseline/summary.json) |
| Mini 功能初跑 | [逐轮 JSONL](../examples/benchmark_evidence/text_benchmark_20261118/mini_baseline/turns.jsonl) | [JSON](../examples/benchmark_evidence/text_benchmark_20261118/mini_baseline/summary.json) |
| Luna 修后定向回归 | [逐轮 JSONL](../examples/benchmark_evidence/text_benchmark_20261118/luna_after_empty_plan_fix/turns.jsonl) | [重评分 JSON](../examples/benchmark_evidence/text_benchmark_20261118/luna_after_fix_rescored/summary.json) |
| Luna 提示写法对照 | [逐轮 JSONL](../examples/benchmark_evidence/text_benchmark_20261118/prompt_methods/luna/turns.jsonl) | [JSON](../examples/benchmark_evidence/text_benchmark_20261118/prompt_methods/luna/summary.json) |
| Luna 风格摸底 | [逐轮 JSONL](../examples/benchmark_evidence/text_benchmark_20261118/style/luna/turns.jsonl) | [JSON](../examples/benchmark_evidence/text_benchmark_20261118/style/luna/summary.json) |
| Mini 风格摸底 | [逐轮 JSONL](../examples/benchmark_evidence/text_benchmark_20261118/style/mini/turns.jsonl) | [JSON](../examples/benchmark_evidence/text_benchmark_20261118/style/mini/summary.json) |

JSONL 每条包含原输入、各次模型回复、计划校验、执行前后状态、MIDI/WAV 摘要与断言。风格摸底和提示写法对照的成功轮另有 WAV、MIDI 文件，日志的 `artifacts` 字段记录了相对路径。原始产物保存在项目本机的 `demo_out/text_benchmark_20261118`，该目录被 Git 忽略。共享原始产物时可以将该目录和本文一起提供。[测试运行器](../examples/text_benchmark/run.py)

在项目根目录可使用已有的 API 配置复跑，输出目录需要使用新路径。

```text
python examples/text_benchmark/run.py --model gpt-6-luna --fluidsynth "<FluidSynth路径>" --output demo_out/text_benchmark/functional_new
python examples/text_benchmark/run.py --cases examples/text_benchmark/prompt_methods.json --model gpt-6-luna --save-audio --fluidsynth "<FluidSynth路径>" --output demo_out/text_benchmark/prompt_methods_new
```

逐轮记录可随仓库查看。试听音频可按提示词对照的命令本地重生成。
