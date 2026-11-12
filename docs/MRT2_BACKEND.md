# MRT2 与 FluidSynth 后端接入

基于工作台基线 `6571fa2` 实现，现已合入 `codex/jammate-workbench`，后续直接在该分支维护。公共边界仍是 InputEvent、JamState 和 BandPlan；MRT2 tokens 是执行端内部格式。

## 运行路径与职责

```text
InputEvent + JamState + history
  → RuleAgent 或 LLMAgent
  → 同一个 BandPlan
  → prepare_plan：编排、版本与 preserve 的实际音符校验
  → KevinRuntime / MRT2Runtime
  → MIDI 回读、WAV 检查、执行回执
```

两个适配器继承同一个执行流程，接收相同的 `JamState + BandPlan`。共用小节到秒的转换、MIDI tick 检查、状态版本、取消检查、输出检查和回执写入。改变 Agent 不会改变后端协议。`examples/backend_demo.py` 对每种 Agent 只决策一次，再把相同计划和相同决策前状态送给两个后端。

| 能力 | FluidSynth | MRT2 |
|---|---|---|
| 标准输入 | MIDI + SF2 | 文字风格嵌入 + 25 Hz 音符/鼓条件 |
| 音符与力度 | MIDI 回读验证 | 条件仅保留音高并集与起音；没有 velocity |
| 声部/音色 | MIDI 通道、program、SF2 | 条件没有通道和 program；文字只提供风格引导 |
| 鼓 | GM 音高标识不同鼓件 | 单个二值鼓条件，不能指定某个鼓件 |
| 精确 preserve | 编排及 MIDI 层校验，音色听感另验 | 不能保证音频中的音高、BPM、key、motif、声部不变 |
| 时间 | MIDI tick 精度 | 条件为 40 ms 一帧，声音对齐另需测量 |
| 当前运行模式 | 离线导出 | 离线生成；每段重置模型上下文 |

`MRT2Runtime.validate()` 默认拒绝无法保证的要求，抛出带 `report` 的 `BackendCapabilityError`，在加载模型之前失败。不能让 LLM 删除用户的 preserve 约束来绕过限制。

显式传 `allow_preview=True` / `--allow-preview` 才生成 MRT2 试听。回执为 `artifact_generated=true, phase=preview, succeeded=false, state_committed=false`，并逐项列出 preserve/action 的 `guaranteed=false`。StateManager 保留输入，并把计划记为 failed，允许使用新 plan_id 改后端重规划。声音文件已生成与要求已兑现分开记录。试听的 `band.mid` 是**条件 MIDI，不是模型音频的转写**。

停止/全静音计划由宿主直接输出零样本，不调用模型；这一效果可真实兑现并提交。任何新输入取消了正在生成的计划，旧回执仍不能提交。导出回执从不代表物理声卡已经播放。

## 官方 MRT2 输入与环境

