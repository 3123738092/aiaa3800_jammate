# 从三个网页入口生成 InputEvent

从仓库根目录完成 [工作台安装](../../README.md#在-windows-上运行)，打开工作台后可直接使用自己的输入。只测试输入时，启动 `python -m jammate.input_server --port 4188` 即可，哼唱使用 `web/optimized` 构建的本地模型。此路径无需配置 LLM、MRT2、GPU 或音源。

只验收这三个入口时，以下命令可在 Windows、macOS 或 Linux 的已激活 Python 虚拟环境运行。使用 Python 3.12、Node 24 和 pnpm 11，首次安装需要联网。

```shell
git clone --branch codex/input-event-handoff https://github.com/3123738092/aiaa3800_jammate.git
cd aiaa3800_jammate
python -m pip install -e ".[dev]" -c requirements-input.lock.txt
pnpm --dir web/optimized install --frozen-lockfile
pnpm --dir web/optimized run build
python -m jammate.input_server --port 4188
```

打开 [本地工作台](http://127.0.0.1:4188/)。

1. **文字**　打开「从一句话出发」，输入任意创作提示，点击「下载 InputEvent JSON」。按钮直接调用 `POST /api/input/text` 和 Python 的 `from_text`，原文、标点、空格和换行都会保留。
2. **哼唱**　打开「从旋律出发」，录音或导入音频，点击「转成 MIDI」。默认使用 Basic Pitch＋D 和按 BPM 调整的短音过滤，可在「对比版本」里试听旧版 D。点击音轨可编辑音符，试听满意后点「确认旋律」，再下载 MIDI 或 InputEvent。手动补回的快音直接进入确认结果。
3. **拍手**　打开「从律动出发」，录一段或导入 WAV，点击「识别节奏」。编辑拍点或力度后，可试听、下载 MIDI 和 InputEvent。识别器复用原仓库的 librosa 起音检测、回溯和力度筛选。常规 PCM WAV 直接按原采样率分析。旧版单小节编排另有入口。

### 接入 Harness

三个入口导出的 JSON 都能交给 `InputEvent.from_dict(data)`，文件也可用 `load_input_event(path)` 读取。按 `source`、`event_type` 和 `schema_version` 分派。样例中的会话和事件 ID 用于回放测试；在同一个实际 Jam 中，调用方应传入共同的 `session_id`，重试时沿用 `event_id`。独立演示页各自建立会话。

| 入口 | 可复用接口 | 输入与返回 |
| --- | --- | --- |
| 键盘文字 | `POST /api/input/text` | JSON 含 `text`、`session_id`，可传 `event_id`。直接返回事件。 |
| 拍手 | `POST /api/input/rhythm` | JSON 含 base64 编码的 WAV 字段 `wav` 和 `session_id`，可传 `event_id`、`started_at`。直接返回事件。 |
| 哼唱 | [网页识别](../../web/optimized/src/app.js) → [hummingEvent](../../web/optimized/src/input-event.js) | 浏览器从音频生成 Melody 文档，封装函数接收该文档及 `sessionId`、`eventId`、`startedAt`，返回标准事件。 |

三个页面都支持 `jammate:input`。页面初始化时注册一次，就能收到与下载 JSON 相同的事件。文字提交或导出时发布，拍手识别或编辑后发布。哼唱在点击「确认旋律」时发布，编辑草稿期间可试听，下载保留最近一次确认的结果。自动哼唱使用 1.2.0，编辑后确认的哼唱使用 1.4.0。重复导出同一条文字时可按 `event_id` 去重。

```js
window.addEventListener('jammate:input', ({detail}) => {
  // 在此将 detail 交给自己的 Harness。
  console.log(detail.event_id, detail.source, detail.payload);
});
```

消费端将事件与会话状态一起交给 Harness，再决定编排并调用 Renderer。MIDI 和页面中的 MRT2 输入文件是附加导出。

### 用同一份输入复验

每个输入文件旁边都有本地工作台实际导出的事件。两段 WAV 均为合成音频。可以把 JSON 直接送入消费端做回归，或将同名输入导入工作台，核对音乐内容。重新识别时，事件 ID 和分析耗时可以变化。

| 输入 | 导出 | 验收 |
| --- | --- | --- |
| [键盘文字](text.txt) | [text / speech · 1.0.0](text.input-event.json) | `payload.text` 逐字符保留原文，包括开头的两个空格和末尾的换行。由 Harness 结合会话上下文解释这句话。 |
| [哼唱](humming.wav) | [humming / musical_phrase · 1.2.0](humming.input-event.json) | 5.5 秒，选中的旋律有 6 个音。读取 `payload.melody.notes` 中的 `pitchMidi`、`startSec`、`durationSec`。起音从录音开头计时。 |
| [自由节奏](rhythm.wav) | [rhythm / rhythm_seed · 1.3.0](rhythm.input-event.json) | 7.15 秒，输入有 10 次合成拍击，当前识别出 8 次。以 `payload.rhythm.onset_seconds` 和同索引的 `accents` 成对读取时间和相对力度。 |

### 下游需要适配什么

本分支的 Python 校验器已支持以上三种事件。自由节奏沿用 `event_type=rhythm_seed`，1.3.0 使用秒制表示。团队基线 `5dc9fb8` 的校验器最高支持 1.2.0。消费端接入时请同时兼容旧节奏事件的 `onset_beats`。

自由节奏的 `payload.rhythm` 是编辑后的当前拍点，`payload.raw_rhythm` 是首次识别结果。本样例尚未编辑，两者相同。`payload.audio_duration_s` 保留录音总长度，包括首尾静音。若当前拍点为空，`payload.status` 为 `no_onsets`。三个样例的 `confidence` 均为 `null`，表示没有校准后的置信度。

从使用项目 Python 环境的终端，在仓库根目录运行以下命令。三条命令均应返回 `valid: true`。

```shell
python -m jammate.contracts validate examples/input-event-handoff/text.input-event.json
python -m jammate.contracts validate examples/input-event-handoff/humming.input-event.json
python -m jammate.contracts validate examples/input-event-handoff/rhythm.input-event.json
```

这些样例验收到输入事件这一层。键盘样例覆盖原文传递，麦克风转写需另测。节奏样例中的两次漏检按实保留。哼唱样例覆盖干净录音，背景噪声和同音再起仍需单独测试。

回归测试可运行 `python -m pytest tests/test_input_event_handoff.py` 和 `pnpm --dir web/optimized test`。浏览器验收脚本为 `node tests/input-event-handoff.mjs`，需要运行中的服务和 Playwright。脚本默认启动 Edge；`PLAYWRIGHT_MODULE_PATH` 可指定已安装的 Playwright，`JAMMATE_BROWSER_CHANNEL` 和 `JAMMATE_INPUT_URL` 可覆盖浏览器与服务地址。
