# 文字创作 Agent 的首批实测与调试记录

2026-11-18 · 5 个独立作品会话，每个模型依次执行 18 轮。作品内延续上一轮的已提交状态，作品之间重新从 80 BPM 开始。[固定输入和逐轮验收条件](../examples/text_benchmark/cases.json)

## 对照结果

| 阶段 | 实际返回的模型 | 通过 | 署名模型输出 | 单轮 Agent 中位耗时 |
| --- | --- | ---: | ---: | ---: |
| 初跑 | `gpt-6-luna` | 17/18 | 18 | 3.65 s |
| 初跑 | `gpt-5.4-mini-2026-03-17` | 18/18 | 18 | 3.48 s |
| 修后回归「速度、力度、疏密」 | `gpt-6-luna` | 5/5 | 5 | 4.08 s |

使用项目已配置的 OpenAI Responses API，推理档位为 `low`。两个模型都走文字页面的服务入口、实际的 BandPlan 校验与状态提交，以及规则音符生成和 Kevin / FluidSynth。表中的延迟取 Agent 阶段。首批两个模型共 36 次实际模型响应，回归另有 5 次。每个模型仅执行了一遍全套固定输入，两者的单次通过率和中位耗时只能用于定位这批用例。

| 会话 | 测试内容 | Luna 初跑 | Mini 初跑 | 执行证据 |
| --- | --- | ---: | ---: | --- |
| 新增声部 | 开场提鼓、加乐器并提速、按指代静音、按指代恢复、请求新增小提琴 | 5/5 | 5/5 | 木吉他在 MIDI 中出现、消失、恢复，两次加入都维持了原有声部。小提琴请求得到能力说明 |
| 速度、力度、疏密 | 指定 110 BPM、只提鼓力度、只减少鼓点、恢复初始力度、原样继续 | 4/5 | 5/5 | 两边都增大鼓的力度且保持打点，再减少鼓点并单独恢复力度。Luna 初跑的「原样继续」被后端拦截 |
| 停奏与恢复 | 只停鼓、全队停、保持停奏、恢复除鼓以外的声部 | 4/4 | 4/4 | 检查了播放状态及鼓、贝斯、键盘的 MIDI 音符 |
| 旋律与部分执行 | 生成短旋律、原样复奏、提至 96 BPM 并请求换 C 小调 | 3/3 | 3/3 | 短旋律登记后能在下一轮按相同的 MIDI 音高和起点复奏。提速成功，改调留在未完成项 |
| 模糊指代 | 无历史时说「把那个再调一点」 | 1/1 | 1/1 | 两个模型均请求明确对象，原有音乐状态保持不变 |

## 发现与改动

1. **合法的空计划被当成了失败。** 输入「这段不错，就这样接着演一段，什么都不用改。」时，Luna 回复「按当前状态继续演奏四小节，不做任何调整。」它给出 `FOLLOW`、空 `actions`、空 `transport` 和空 `unresolved`。Agent 校验接受了这个计划，文字页面后端却因为没有改动项拒绝生成。Mini 提案中重填了现有的播放状态和 110 BPM，因此能继续生成。

   后端现在允许无未解决项的空计划继续生成，空计划里存在未解决项时保留原有提示和拦截。修后 Luna 再次返回空计划，生成并提交了新小节。自动化回归还验证了含未解决项的请求会被拦截，之后可以正常提交新的指令。[后端](../scripts/reference_worker.py) · [回归测试](../tests/test_reference_bridge.py)
2. **浮点存储差异造成评分误报。** 修后回归中，Luna 从鼓的 `0.7` 用 `level_delta=-0.2` 恢复初始音量，状态实际存为 `0.49999999999999994`。鼓点疏密、BPM 和其余声部均正确。评分器此前用精确相等比较 `0.5`，现在归一化音量和疏密的等值检查采用 `1e-9` 绝对容差。对原始回归记录重新评分得到 5/5，模型输出和音乐产物沿用原记录。[评分器](../examples/text_benchmark/run.py)

