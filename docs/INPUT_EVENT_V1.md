# InputEvent v1：下游接收契约

日期：2026-10-31。状态：**已实现的下游接收基线**，供 Wenhan 对接和 Wyman 独立开发 Agent。它不表示实时输入、Agent、State Manager 和 Renderer 已全部完成集成。

机器可读定义：[input_event.schema.json](../src/jammate/contracts/input_event.schema.json)。Python 校验：[input_event.py](../src/jammate/contracts/input_event.py)。转换入口：[adapters.py](../src/jammate/contracts/adapters.py)。

本文定义 InputEvent 边界。后续已实现 [JamState / BandPlan / State Manager](AGENT_V1.md)，并接上 [LLM Agent](LLM_AGENT.md)：原文与音乐证据直接进入模型，额外提供状态和最近执行历史，未先压成关键词 Intent。旧 `rhythm`、`rhythm-audio`、`offline`、`live` 命令保持原行为，尚未迁移到新闭环。

## 1. 已确定的公共外层

| 字段 | 类型 | 规则 |
|---|---|---|
| `schema_version` | string | 精确接受 `1.0.0` / `1.1.0`；1.1 增加 system 生命周期，原三类文件仍可用 1.0 |
| `event_id` | string | 非空；同一次事件的传输重试保留 ID；重新提交/重新识别形成新事件时使用新 ID |
| `session_id` | string | 非空；由会话创建者提供，不从文件名或系统时间猜测 |
| `source` | enum | `text`、`speech`、`rhythm`、`humming`；1.1 新增 `system` |
| `event_type` | enum | `speech`、`rhythm_seed`、`musical_phrase`；1.1 新增 `session_control` |
| `started_at` / `ended_at` | number 或 null | 相对会话开始的秒数；必须同时已知或同时为 null，结束不得早于开始 |
| `confidence` | 0–1 或 null | 事件级、由 producer 定义的评分；未知填 null。提供数值时必须同时提供 `provenance.confidence_method` |
| `payload` | object | 根据类型选择固定结构，见下文 |
| `provenance` | object，可选 | producer、上游来源 ID/版本、音频/分析文档引用、评分方法；引用只是标识，不会被校验器自动打开或访问 |

前三种输入的公共字段和 payload 都拒绝未知字段，避免拼写错误和提前加入 `action`/`intent` 等决策内容。唯一明确的开放边界是保留的上游 Melody 文档，它可以携带模型、质量、处理参数和其他原始 JSON 元数据。

`event_type=speech` 沿用 SPEC 的语言事件类别。为区分现有控制台打字和 ASR，增加 `source=text`；识别出的语音使用 `source=speech`，两者 payload 相同。`instrument` 尚未支持。`system/session_control` 必须用 1.1.0，不能向 1.0.0 塞入新类型。

