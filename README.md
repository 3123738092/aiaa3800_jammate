# JamMate

JamMate 是 AIAA 3800 的浏览器音乐工作台。你可以用一句话、哼唱、拍手或 MIDI 开场，之后边听边调整乐队。工作台显示每次指令的处理结果，也能导出这场演奏的 WAV。

## 在 Windows 上启动

用 Python 3.12 在 PowerShell 里运行。首次试用只需要本地规则和内置合成器。

```powershell
git clone https://github.com/3123738092/aiaa3800_jammate.git
cd aiaa3800_jammate
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" -c requirements-input.lock.txt
.\.venv\Scripts\python.exe -m jammate.input_server --port 4188
```

打开 [Studio](http://127.0.0.1:4188/jam.html)，先确认唱机屏幕显示「已连接乐队」。如果端口已经被旧版本占用，可以改用 `--port 4199`，再打开 [4199 上的 Studio](http://127.0.0.1:4199/jam.html)。

## 试一轮 Jam

1. **开场。** 在输入栏发送「来个鼓点」，或者点唱机的播放键开始全乐队伴奏。也可以空等约 8 秒，看看乐队是否提议一个短鼓点或旋律。点「试试」才会采用提议。
2. **边听边改。** 演奏时发送「鼓少一点」。如果只想对一个声部说话，先左右切换到对应乐器，再点乐器名称，把它设为输入目标。
3. **看结果。** 输入栏上方显示乐队回应。右上角「运行日志」可以查看输入、编排、渲染和播放回执。实际应用后，唱机与和声屏才更新相应的演奏状态。
4. **保留演奏。** 播放后可点「导出演奏 WAV」。唱机键负责暂停或继续。在「设置」里点「结束会话」，就能换音频后端或重新开场。

### 还可以怎么输入

| 输入 | 在 Studio 里怎么用 |
| --- | --- |
| **说话** | 选「说话」，点一次麦克风。只说「Hey JamMate」，听到「我在」再说指令。也可叫「Hey 节奏组」「Hey 旋律组」，或一句话说完「Hey JamMate，速度调到 108」。停顿后自动提交。 |
| **哼唱、拍手** | 选对应模式，点麦克风开始，再点一次结束。音频作为素材放进输入栏，可先试听，再单独发送或附上一句说明。 |
| **键盘、MIDI** | 在旋律组点「录入旋律」，用屏幕琴键或通过「连接 MIDI」接上的外部键盘演奏。点「完成」和「加入输入」，再提交。已有 `.mid` / `.midi` 文件可通过附件导入。 |

文字和 MIDI 可以直接试。哼唱、拍手录音需要浏览器麦克风权限。说话模式另外需要听写服务。开启唤醒后，有声音的片段会发送给听写服务来判断是否叫了乐队。没有唤醒词的普通讲话不会作为乐队指令提交。关闭麦克风或切出页面会停止收音。

## 接语音和大模型

第一次接云服务时，在仓库根目录安装依赖。以下命令请在启动 Studio 服务的同一个 PowerShell 窗口执行。

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[llm]"
if (-not (Test-Path xfyun-asr.secrets.json)) { Copy-Item xfyun-asr.example.json xfyun-asr.secrets.json }
```

- **讯飞听写。** 在 `xfyun-asr.secrets.json` 中填写流式听写服务的 `app_id`、`api_key` 和 `api_secret`。如果已有文件，直接检查现有配置即可。也可用 `JAMMATE_XFYUN_ASR_CONFIG` 指向其他位置。讯飞调用失败时，有 `DASHSCOPE_API_KEY` 才会尝试百炼备用。
- **ChatAnywhere Agent。** 创建 `chatanywhere-llm.secrets.json`，填写自己的密钥和有权限的模型。当前联调使用 `gpt-5.4-mini`。在启动前设定 `$env:JAMMATE_LLM_CONFIG = (Resolve-Path .\chatanywhere-llm.secrets.json).Path`。

```json
{
  "api_key": "填写你自己的密钥",
  "base_url": "https://api.chatanywhere.tech/v1",
  "api": "chat_completions",
  "model": "gpt-5.4-mini",
  "reasoning_effort": "none",
  "timeout_s": 28
}
```

`*.secrets.json` 已被 Git 忽略。填好配置后启动或重启服务，在「设置」查看语音和模型状态。Studio 检测到模型配置时默认使用「大模型 Agent」。要只试本地规则，可以切回「本地规则」。语音用户的音频由听写服务处理，大模型收到的是转写后的指令。

### 设置里的三个选项

| 选项 | 负责什么 |
| --- | --- |
| **指令处理** | 根据用户输入和当前演奏，决定改什么。可选本地规则或大模型 Agent。 |
| **音符编排** | 把通过校验的计划写成具体音符。默认本地规则，也可单独选大模型编排。 |
| **音频后端** | 把音符变成声音。内置合成器可直接用，FluidSynth 需要 SF2 音色库。MRT2 独立试听和实时伴奏各有运行环境要求。 |

MRT2 可从「内置伴奏 + MRT2 试听」试起，生成的音频在工作台底部单独播放。流式和独立生成选项是否可用，以当前服务的设置页为准。本地配置和后端限制见 [MRT2 与 FluidSynth 文档](docs/MRT2_BACKEND.md)。

## 交互与联调

```text
文字 / 听写 / 音频 / MIDI → InputEvent
InputEvent + 当前 JamState → 指令处理 → BandPlan
BandPlan → 音符编排 → 音频渲染 → 小节播放回执
```

- 「已提交」表示服务收到输入。处理中的计划可能要等到可用的小节才生效。以「已完成」和日志中的播放回执核实实际变更。
- 空闲时的乐队提议由用户决定是否采用。开场只给一个短鼓点或旋律，演奏中接受的建议由后端安排到可用小节。
- 要查看原键盘和旧版日志，从「设置」打开经典工作台。更多交互细节见 [Studio 操作说明](docs/JAM_STUDIO_UI.md)。

## 独立创作页面与试听样例

首页的「从旋律出发」「从律动出发」「从一句话出发」仍可单独试用。运行这几条流程需要额外构建 `web/baseline` 和 `web/optimized`，完成后可用 `start-input-lab.cmd` 打开首页。两套前端的构建命令、历史基线与验收步骤见 [输入工作台运行说明](docs/integration/RUN.md)。

| 示例 | 试听 |
| --- | --- |
| 哼唱与变奏 | [原录音](examples/creation/humming-source.wav) · [变奏](examples/creation/humming.wav) |
| 拍手与节奏编排 | [跟随](examples/reference-creation/rhythm-follow.wav) · [加花](examples/reference-creation/rhythm-vary.wav) · [简化](examples/reference-creation/rhythm-simplify.wav) |
| 文字开场与调整 | [第一段](examples/reference-creation/text-first.wav) · [调整后](examples/reference-creation/text-adjusted.wav) |

经典工作台的总麦克风还可配置单独的意图分类器，模板见 [audio-router.example.json](audio-router.example.json)。团队原版命令行与研发记录见 [历史 README](https://github.com/3123738092/aiaa3800_jammate/blob/a63a0f5f579cbc36ed0dfa31874c8888e4873cbd/README.md)。
