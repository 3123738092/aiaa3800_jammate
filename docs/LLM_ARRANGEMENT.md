# LLM 辅助音符编排

Agent 根据 InputEvent 和 JamState 选择 BandPlan。执行端先用规则编排器生成完整的音符，再按需调用独立的 LLM 编排步骤。规则 Agent 和 LLM Agent 都可以搭配两种编排方式。模型预检只走本地代码，正式执行时每段最多调用一次编排模型。

LLM 在 BandPlan 授权的鼓、贝斯、键盘声部内编辑现有音符组。每组是同一声部、同一落点的一组音符，和弦会整体编辑。它可以按动作调整 MIDI 力度、前后挪动 0.125 拍、改变时值或删减指定音符组。音高、和声、主旋律和全局速度仍来自计划与规则编排。`BUILD` 允许增加力度，`SIMPLIFY` 允许继续删减，`ADJUST_FEEL` 和 `ADD_LAYER` 在授权声部提供更多编辑选择。

程序会校验模型选中的音符组和操作、受保护声部和节奏锚点、音符边界以及同音重叠。仅保留、停奏或没有可编辑音符的计划会跳过模型。API 超时或编辑被拒绝时，默认整段使用规则音符，日志标记为 `fallback`。命令行可以用 `--arrangement-fallback error` 检查失败路径。

编辑当前应用于这一段的实际音符。后续段落从已提交的 JamState 和规则音符重新开始，模型编辑本身没有作为可重复的乐段写入共享状态。MRT2 照常按试听能力出回执，音符条件中的力度调整仍可能无法传到 MRT2 音频。

## 使用

网页文字入口中，在「高级设置 → 音符编排」选择「LLM 辅助编排」。初始设置和现有的 Agent、起始 BPM 一样，开始新作品时选择。可先试「鼓更猛一点」，再展开结果下方的「音符编排日志」查看本轮模型版本、耗时、编辑、状态和失败原因。

也可用规则 Agent 隔离验证编排步骤。API 配置沿用 `JAMMATE_LLM_CONFIG` 或 `--config`。

```bash
python examples/agent_demo.py --agent rule --arranger llm --text '鼓更猛一点' --out demo_out/arrangement_demo
```

改成 `--agent llm --arranger llm` 会分别调用决策模型和编排模型。决策模型知道本轮有音符编排器，可用现有 `ADJUST_FEEL` 动作授权局部编辑，原文由编排模型读入，实际动作以编排结果为准。默认编排仍为 `--arranger rule`。NumPy、Kevin/FluidSynth 以及显式的 MRT2 试听共用编排入口，正式渲染前会重新检查计划是否已被新输入取消。

## 日志与复现

网页会话写入 `demo_out/arrangement_logs/<session_hash>.jsonl`。CLI 写入输出目录的 `trace.jsonl`，每段另有 `arrangement.json`、MIDI 和 WAV。用 `plan_id` 对齐同一轮记录。

| 日志事件 | 用来检查什么 |
|---|---|
| `arrangement_request` | 模型收到的 BandPlan、输入事件和可编辑音符组。字段 `context` 是发送时用的 JSON 字符串。 |
| `arrangement_model_output` | 模型版本、token 用量与原始结构化回复。过长回复保留开头并标记截断。 |
| `arrangement_result` | 是否采纳、跳过或回退，原因和改动内容，以及实际交给渲染器的音符。 |
| `execution_receipt` | 对照渲染和状态提交是否成功。 |

日志保存在本机 Git 忽略目录，包含用户文字和音乐素材，请按录音和输入数据的方式管理。日志记录的模型说明是结构化回复中的简短音乐说明。API 凭据和 provider 异常正文不在这些记录中。

最小差异对照固定 JamState 和 BandPlan，再改变请求里一个数字或词，直接读回 MIDI 验证。真实和模拟结果、运行方式见 [编排 benchmark](ARRANGEMENT_BENCHMARK.md)。

让模型直接生成一整个声部的实验见 [LLM 直接编排贝斯](LLM_BASS_GENERATION.md)。
