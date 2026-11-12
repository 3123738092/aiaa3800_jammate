# MRT2 / FluidSynth 实测记录 · 2026-11-12

代码基线 `6571fa2`，独立分支 `feat/mrt2-runtime`。所有源 MIDI 都是人工构造或已有规则编排器输出；未使用个人录音。

## 真实模型与真实音源

实际在 WSL Linux、RTX 4060 Laptop 8 GiB 上安装并运行官方 `magenta-rt==2.0.3`，使用 `mrt2_small` raw checkpoint 和 JAX/CUDA 12。环境快照为 [requirements-mrt2-linux.lock.txt](../requirements-mrt2-linux.lock.txt)，实际下载的 10 个权重/资源文件的 SHA256 见 [MRT2_MODEL_ASSETS.json](MRT2_MODEL_ASSETS.json)。这些文件留在仓库外。

FluidSynth 实测版本为 Linux 2.2.5，使用样例目录中原样提供的 TimGM6mb。Linux 依赖以本地解包方式准备，没有替换已有 Windows 环境。下面三段都是真实生成，不是 mock：

| 输入 | 输出 | 实测 |
|---|---|---|
| 标准 MIDI + SF2，绕过 JamMate 上游 | [direct-fluid.wav](../examples/backend_outputs/direct-fluid.wav) | 48 kHz stereo，2.004 s；按 MIDI 演奏 |
| 只有文字风格条件 | [direct-text.wav](../examples/backend_outputs/direct-text.wav) | 48 kHz stereo，2.4 s；包含加载/编译约 22.91 s |
| 同一文字 + melody.mid 条件 | [direct-midi.wav](../examples/backend_outputs/direct-midi.wav) | 48 kHz stereo，2.4 s；包含加载/编译约 24.34 s |

三段音频都通过有限样本、时长与非静音检查。MRT2 两段各生成 60 帧；直接输入、命令和时长含义见 [输入目录](../examples/backend_inputs/README.md)。FluidSynth 与 MRT2 的尾音/生成窗口不同，不能用文件长度相等判定音符一致。

JAX 编译时出现过自动调优的显存分配警告，最终真实推理完成。未验证 MLX 或声卡实时播放。首次调用耗时包含加载与编译，不能当成实时演奏延迟。

## 规则与真实 LLM 的同输入对照

固定输入「鼓更猛一点」，初始状态 100 BPM、鼓 level=0.5。每种 Agent 只生成一个计划，两个后端分别从相同初始状态接收同一个事件，执行相同计划。

| Agent | FluidSynth | MRT2 |
|---|---|---|
| RuleAgent | [音频](../examples/backend_outputs/rule-fluidsynth.wav)；完成实际文件校验并提交状态，鼓 level=0.75 | [音频](../examples/backend_outputs/rule-mrt2.wav)；真实生成 9.6 s；只生成试听，鼓状态仍为 0.5 |
| 真实 LLMAgent | [音频](../examples/backend_outputs/llm-fluidsynth.wav)；完成实际文件校验并提交状态，鼓 level=0.7 | [音频](../examples/backend_outputs/llm-mrt2.wav)；真实生成 9.6 s；只生成试听，鼓状态仍为 0.5 |

每种 Agent 下，FluidSynth 和 MRT2 保存的条件 MIDI **字节完全一致**。MRT2 逐项报告 tempo/key/layer 等 preserve 未保证；没有把生成 WAV 算成完成音乐要求。

这个案例也展示了条件信息损失：规则与 LLM 主要给出不同的鼓力度，但 MRT2 条件没有 velocity，两种计划转成相同的 note/drum 条件，生成音频也相同。这不是 LLM 音乐质量提升的证据。

MRT2 第一次配对生成含模型加载约 21.32 s；复用已加载模型后的下一次约 4.44 s。不能把这两个耗时解释成规则 Agent 与 LLM Agent 的性能差异。每段仍重置生成上下文，没有测试跨段音乐连续性。

完整计划、回执、音频 SHA256、RMS 和状态结果见 [summary.json](../examples/backend_outputs/summary.json)。MRT2 的 `.mid` 明确标记为 conditioning，而不是生成音频的转写。

## 浏览器与真实 API

使用 `tests/mrt2_browser.py` 对实际工作台进行 opt-in 验收：

1. 严格模式在模型加载前拒绝不保证的 preserve。
2. 规则 Agent + MRT2 真正生成音频；浏览器解码 WAV，页面显示未提交状态，并把 MIDI 标为条件文件。
3. 真实 LLM + FluidSynth 生成并提交，验证 390 px 布局无横向溢出。

真实 LLM 请求型号为 `gpt-5.4-mini`，返回 `gpt-5.4-mini-2026-03-17`，一次 accepted，无 fallback。该次决策约 6.877 s。调用记录在 [browser-report.json](../examples/backend_outputs/browser-report.json)，不含 key。浏览器录到的是音频文件解码，不是声卡物理播放验收。

最后补充检查了新的服务身份与逐项能力错误报告：`/api/ready` 为 `jammate-workbench-backends-v2`、implementation=workspace，严格拒绝的 HTTP 400 保留结构化 capabilities。新增界面约束说明使用**已记录真实音频的 UI 回放**检查，不重新推理；单独标记在 [final-ui-check.json](../examples/backend_outputs/final-ui-check.json)。

## 自动化与构建

- **451 项 Python 通过**，含原有执行端和新增适配测试；1 项依赖 Google SDK 的 Lyria 测试未选择，5 个既有音频依赖警告。最后完整回归耗时见 summary 中原始 JUnit 元数据。
- **30 项 JavaScript 通过**。
- 基线、优化版两套前端构建通过。本次 Linux 构建使用 Node 20.20.1 与 npm 安装依赖，未改 package.json/pnpm-lock；团队原 Windows/pnpm 流程保留。
- 普通 pytest 中模型输出和外部 LLM provider 均是明确标记的测试替身；上面的真实推理、真实 API 和浏览器验收独立记录。
- 验证了缺少模型依赖时的可恢复失败、Windows 读取 WSL worktree 的固定版本快照、同目录旧服务/历史 pinned 服务被新启动器拒绝。

以上是一次开发验收；未提供独立留出准确率、真人哼唱质量、盲听优劣、生成音频的精确音符保留或实时持续演奏保证。
