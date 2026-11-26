# JamMate MRT2 demo

Renderer 与 Runtime 专项的固定输入、复跑命令和实测结果见 [Renderer Runtime 验收报告](../docs/RENDERER_RUNTIME_REPORT.md)。

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

子项目 CLI 和「一起 Jam」的流式播放选项都可使用本地 `.mlxfn`。Apple Silicon 上，网页整段试听优先用已下载的 `.mlxfn`。可以用 `JAMMATE_MRT2_ENGINE=jax` 指定 JAX，这时需要单独的 checkpoint。修改启动环境后重启工作台服务。

## 在「一起 Jam」边生成边播放

从 JamMate 仓库根目录启动已有工作台服务。在 Apple Silicon 上选择「高级设置 → 生成后端 → MRT2 本地流式播放」。该选项用本机 MLX 加载 `.mlxfn`。启动前按上文设置 `MAGENTA_HOME`；可通过 `JAMMATE_MRT2_MODEL` 和 `JAMMATE_MRT2_PROMPT` 设置模型与初始风格。

```bash
python -m jammate.input_server --implementation workspace --port 4185
```

浏览器打开 `http://127.0.0.1:4185/jam.html`。使用本地规则和冷启动时，可以用「96 BPM，C 大调，反拍」作为开场，再发送「快一点」验证小节更新。已有 LLM 配置时，也可以选大模型 Agent。语音、拍手和哼唱沿用本页面的输入流程。

### Mac 运行 MRT2，Windows 调试网页

1. 两台机器使用能相互访问的网络。在 Mac 的「系统设置 → 通用 → 共享 → 远程登录」中开启 SSH，访问范围选择自己的账号。远程登录详情会显示账号和主机地址。 [Apple 远程登录说明](https://support.apple.com/guide/mac-help/allow-a-remote-computer-to-access-your-mac-mchlp1066/mac)
2. 在 Mac 的仓库根目录，进入已有 MRT2/MLX 的 Python 环境。将 `MAGENTA_HOME` 指向 Mac 上的模型目录并启动服务，例如：

   ```bash
   PYTHONPATH=src MAGENTA_HOME=/path/to/models python -m jammate.input_server --implementation workspace --port 4215
   ```

   Mac 可先打开 `http://127.0.0.1:4215/jam.html`，确认「MRT2 独立生成」可选。需要同时测试 SoundFont 时，可追加 `--fluidsynth /path/to/fluidsynth`。
3. Windows PowerShell 使用 OpenSSH Client。将 `macuser@MAC_IP` 换成 Mac 的账号和地址，运行以下命令，并保持窗口开启。两端端口保持一致，符合 JamMate 的同源检查。 [Windows OpenSSH Client 安装说明](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)

   ```powershell
   ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:4215:127.0.0.1:4215 macuser@MAC_IP
   ```

4. 在 Windows Chrome 或 Edge 打开 `http://127.0.0.1:4215/jam.html`，选择 MRT2 独立生成，然后打开「查看调试日志」。网页的麦克风、播放和 DevTools 在 Windows，JamState、编排和 MRT2 模型在 Mac。本地回环 URL 可获得浏览器麦克风权限。两边浏览器各自新建会话，调试时在同一个 Windows 标签页内复现素材和操作。 [浏览器安全上下文说明](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Secure_Contexts)

连接失败时，在 Windows 运行 `Test-NetConnection MAC_IP -Port 22` 检查 SSH，再用 `Invoke-RestMethod http://127.0.0.1:4215/api/ready` 检查转发和 Mac 服务。调试日志可切换查看开场或某轮输入，小节选择可独立检查当前或待播输出。需要复盘时用「复制调试 JSON」保存 BandPlan、模型条件和起拍回执。

JamMate 提供按小节编排的条件，同一个 MRT2 会话连续生成并按 40 ms 块发送 48 kHz 双声道 PCM。浏览器维护短播放缓冲，在收到首批音频后开始播放。会话中可继续给出指令，新计划按 JamMate 的小节时钟更新条件，播放导出可保存双声道 WAV。页面的声部和计划状态描述输入条件，MRT2 生成的听感由试听确认。「MRT2 生成试听」选项仍可导出完整 WAV。

要试听由 MRT2 作曲的版本，在「一起 Jam → 高级设置 → 生成后端」选择「MRT2 独立生成（BandPlan 直连）」。此模式将 BandPlan 和当前的速度、调性、声部等目标转为 MusicCoCa 文本条件，整段声音由本地 MRT2 生成。可在冷启动填写风格，例如「轻柔的爵士钢琴和低音贝斯」，边听边发送演奏指令，结束后导出 WAV。精确速度、和弦、哼唱复现和单声部静音需要通过试听确认。

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
