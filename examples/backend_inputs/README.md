# 不经过 JamMate 上游的音频后端输入

这里的文件可以独立交给音频后端，不需要先识别录音、创建 InputEvent 或调用 Agent。

- `melody.mid`：标准 MIDI，100 BPM，四个旋律音 60/64/67/64，保留前导和音间休止。由 `examples/make_backend_inputs.py` 生成。
- `prompt.txt`：文字风格条件。
- `TimGM6mb.sf2`：FluidSynth 音色库，附上游 `TimGM6mb.COPYRIGHT` 和 `GPL-2.txt`；原文件未修改。
- `manifest.json`：输入校验和及建议 2.4 秒时长，仅为项目来源记录，**不是 MRT2 官方输入协议**。

安装 FluidSynth 后，在仓库根目录先建 `demo_out`，执行：

```bash
fluidsynth -ni -g 0.8 -r 48000 -F demo_out/direct-fluid.wav examples/backend_inputs/TimGM6mb.sf2 examples/backend_inputs/melody.mid
```

Windows 可以把 `fluidsynth` 替换为 `fluidsynth.exe` 的完整路径。SF2 影响 MIDI 演奏音色；MRT2 不读取 SF2。

在已安装官方 MRT2 和权重的 Python 环境中：

```bash
# 只提供文字
python -m jammate.render.mrt2 --prompt 'a warm acoustic jazz trio' --duration 2.4 --out demo_out/direct-mrt2-text.wav
# 文字 + 本目录中的标准 MIDI
python -m jammate.render.mrt2 --prompt 'a warm acoustic jazz trio' --midi examples/backend_inputs/melody.mid --duration 2.4 --out demo_out/direct-mrt2-midi.wav
```

这是本项目对官方 `MagentaRT2Jax.generate` 的包装，MIDI 被转成官方 25 Hz note/drum token。官方 CLI 的文字输入也可直接运行：

```bash
mrt jax generate --model mrt2_small --prompt 'a warm acoustic jazz trio' --duration 2.4
```

官方 CLI 输出路径由 Magenta 配置决定。它没有 `--midi` 选项；本项目没有给官方 CLI 虚构此参数。MRT2 的 MIDI 是生成条件，不能保证与输出音频的音符完全相同。

环境安装、规则/LLM 的双后端对照、网页入口和回执解释见 [MRT2_BACKEND.md](../../docs/MRT2_BACKEND.md)。真实运行证据见 [MRT2_VALIDATION.md](../../docs/MRT2_VALIDATION.md)。
