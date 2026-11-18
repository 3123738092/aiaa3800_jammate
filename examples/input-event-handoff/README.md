# 三个网页入口的 InputEvent 样例

每个输入文件旁边都有本地工作台实际导出的事件。两段 WAV 均为合成音频。可以把 JSON 直接送入消费端做回归，或将同名输入导入工作台，核对音乐内容。重新识别时，事件 ID 和分析耗时可以变化。

| 输入 | 导出 | 验收 |
| --- | --- | --- |
| [键盘文字](text.txt) | [text / speech · 1.0.0](text.input-event.json) | `payload.text` 逐字符保留原文，包括开头的两个空格和末尾的换行。由 Harness 结合会话上下文解释这句话。 |
| [哼唱](humming.wav) | [humming / musical_phrase · 1.2.0](humming.input-event.json) | 5.5 秒，选中的旋律有 6 个音。读取 `payload.melody.notes` 中的 `pitchMidi`、`startSec`、`durationSec`。起音从录音开头计时。 |
| [自由节奏](rhythm.wav) | [rhythm / rhythm_seed · 1.3.0](rhythm.input-event.json) | 7.15 秒，输入有 10 次合成拍击，当前识别出 8 次。以 `payload.rhythm.onset_seconds` 和同索引的 `accents` 成对读取时间和相对力度。 |

### 下游需要适配什么

团队工作台基线 `5dc9fb8` 的 Python 校验器可以读入文字和哼唱。自由节奏沿用 `event_type=rhythm_seed`，新增了 1.3.0 的秒制表示。基线校验器会拒绝这份事件。接入时请同时兼容旧节奏事件的 `onset_beats`。

自由节奏的 `payload.rhythm` 是编辑后的当前拍点，`payload.raw_rhythm` 是首次识别结果。本样例尚未编辑，两者相同。`payload.audio_duration_s` 保留录音总长度，包括首尾静音。若当前拍点为空，`payload.status` 为 `no_onsets`。三个样例的 `confidence` 均为 `null`，表示没有校准后的置信度。

从已安装项目依赖的仓库根目录运行以下命令。每个文件独立校验。支持 1.3.0 后，三条命令均应返回 `valid: true`。

```shell
python -m jammate.contracts validate examples/input-event-handoff/text.input-event.json
python -m jammate.contracts validate examples/input-event-handoff/humming.input-event.json
python -m jammate.contracts validate examples/input-event-handoff/rhythm.input-event.json
```

这些样例验收到输入事件这一层。键盘样例覆盖原文传递，麦克风转写需另测。节奏样例中的两次漏检按实保留。哼唱样例覆盖干净录音，背景噪声和同音再起仍需单独测试。
