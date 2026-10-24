# JamMate Renderer Experiments

这个目录是 JamMate 的实验性 renderer 实现与代表性样例。它比较两条路径：

```text
Shared MusicIntent
  -> MIDI Renderer -> exact MIDI events -> FluidSynth -> WAV
  -> Lyria Renderer -> Gemini Live Music API -> PCM -> WAV
```

## 代码和证据

- `app/models.py`：共享 `MusicIntent`。
- `app/midi/`：确定性 MIDI pattern / performance arrangement、GM program、CC、FluidSynth 渲染。
- `app/backends/lyria.py`：Gemini Lyria RealTime API adapter。
- `app/upstream_intent_adapter.py`：Action Intent + baseline state -> 完整 MusicIntent + backend overrides。
- `scripts/renderer_cli.py`：MIDI / Lyria 命令行入口。
- `scripts/demo_upstream_intent.py`：上游 Action Intent 对 MIDI/Lyria 的完整对照流程。
- `examples/`：可试听 WAV、MIDI、event JSON 和 session JSON。
- `docs/RENDERER_INPUT_CONTRACT_CN.md`：从人的意图反推上游 Agent 输入契约。

## 结论


### MIDI

MIDI 是精确执行端。它能强保证 BPM、chord progression、指定 bar/beat 入场、保留既有 drum event、MIDI note/CC/program 的可重复输出。

演奏型 warm-funk 例子不是简单增加音符，而是增加 8 小节 phrase、Bass 句法、voice-leading、drum fill、CC 动态与确定性 microtiming。

试听：

```text
examples/performance-midi/audio/midi_warm_funk_electric_performance_performance_1792825710.wav
```

### Lyria

Lyria 是语义生成端。它接收 weighted prompts，以及 BPM / density / brightness / scale / mute controls。适合自然感、风格、情绪和氛围。

当前公开 API 没有文档化的 MIDI、note sequence 或结构化 chord timeline 输入。因此，“保留完全一样的鼓 groove”对 Lyria 是 best-effort 语义请求，不是 MIDI 级别的强保证。

### Action Intent 必须绑定 session state


`examples/upstream-intent/configs/source_intent.json` 是一个增量命令：保持鼓 groove、下一小节加入柔和 Bass、整体更 dreamy。

它缺少 BPM、和弦进行、当前声部状态和 transport 位置，因此不能无状态地直接交给 renderer。Adapter 明确地把它与 baseline state 组合。

对照音频：

```text
examples/upstream-intent/audio/midi_upstream_intent_baseline.wav
examples/upstream-intent/audio/midi_upstream_intent_adapted.wav
examples/upstream-intent/audio/lyria_upstream_intent_before_after_update_before.wav
examples/upstream-intent/audio/lyria_upstream_intent_before_after_update_after.wav
```

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### MIDI 演奏段落


需要 FluidSynth 二进制和 `.sf2` SoundFont。

```powershell
python scripts/renderer_cli.py --round round-demo midi-test `
  --style warm_funk `
  --instrument electric_performance `
  --arrangement performance `
  --bars 8 `
  --fluidsynth <path-to-fluidsynth.exe> `
  --soundfont <path-to-soundfont.sf2>
```

### Lyria 短测


```powershell
Copy-Item .env.example .env
# 在 .env 中设置 GEMINI_API_KEY
python scripts/renderer_cli.py --round round-demo lyria-test --duration 20
```

### 上游 Intent 对照


```powershell
Copy-Item .env.example .env
python scripts/demo_upstream_intent.py
```

输出写入 `results/generated-upstream-intent/`。

## 文件导航

```text
app/models.py                    Shared MusicIntent
app/upstream_intent_adapter.py   Action Intent + baseline -> adapted MusicIntent
app/midi/patterns.py             basic / performance MIDI arrangement
app/midi/files.py                Standard MIDI + FluidSynth renderer
app/backends/lyria.py            Gemini Lyria RealTime API adapter
scripts/renderer_cli.py          CLI entry point
scripts/demo_upstream_intent.py  complete Action Intent comparison
examples/                        representative WAV / MIDI / JSON
```

## 依然需要继续探索的问题

- MIDI 的 density / brightness 还没有完整映射到 note density、register 和 filter。
- MIDI 还没有完整 voice-leading optimizer、slide、ghost bass 和更大的 fill 库。
- Lyria 的 key、chord progression 和局部 refinement 仍需更多受控实验。