1.1 的 `payload.command` 为 start / stop / user_activity / idle；只有 user_activity 携带且必须携带 active 布尔值，只有 idle 携带且必须携带 bars 非负整数。生命周期事件表示一个时刻，started_at=ended_at，或同时为 null。使用 `from_system(...)` 包装，示例与执行语义见 [生命周期输入](AGENT_V1.md#6-文本和生命周期输入)。这是 Runtime/输入检测器提供的事实，本次未接入真实 VAD。

这是输入契约，不包含 Interaction Strategy、Jam Action 或生成的伴奏音符。自然语言原文保持不变，由 Agent 结合状态理解。

## 2. 时间坐标：一次定义清楚

对于音频事件，外层起止时间表示**此次分析所对应的完整原始音频片段**在会话中的采集窗口，可包含前导休止、定速拍手和尾部休止。内部音乐内容的时间仍相对该片段，不能把文件的第一个音当作会话零点。

- 哼唱：`sessionNoteStart = started_at + startSec`。
- 节奏：`sessionOnset = started_at + beat_origin_s + onsetBeat * 60 / tempo_bpm`。
- 节奏 `tempo_bpm` 是该次分析使用的时间网格；不是要求当前 Jam 无条件切换到这个速度。
- `ended_at - started_at` 必须等于原片段时长。不要用 ASR/转写完成时间、JSON 的 `createdAt` 或消息到达时间填 ended_at。
- 从离线文件导入却不知道它属于会话的哪个时刻时，两者填 null。此类事件不能仅凭到达顺序声称与其他输入同步。
- 模型返回顺序不等于发生顺序。未来合并多模态事件还要考虑表达是否完成和语义关联，本版本不自动划分交互轮次。

内部秒坐标和会话秒坐标的转换没有量化。首尾裁剪偏移已由哼唱模块加回，适配器不能再加一次。跨变速音频需要 Runtime 的 tempo map；当前旧接口的秒转拍桥接只支持调用方明确给出的恒定 BPM。

Python 跨字段校验允许 `1e-5 s` 的 JSON 数值舍入误差。这个容差不是实时同步精度或声卡延迟保证。

## 3. 三种 payload

### 语言：`source=text/speech`，`event_type=speech`

```json
{
  "text": "别停，鼓收一点，刚才那句保留。",
  "language": "zh"
}
```

`text` 必填且不能只有空白，但保留原有空格、换行、标点、否定和完整语句；不执行去空格、改写或关键词解析。`language` 和 `alternatives`（其他转写候选字符串数组）可选。原音频引用放在 `provenance.audio_ref`。

这里只接受已经提交的文字/最终转写。ASR 临时假设不能当作多个独立指令执行。输入开始、活动状态、局部修订事件需要后续单独约定。

完整样例：[speech.json](../examples/input_events/speech.json)。

### 节奏：`source=rhythm`，`event_type=rhythm_seed`

```json
{
  "status": "ready",
  "audio_duration_s": 2.4,
  "tempo_source": "session",
  "confidence_kind": "manual",
  "rhythm": {
    "tempo_bpm": 100.0,
    "onset_beats": [0.0, 1.0, 1.5, 3.0],
    "accents": [1.0, 0.5, 0.8, 1.0],
    "beat_origin_s": 0.0,
    "beats_per_bar": 4,
    "confidence": 1.0
  }
}
```

- `rhythm` 保留现有 RhythmContext；支持 60–180 BPM、4/4 单小节、最多 16 个拍点。
- 拍点严格递增、无重复、位于 `[0,4)`；accents 与拍点一一对应，取值 `(0,1]`。
- `audio_duration_s` 包含整个原片段，必须足以覆盖从 beat_origin_s 开始的一小节，不只到最后一次敲击就截断。
- `tempo_source` 必须明确为 detected / manual / session。实时跟随当前播放网格分析时使用 session，不能把它误当作新的估速结果。
- `confidence_kind` 为 grid_alignment_heuristic / manual / unspecified，用于解释内层 rhythm.confidence。它不会自动复制成外层事件级 confidence，更不是默认正确率。
- 空拍点使用 `status=no_onsets`；它是合法的空识别结果，不会被补成默认节奏。非空拍点使用 ready。

完整样例：[rhythm.json](../examples/input_events/rhythm.json)。

### 哼唱：`source=humming`，`event_type=musical_phrase`

最终采用 `payload.melody = 原 Melody v1 JSON` 的包装方式，保留整个上游文档；没有把之前讨论中的 notes/tempo 再拷贝成另一份扁平字段。这避免丢失 raw/cleaned、模型信息、质量警告、处理参数和音符来源。

```text
payload.melody
  schemaVersion / id / source / createdAt
  timebase = {unit: seconds, origin: original_audio_start}
  tempo = {bpm: null, source: unknown, mode: free, quantized: false}
  key = null; timeSignature = null
  results.raw / results.cleaned
  selectedResult / notes
  input / model / playback / midiExport 等原始元数据
```

本版对接的是已经交付的 Melody 1.0.0 自由哼唱协议，因此 BPM、调性、拍号保持 unknown/null，未量化。未来上游增加新的估速/调性协议时，需要明确升级适配规则，不可直接把 MIDI 的 120 BPM 编码参考填进 tempo.bpm。

音符包含 id、pitchMidi、startSec、durationSec、velocity01、sourceNoteIds，以及可选的 modelActivation/onsetActivation。适配器保留重叠音、前导休止和原始分数，不强制单声部、不删八度、不自动纠音。

- notes 必须与 selectedResult 对应的结果完全一致。
- raw/cleaned 内部按开始秒数、音高排序，音符 ID 不重复，音尾不能越过录音长度，sourceNoteIds 必须能在 raw 中找到。
- `status=no_notes` 与空结果对应；不把空输入变成虚构用户旋律。
- 当前 velocity01 固定为 90/127；模型激活值不等于识别正确率或物理响度。
- `id` 是上游录音来源身份；重新转换同一录音可能复用它。外层 event_id 是本次事件身份，两者不能混用。

完整样例：[humming.json](../examples/input_events/humming.json)。原始合成来源：[humming_melody_v1.json](../examples/input_events/source/humming_melody_v1.json)，来自 2026-10-24 哼唱交接包；没有个人录音。Agent 后续可为模型挑选相关字段，但事件中原始证据仍可追溯。

## 4. 下游如何接收和验证

安装本仓库依赖后，在仓库根目录执行：

```bat
python -m jammate.contracts validate examples/input_events/speech.json examples/input_events/rhythm.json examples/input_events/humming.json
```

成功输出每个文件的事件 ID、会话 ID、类型；失败返回退出码 2，并指出字段位置。没有打开麦克风、调用模型、读取音频引用或访问云 API。

Python 接口：

```python
from jammate.contracts import InputEvent, load_input_event

event = load_input_event("examples/input_events/speech.json")
# 网络消息也使用同一个入口：event = InputEvent.from_dict(received_dict)
assert event.payload["text"] == "别停，鼓收一点，刚才那句保留。"
wire_data = event.to_dict()
```

InputEvent 是不可变快照；调用方修改原字典、payload 返回值或 to_dict 返回值，都不会改动已经接收的事件。原文保留，但未知公共字段、NaN/Infinity、重复 JSON 键、单位错误、事件类型与来源不匹配会被拒绝。

JSON Schema 负责字段、类型、枚举和数值范围；Python 接收端另查时间先后、片段长度、拍点/力度配对、选中结果和音符来源等跨字段约束。上游只通过通用 JSON Schema 校验还不够，应同时通过这里的接收验证。实现使用 [jsonschema 的 Draft202012Validator](https://python-jsonschema.readthedocs.io/en/stable/validate/)。

当前没有存储型事件总线或幂等 State Manager；ID 约定和不可变快照不等于已经实现重复消息去重。去重、过期计划和执行回执属于下一阶段。

## 5. 上游文件怎么转成事件

自然语言原文：

```bat
python -m jammate.contracts text "别停，鼓收一点" --session-id jam_001 --event-id evt_001 --out demo_out/input_events/text.json
```

这条命令未提供时间，输出两个 null。打字提交若在会话第 12 秒发生，可显式加 `--started-at 12 --ended-at 12`。语音最终转写使用 `--source speech`，并传实际采集起止时间。

现有节奏 JSON：

```bat
python -m jammate.contracts wrap-rhythm examples/rhythm_a.json --session-id jam_001 --audio-duration 2.4 --tempo-source session --confidence-kind manual --started-at 12 --out demo_out/input_events/rhythm.json
```

注意：`rhythm_a.json` 是人工模板，例子显式指定片段时长和会话速度来源。真实分析使用实际时长、实际 tempo_source 和 confidence_kind。

哼唱交接 JSON：

```bat
python -m jammate.contracts wrap-humming examples/input_events/source/humming_melody_v1.json --session-id jam_001 --started-at 20 --out demo_out/input_events/humming.json
```

省略 `--started-at` 表示未知会话对齐，适用于仅检查旧文件格式。没有 event_id 时生成新 UUID；传输重试应重发已经生成的文件，而不是重新包装并生成新的 ID。

## 6. 可复现的兼容预览

```bat
python examples/input_event_demo.py --preview
```

输出在 `demo_out/input_events/`：三类事件 JSON、验证摘要、`legacy_preview.wav` 和 `.mid`。示例使用明确的 100 BPM 将哼唱秒数转成拍，再调用旧规则指挥和编排器；新包装不会读取 MIDI 的编码速度。节奏来自 session 网格时，旧 UserRhythm 的 update_tempo=False，避免覆盖新速度。

这是兼容预览，不是正式 Agent/多轮端到端验收：

- 文本事件被保留和校验，但没有送入旧关键词解析器，也未执行其音乐要求。
- 哼唱原事件保留首音位置和重叠，旧 policy 在生成回答时仍会将 motif 相对化并选择变换。
- 不含真人原声，不测声卡或真实延迟；示例的重叠事件时间也不会自动分组为一轮。
- 显式旧接口转换是 `to_legacy_rhythm(event)` / `to_legacy_phrase(event, tempo_bpm=100)`；后者不量化，原点调整若产生负拍点会报错而不丢音符。

## 7. 当前进展与接下来 Wyman 的工作

1. 将这份 schema、三个样例和 validate 命令作为输入交接的接收标准，Wenhan 可以在内部继续改模型。
2. 带 state_version 的 State Manager、BandPlan 和规则 Harness 已实现；去重、版本、失败回执和离线多轮验证见 [AGENT_V1.md](AGENT_V1.md)。
3. 显式 batch、手工生命周期事件和 INITIATE → LISTEN 已具备。继续对接真实活动检测和自动多模态事件关联。
4. 与 Kevin 对接正式 Renderer 回执/播放时钟，再用真人输入运行八个 Golden Scenarios。

本版字段与行为变更必须同时更新 schema、适配器、样例和测试；不要只在聊天中改约定。接收端当前精确接受 1.0.0 / 1.1.0，未来版本必须显式支持，禁止悄悄猜字段含义。
