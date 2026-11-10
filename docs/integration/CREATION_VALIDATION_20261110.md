# 单输入创作验收 · 2026-11-10

新分支 `integration/single-input-creation` 从 `f30b855` 开始。先验收哼唱、节拍并提交 `e29b15c`，之后接入文字。日常试用见 [启动与操作](RUN.md)。

| 入口 | 实际浏览器验证 | 可试听的产物 |
|---|---|---|
| 哼唱 | Basic Pitch 识别了五个音 `60, 64, 67, 64, 62`。保存的变奏为 `64, 67, 71, 67, 64`，保留了原句的节奏间距。另测了单个持续音。 | [原录音](../../examples/creation/humming-source.wav) · [作品 WAV](../../examples/creation/humming.wav) · [MIDI](../../examples/creation/humming.mid) |
| 节拍 | 识别速度约 100.45 BPM。第一个小节从 `[0, 1, 1.5, 3]` 发展为 `[0, 0.25, 1, 1.5, 3]`，保留拍点并加入一个装饰拍。更快的录音使用自己的识别速度。 | [原录音](../../web/rhythm-example.wav) · [作品 WAV](../../examples/creation/rhythm.wav) · [MIDI](../../examples/creation/rhythm.mid) |
| 文字 | 从节拍页切换到空白文字入口。独立输入「来一段，慢一点」和「来一段，快一点」，MIDI 分别为 92 和 108 BPM。有不支持的要求时返回错误，改提示后可再次生成。 | [慢一点 WAV](../../examples/creation/text.wav) · [MIDI](../../examples/creation/text.mid) |

- 两个音频入口通过后，阶段测试为 427 项 Python、30 项 JavaScript、两套前端构建和四组 Edge 浏览器脚本。音频先通过识别、编排和 Kevin / FluidSynth，再由浏览器解码 WAV、读回下载的 MIDI。
- 文字接入后，430 项 Python 和 30 项 JavaScript 通过。浏览器测试确认文字只提交本次提示，支持重试、成功下载、切换入口和手机宽度，且拒绝不支持的请求。
- 录音回归使用虚拟麦克风，并验证待处理的权限、识别和解码不会覆盖新录音。生成接口模拟一次 503 后可以重试。测试和版本说明中保留了采用本地规则、默认调性及真人试听的边界。
- 试听产物来自合成录音或公开的示例提示。自动验收检查可播放、音乐内容和输入关系，真人麦克风识别及音乐听感还需要实际试听。

[音频浏览器记录](../../examples/creation/audio-browser-report.json) · [文字浏览器记录](../../examples/creation/text-browser-report.json)
