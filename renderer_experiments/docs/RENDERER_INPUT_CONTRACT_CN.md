# JamMate Renderer 输入契约：从人的意图到自然 MIDI / Lyria 段落

更新时间：2026-10-24

## 核心经验

演奏型 `warm_funk` 变得更好听，不是因为单纯增加了 Clavinet 层。更关键的是：所有声部共享 8 小节 phrase 结构，并且各自有不同的演奏职责。

```text
自然的演奏段落
!= 添加更多音符
=
共同的时间结构
+ 声部角色分工
+ phrase 内变化
+ phrase 间发展与收束
+ dynamics / articulation / timing
+ 可验证的 preserve 约束
```

这次实现的具体做法：

| 维度 | 演奏型 MIDI 的处理 | 听感收益 |
|---|---|---|
| 段落 | 8 小节，4 小节一个 phrase，句尾 fill | 不再像无限循环的 pattern |
| Drums | 重音、ghost note、open hat、fill、crash | 有呼吸和句读 |
| Bass | root / chord tone / octave / approach note | 从根音标记变成有方向的线条 |
| Harmony | voice-leading + syncopated comping | 避免每小节重新堆一次 block chord |
| Ornament | Clavinet call-and-response | 产生前景回应和层次 |
| Dynamics / space | velocity + CC 7/10/11/91 | 有声部层次、动态和空间 |
| Timing | 确定性 -14 ms 至 +12 ms 偏移 | 减弱完全量化的机械感 |

## 上游应提供的三层输入

### 1. Shared Musical State

这是 session 维护的当前音乐世界状态，不能每次都由 renderer 猜测。

```json
{
  "session_id": "jam_001",
  "revision": 18,
  "transport": {"bpm": 100, "meter": "4/4", "bar_index": 12, "beat_in_bar": 2.5},
  "harmony": {"key": "C", "mode": "major", "progression": ["Cmaj7", "Am7", "Dm7", "G7"], "chord_duration_bars": 1},
  "groove": {"family": "funk", "subdivision": "sixteenth", "swing": 0.0},
  "form": {"section": "verse", "phrase_start_bar": 9, "phrase_length_bars": 4},
  "arrangement": {
    "drums": {"active": true, "groove_id": "funk_16th_a"},
    "bass": {"active": false},
    "harmony": {"active": true},
    "ornament": {"active": false}
  }
}
```

### 2. Action Intent

这是人这次想改什么，不应承担完整音乐状态。

```json
{
  "target": "bass",
  "operation": "add",
  "timing": "next_bar",
  "attributes": {
    "role": "supportive_counterline",
    "dynamics": "soft",
    "register": "low",
    "instrument": "fingered_bass"
  },
  "constraints": [
    {"target": "drums.groove", "operation": "preserve", "strength": "strict"}
  ]
}
```

### 3. Render Plan

这是 Adapter 将 Shared State + Action Intent 转成 renderer 可执行计划的结果。MIDI 和 Lyria 不需共享同一份底层 plan。

## MIDI Render Plan 需要的关键字段

```json
{
  "phrase": {
    "length_bars": 4,
    "position": 3,
    "energy_curve": [0.45, 0.55, 0.65, 0.8],
    "cadence": "fill_into_next_phrase",
    "variation_seed": 73
  },
  "layers": {
    "drums": {"role": "timekeeper", "activity": 0.75},
    "bass": {"role": "supportive_counterline", "activity": 0.45},
    "harmony": {"role": "syncopated_comping", "activity": 0.55},
    "ornament": {"role": "call_response", "activity": 0.25}
  },
  "performance": {
    "bass": {"note_palette": ["root", "third", "fifth", "seventh", "octave", "approach"], "articulation": "short", "fill_probability": 0.25},
    "harmony": {"voice_leading": true, "voicing_density": "3_to_4_notes", "sustain": "short"},
    "drums": {"ghost_note_probability": 0.25, "fill_at_phrase_end": true, "humanize_ms": 12}
  },
  "timbre": {
    "bass": {"program": 33},
    "harmony": {"program": 4},
    "ornament": {"program": 7},
    "drum_kit": {"bank": 128, "program": 0}
  }
}
```

重点：`parts.bass = 0.45` 只表示 Bass 的活动程度，不足以决定它是根音、walking line 还是 syncopated funk line。需要 role、note palette、articulation、register、phrase policy 等额外输入。

## Lyria Render Plan 需要的关键字段

```json
{
  "weighted_prompts": [
    {"text": "warm funk groove", "weight": 1.0},
    {"text": "dreamy atmosphere", "weight": 0.7},
    {"text": "soft supportive fingered bass entering next phrase", "weight": 0.8},
    {"text": "preserve the current drum feel", "weight": 0.6},
    {"text": "harmonic movement: Cmaj7 - Am7 - Dm7 - G7", "weight": 0.55}
  ],
  "generation_config": {
    "bpm": 100,
    "density": 0.55,
    "brightness": 0.55,
    "scale": "C_MAJOR_A_MINOR",
    "mute_bass": false,
    "mute_drums": false
  }
}
```

| 需求 | MIDI | Lyria |
|---|---|---|
| 保留完全相同的鼓 groove | 可用固定 event 强保证 | 只能语义请求 |
| 下一小节精确加 Bass | 可按 transport tick 调度 | 只能近似按 session update 触发 |
| 指定 bassline / note | 可直接执行 | 当前没有 note-level 结构化输入 |
| 严格 chord progression | 可直接执行 | 当前只能 text conditioning |
| dreamy / organic | 要转成音色、CC、articulation、register | 适合 weighted prompts |

## 推荐的 Agent 架构

```text
用户文本 / 演奏 / 历史上下文
          -> Intent Parser
          -> Action Intent
          -> Musical State Manager
             - transport
             - harmony timeline
             - groove identity
             - form / phrase position
             - active layers
             - timbre / mix
          -> Renderer Planning Adapter
             - MIDI: exact events + CC + programs
             - Lyria: weighted prompts + generation config
          -> MIDI Renderer / Lyria Renderer
```

## 上游调用 renderer 前的最小输入

```text
必需：
- session_id、state revision
- BPM、meter、当前 bar / beat
- key、mode、chord progression、每个 chord 的时长
- active layers 与各层 role
- action target、operation、timing
- preserve constraint 与 strictness

强烈建议：
- phrase length、当前 phrase position、section role
- 每个新增层的 instrument / timbre
- articulation、register、activity、fill policy
- deterministic variation_seed
```