## 后续想验证的功能

| 希望支持的用户指令 | 当前观察 | 可以怎样实现和验收 |
| --- | --- | --- |
| 「加入小提琴长音，吉他继续弹」 | 两个模型都知道当前无法新增小提琴 | 让 Agent 从支持的乐器目录选型，在 JamState 注册独立声部。用当下和弦生成小提琴持续音，分配独立 MIDI 通道和音色。验收新增、保留吉他、后续单独静音小提琴三个动作 |
| 「下一段换成 C 小调，速度升到 96」 | 两个模型都提速并报告改调未完成 | 在 BandPlan 定义带主音、调式和生效小节的和声动作。提交后用新调生成后续和弦、贝斯与吉他，定义历史主旋律是否随调移调的明确规则。验收目标调式、BPM、生效边界和上一段不被重写 |
| 「把刚才那个再调一点」 | 两个模型都要求明确对象。初跑中 Luna 返回提示，Mini 还生成了一段不变的伴奏 | 让未指明目标的请求返回明确的待确认状态。保留当前试听与小节位置，并记录待确认对象。用户补充「只改鼓」后，再校验对鼓的改动和其他声部的保留 |
| 「鼓晚一点落点，速度和力度别动」 | 本批测试覆盖整轨力度和疏密 | 加入开启和关闭 LLM 音符编排的对照。解码 MIDI 检查目标落点的位移量、未触及的节拍锚点和其他声部 |
| 「恢复到第三段时的贝斯，但吉他保持现在这样」 | 本批已验证短历史里的力度及静音恢复 | 测试超出近期上下文的引用、同名声部以及导入旧会话。用历史已提交状态或段落 ID 定位来源，验证只恢复被指明的属性 |
| 「更快一点，BPM 又不要变」 | 本批未覆盖相互冲突的要求 | 加入冲突和否定改写用例。验证澄清时不推进小节，并确保后续明确确认后只执行一次 |
| 「只改这一小节，下一小节恢复」 | 本批验证的是跨轮持续修改 | 给临时效果定义结束小节与恢复条件。验收变化和恢复的位置、重试不重复应用、用户中途改口的优先级 |

## 复跑和查阅记录

在仓库根目录执行，`<FluidSynth路径>` 使用本机程序路径，每次选用新的输出目录。

```text
python examples/text_benchmark/run.py --model gpt-6-luna --fluidsynth "<FluidSynth路径>" --output demo_out/text_benchmark/new_run
python examples/text_benchmark/run.py --model gpt-6-luna --scenario tempo_level_density --fluidsynth "<FluidSynth路径>" --output demo_out/text_benchmark/regression_run
python examples/text_benchmark/run.py --rescore demo_out/text_benchmark/regression_run --output demo_out/text_benchmark/rescored
```

每个输出目录包含 `summary.json` 和逐轮追加的 `turns.jsonl`。每一行带原始文字、每次有内容的模型回复、调用轨迹、最终计划、前后状态、执行结果、MIDI 音符与起点、WAV 摘要，以及失败的断言。`--rescore` 读取原记录并写入新目录，适合单独修评分标准。

- [Luna 初跑逐轮日志](../examples/benchmark_evidence/text_benchmark_20261118/luna_baseline/turns.jsonl) · [汇总](../examples/benchmark_evidence/text_benchmark_20261118/luna_baseline/summary.json)
- [Mini 初跑逐轮日志](../examples/benchmark_evidence/text_benchmark_20261118/mini_baseline/turns.jsonl) · [汇总](../examples/benchmark_evidence/text_benchmark_20261118/mini_baseline/summary.json)
- [Luna 修后原始回归日志](../examples/benchmark_evidence/text_benchmark_20261118/luna_after_empty_plan_fix/turns.jsonl) · [重新评分](../examples/benchmark_evidence/text_benchmark_20261118/luna_after_fix_rescored/summary.json)

能力边界用例的通过条件包含准确告知限制和保持原有音乐状态。基准检查数字产物和状态，试听音质由后续人工验收。
