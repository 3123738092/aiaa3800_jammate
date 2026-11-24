# JamMate MRT2 demo

这个子项目用 JamMate 的 `InputEvent`、`JamState`、`BandPlan` 和 `StateManager` 驱动本地 MRT2 推理，也保留了独立的流式播放和实验入口。主要依赖和模型准备见 [JamMate MRT2 后端文档](../docs/MRT2_BACKEND.md)。

## 本地运行

在 JamMate 仓库根目录执行。`MAGENTA_HOME` 指向已有 `magenta-rt-v2` 缓存的父目录。未设置时，Magenta 使用自己的默认缓存路径。

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

交互播放支持 `text YOUR PROMPT`、`audio PATH`、`quit`。常规 MIDI demo 使用现行 JamMate 编排器。同一新版编排用于 `python -m mrt2_demo.baseline`，可用于新的对照实验。`--fixture` 的显式音符实验继续使用原有输入，历史报告保留在迁移前的目录。

生成音频、报告和缓存保存在本机。通过 `--out` 可把运行结果写到其他位置。

## 验证

```bash
python -m pytest mrt2_demo tests/test_mrt2_runtime.py tests/test_kevin_runtime.py
MRT2_INTEGRATION=1 python -m pytest mrt2_demo/test_bandplan_robustness.py
```

第一条跑本地单元测试和宿主 Runtime 回归测试。第二条需要装有模型和 Metal 的 Apple Silicon 环境。真实推理、声卡播放和听感分别验收。
