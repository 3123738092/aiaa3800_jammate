# JamMate 原版接入与验收

打开 [本机工作台](http://127.0.0.1:4188/)，或双击本工作区的 `start-input-lab.cmd`。代码在 Farewellxxxx/3800-HAI-Project 的 `integration/style-llm-creation` 分支。

| 入口 | 操作 | 可调设置 |
|---|---|---|
| 从旋律出发 | 哼一句、导入或载入示例，转成 MIDI 后生成。先听识别，再听变奏。 | 保留现有哼唱选项和试听音色。 |
| 从律动出发 | 载入示例或先拍四下定速，再拍一小节。识别后用同一份节拍依次生成跟随、加花、简化。 | 自动或手动 BPM、小节起点、原版三种编排、力度级别、小节数。 |
| 从一句话出发 | 用「来一段，慢一点」生成，接着试「去掉贝斯」。每次继续调整生成作品的下一段。 | 选择大模型或原版本地规则。开新作品时可设起始 BPM 与 FluidSynth 音色。 |
| 试听队友的风格预设 | 在首页展开预设区，选择 Lo-fi、Funk、Indie Rock 并分别生成。 | 这些是独立风格样段，三者使用同一速度和音色，鼓点和贝斯走法来自队友的风格实验。 |

文字作品的初始设置在生成后固定，可用指令继续调整同一作品。网页显示和下载当前段落，想留对比可先下载再继续。按「开始新作品」重新开始。会话保存在本机进程内，服务重启后需要新建作品。

## 代码来源与范围

从 `9da4027` 开发。队友的固定版本是 `a63a0f5f579cbc36ed0dfa31874c8888e4873cbd`，已经在本仓库的 Git 历史里。首次调用时从该提交提取 `src/jammate` 和 `renderer_experiments/app`，隔离加载并核对模块来自该快照。拍手调用原版 `analyze_rhythm_audio` 和 `jam_rhythm`。文字调用原版 `from_text`、`LLMAgent` 或 `RuleAgent`、`StateManager` 和渲染器。网页接入层负责设置、序号、重试和媒体回传。

原版风格试验调用 `MusicIntent`、`render_pattern` 和 FluidSynth。原版节拍编排的选项是跟随、加花和简化；它没有把这些风格预设套到已有拍手上的接口。因此首页预设区提供独立对比，拍手和哼唱继续使用各自的音乐来源。

## 大模型配置

文字页检查原版 `LLMConfig`。安装项目的 `llm` 可选依赖后，本机若已有授权的 `OPENAI_API_KEY`，或有 `JAMMATE_LLM_CONFIG` 指向的私有配置，可重启服务后选择大模型。依赖安装见 [启动说明](RUN.md) 和 [原版 LLM 说明](../LLM_AGENT.md)。密钥与配置留在仓库外。

大模型入口只提交文字和本作品的结构化状态。录音、原始音频和其他入口的输入留在本机。配置缺失时可选择原版本地规则；网页明确显示所用 Agent。

## 试听产物与验证

- 哼唱保留原有页面和逻辑。浏览器用实际 Basic Pitch 识别五音示例和一个持续音，再经 Kevin / FluidSynth 导出变奏。[哼唱原录音](../../examples/creation/humming-source.wav) · [变奏](../../examples/creation/humming.wav)
- 节拍使用同一录音和约 100.45 BPM。读回 MIDI 确认跟随保留全部原拍点，加花增加拍点，简化保留主要重音。[跟随](../../examples/reference-creation/rhythm-follow.wav) · [加花](../../examples/reference-creation/rhythm-vary.wav) · [简化](../../examples/reference-creation/rhythm-simplify.wav)
- 文字浏览器示例选择原声钢琴，先生成 92 BPM，再用「去掉贝斯」调整下一段。读回 MIDI 确认真实乐器配置、速度与贝斯变化。成功响应被人为丢弃后重试仍只生成一次。[第一段](../../examples/reference-creation/text-first.wav) · [去掉贝斯](../../examples/reference-creation/text-adjusted.wav)
- 风格参考使用队友原版代码从预设生成。读取各自的 MIDI，鼓点不同，速度均为 100 BPM。[Lo-fi](../../examples/reference-creation/lofi.wav) · [Funk](../../examples/reference-creation/warm_funk.wav) · [Indie Rock](../../examples/reference-creation/indie_rock.wav)

437 项 Python、30 项 JavaScript、两套前端构建和五组 Edge 浏览器检查通过。覆盖生成、解码 WAV、MIDI 下载、失败重试、切换入口和录音生命周期。麦克风测试使用合成录音或虚拟设备。当前机器无已授权大模型凭据，真实服务联调待验；测试用脚本替代外部服务回复，真实运行原版 Agent、计划校验、历史传递和渲染逻辑。

[音频入口测试记录](../../examples/reference-creation/audio-report.json) · [文字与风格测试记录](../../examples/reference-creation/report.json)
