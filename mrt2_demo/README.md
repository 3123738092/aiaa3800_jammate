# JamMate MRT2 demo

这个子项目用 JamMate 的 `InputEvent`、`JamState`、`BandPlan` 和 `StateManager` 驱动本地 MRT2 推理，也保留了独立的流式播放和实验入口。主要依赖和模型准备见 [JamMate MRT2 后端文档](../docs/MRT2_BACKEND.md)。

## 本地运行

在 `aiaa3800_jammate` 仓库根目录执行。`python -m mrt2_demo` 运行 JamMate 计划，生成整段试听 WAV 和执行回执。独立流式播放用下方的 `python -m mrt2_demo.run`。

`MAGENTA_HOME` 指向已有 `magenta-rt-v2` 缓存的父目录。未设置时，Magenta 使用自己的默认缓存路径。

```bash
conda activate JamMate
python -m pip install -e '.[dev,live]'
export MAGENTA_HOME=/path/to/existing/models
export JAMMATE_MRT2_ENGINE=mlx
python -m mrt2_demo --text '鼓更猛一点' --allow-preview --out mrt2_demo/results/jammate
python -m mrt2_demo --stop --out mrt2_demo/results/jammate
```

Apple Silicon 的 `mlx` 走 demo 的 `MagentaRT2StdMlxfn`，需要 `magenta-rt[mlx]==2.0.3`、匹配的 `.mlxfn` 和状态文件。已有 `.mlxfn` 实测环境使用 `mlx==0.31.2` 和 `mlx-metal==0.31.2`。`jax` 沿用 JamMate 的官方 JAX 包装器和对应 checkpoint。默认模型是 `mrt2_small`，可通过 `JAMMATE_MRT2_MODEL` 配置。

常规编排由 JamMate 写入 `band.mid`，适配器按 MIDI tempo map 生成条件。安装 JamMate 的 `llm` extra 并准备 LLM 配置后，可加 `--arrangement llm --config /path/to/config.json`，直接传入 25 Hz 音高与鼓条件，并保存 `conditioning.json`。预览输出为 48 kHz 双声道 `band.wav` 和 `receipt.json`。有声试听需要 `--allow-preview`，回执保留 `phase=preview` 和 `state_committed=false`。停止由宿主生成静音并提交。

其他 Python 调用方可以传入同一生成器，然后使用自己的 `StateManager` 和已暂存的计划。

```python
from jammate.render.runtime import create_runtime
from mrt2_demo.jammate_bridge import generate

runtime = create_runtime(output, backend="mrt2", generator=generate, allow_preview=True)
result = runtime.execute(manager, plan)
```

JamMate 网页的两条渲染路径目前使用其内置生成器。这个子项目通过上面的 Python Runtime 和 CLI 入口使用本地 `.mlxfn`。

## 流式播放与实验

```bash
python -m mrt2_demo.run --mode midi --seconds 60 --play --interactive
python -m mrt2_demo.run --mode text --seconds 20
python -m mrt2_demo.run --mode audio --audio /path/to/reference.wav --seconds 20
python -m mrt2_demo.run --mode update --seconds 20 --play
python -m mrt2_demo.benchmark
python -m mrt2_demo.experiment
python -m mrt2_demo.experiment_style_control
```

`--play` 将 48 kHz 双声道 PCM 按 40 ms 一帧送到声卡。开启 `--interactive` 后，播放期间可输入 `text YOUR PROMPT`、`audio PATH` 或 `quit`。风格编码在后台完成，在下一次生成时应用。`audio PATH` 使用整份 WAV 提取风格，`--mode midi` 使用预置编排按帧生成条件。这个入口的实时输入是终端命令。

常规 MIDI demo 使用现行 JamMate 编排器。同一新版编排用于 `python -m mrt2_demo.baseline`，可用于新的对照实验。`--fixture` 的显式音符实验继续使用原有输入，历史报告保留在迁移前的目录。

生成音频、报告和缓存保存在本机。通过 `--out` 可把运行结果写到其他位置。

## 验证

```bash
python -m pytest mrt2_demo tests/test_mrt2_runtime.py tests/test_kevin_runtime.py
MRT2_INTEGRATION=1 python -m pytest mrt2_demo/test_bandplan_robustness.py
```

第一条跑本地单元测试和宿主 Runtime 回归测试。第二条需要装有模型和 Metal 的 Apple Silicon 环境。真实推理、声卡播放和听感分别验收。

## JamMate 端到端 benchmark

在 JamMate 仓库根目录运行。每次使用新的输出目录，`--trials` 默认是 3。

```bash
JAMMATE_MRT2_ENGINE=mlx python -m mrt2_demo.benchmark_e2e \
  --out mrt2_demo/results/e2e-local
```

加 `--trials 1` 可让每个场景只跑一遍。已有 JAX checkpoint 时，可用 `JAMMATE_MRT2_ENGINE=jax JAX_PLATFORMS=cpu` 运行同一套场景。CPU 与 Metal 的耗时需要各自记录。

- 文字、转写文本、合成拍手 WAV 与标准哼唱 Melody v1 数据进入 JamMate 的输入适配器，再由 `StateManager`、Agent 和 `MRT2Runtime` 驱动真实模型。拍手 WAV 会经过 JamMate 的节奏分析。
- 覆盖规则 Agent 与 LLM Agent、标准 MIDI 与直接条件帧、变速、16 小节持续生成、严格拒绝、生成中被新输入取消、失败后重规划以及宿主静音停止。
- 默认 LLM 场景使用固定响应，经 JamMate 的真实验证和编排执行。装好 `llm` extra 和私有配置后，可加 `--live-llm --llm-config /path/to/config.json`，加入一次真实 LLM 决策与直接编排。每次启用该选项都会调用配置的 API。
- `summary.json` 记录逐项通过率、各阶段耗时、生成实时率和音频统计。各试次保留输入、WAV、MIDI 或条件帧、计划和回执。某项失败会继续跑剩余场景，最后返回非零退出码。

这套基准从 JamMate 的输入数据接口开始，转写和哼唱使用现成的识别结果。浏览器、实际录音、声卡播放及盲听分别验收。原来的 `mrt2_demo.benchmark` 仍用于本地流式播放测量。