核对的官方代码为 [magenta/magenta-realtime 694a545](https://github.com/magenta/magenta-realtime/tree/694a545e4ba0b88bf1150137b129582166d3e07f)，Python 包 `magenta-rt==2.0.3`。来源：

- [官方安装说明](https://github.com/magenta/magenta-realtime/blob/694a545e4ba0b88bf1150137b129582166d3e07f/docs/installation.md)：Linux JAX 离线推理；Apple Silicon MLX 支持实时引擎。
- [官方 token 定义](https://github.com/magenta/magenta-realtime/blob/694a545e4ba0b88bf1150137b129582166d3e07f/magenta_rt/config.py)：`mulan_tokens_25hz`、`pianoroll_with_onsets_tokens`、`drum_pianoroll_tokens`。
- [官方 Python generate](https://github.com/magenta/magenta-realtime/blob/694a545e4ba0b88bf1150137b129582166d3e07f/magenta_rt/jax/system.py)：`generate(conditioning=..., frames=..., state=...)`；同一次调用内条件固定。

本项目包装器读取标准 `.mid`，按实际 MIDI tempo map 转成上述官方条件。非鼓音符合并为 128 音高状态：0=off、1=持续、2=起音。鼓为 0/1。起音向下取帧、音尾向上取帧，短音至少占一帧；条件变化时分次调用并传回同一个模型 state。最终音频按请求长度裁切，48 kHz stereo。

官方 `mrt jax generate` 当前没有 `--midi` 参数。不要把本项目的命令行参数或 JSON 报告说成官方文件协议。`src/jammate/render/mrt2.py` 是对官方 Python API 的 MIDI 输入包装。

建议使用 Python 3.12 的独立 Linux/WSL 环境，不改已有 Windows 工作环境：

```bash
git clone --branch codex/jammate-workbench https://github.com/3123738092/aiaa3800_jammate.git aiaa3800-jammate-mrt2
cd aiaa3800-jammate-mrt2
python -m venv .venv-mrt2
source .venv-mrt2/bin/activate
python -m pip install -e '.[dev,llm,mrt2]'
# NVIDIA GPU：按驱动选择 JAX wheel；本次采用 CUDA 12
python -m pip install 'jax[cuda12]'
# 如果代理使用 socks，还需要 python -m pip install socksio
mrt models init
mrt checkpoints download mrt2_small.safetensors
```

JAX 使用 raw `.safetensors` checkpoint；`mrt models download` 的 `.mlxfn` 不是 JAX checkpoint 的替代。权重与资源保存在用户的 Magenta 目录，不提交仓库。安装包本身不表示权重已下载或推理已验证。Apple MLX 可改用 `magenta-rt[mlx]==2.0.3` 和包装器的 `--backend mlx`；本次验收记录单独标注实际运行的平台。

如需重建本次 Linux/CUDA 12 的 Python 包环境，可先安装 `requirements-mrt2-linux.lock.txt` 再 `pip install -e .`。该锁定文件不适用于 Windows 原生 Python 或 Apple MLX。

## 跳过上游的直接输入

完整标准输入位于 [examples/backend_inputs](../examples/backend_inputs/README.md)：`melody.mid`、`TimGM6mb.sf2`、`prompt.txt`。不需要 InputEvent、麦克风、Agent、API key 或 JamState。

```bash
# FluidSynth：这个 MIDI 就是要演奏的音符
fluidsynth -ni -g 0.8 -r 48000 -F demo_out/direct-fluid.wav examples/backend_inputs/TimGM6mb.sf2 examples/backend_inputs/melody.mid

# MRT2：只用文字条件，真实模型推理
python -m jammate.render.mrt2 --prompt 'a warm acoustic jazz trio' --duration 2.4 --out demo_out/direct-mrt2-text.wav

# MRT2：同一个真实 MIDI 文件作为音符条件，另提供文字风格
python -m jammate.render.mrt2 --prompt 'a warm acoustic jazz trio' --midi examples/backend_inputs/melody.mid --duration 2.4 --out demo_out/direct-mrt2-midi.wav
```

先创建 `demo_out`。每次真实 MRT2 运行写出旁边的 `.inference.json`，记录模型、引擎、条件类型、输入 MIDI SHA256、耗时和 `inference=real_magenta_rt`。测试替身会明确写成 `injected_test_double`。

## 规则版与 LLM 版、同输入对照

在完整仓库根目录运行，输出目录必须尚不存在：

```bash
python examples/backend_demo.py --agent both --renderer both --text '鼓更猛一点' --fluidsynth /path/to/fluidsynth --soundfont examples/backend_inputs/TimGM6mb.sf2 --allow-preview --out demo_out/backend-comparison
```

这会真实调用配置的 LLM，并运行两个真实音频后端。只跑规则用 `--agent rule`；只检查严格拒绝时去掉 `--allow-preview`。API 配置沿用仓库外的 `JAMMATE_LLM_CONFIG` 或 `--config`。生成报告保存输入、初始状态、两种 Agent 的计划、各后端回执和执行后状态。MRT2 不可兑现的要求不能因生成成功就计为通过。

## 网页加载新后端

旧 `reference_bridge.py` 固定提取 `a63a0f5`，其中没有 MRT2。新增显式 `workspace/pinned` 加载方式：

```bash
python -m jammate.input_server --implementation workspace --port 4188
```

服务器默认 workspace，在独立工作进程中只加载当前检出的源码。文字页先选择规则或 LLM，再选生成后端；MRT2 需要明确勾选生成试听。规则和 LLM 共用同一个后端入口。`--implementation pinned` 保留历史复现；请求新后端时明确拒绝，不混用两个版本的 JamState 类型。

模型/引擎/风格是服务端配置：`JAMMATE_MRT2_MODEL=mrt2_small`、`JAMMATE_MRT2_ENGINE=jax`、`JAMMATE_MRT2_PROMPT=...`。它们不由 LLM 自由写入可执行命令。MRT2 试听不会更新作品的已确认音乐状态；后续调整仍从该已确认状态决策，不能声称已经有音频反馈验证的多轮生成。

## 验证与仍待完成

普通 `pytest` 不调用 OpenAI、不加载 MRT2 权重。新增测试覆盖官方 API 参数、动态条件和连续 state、MIDI tempo map、短音/重复音/鼓、规则/模拟 LLM 的配对执行、严格拒绝、试听不提交、静音停止、过期回执及 workspace 加载。

真实推理与实际产物另记于 `MRT2_VALIDATION.md`。尚需真人输入、盲听、模型音符/节拍跟随评测、MLX、连续模型上下文与声卡实时调度验收。生成 WAV 不能代替这些检查。
