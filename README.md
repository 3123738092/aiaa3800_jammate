# JamMate

AIAA 3800 课程项目。JamMate 是一个本地音乐创作工作台，你可以哼一段旋律、拍一段节奏，或用一句话开始创作。三个入口都能单独生成乐队片段，支持浏览器试听、下载 WAV 和导出 MIDI。

## 三种创作方式

| 入口 | 怎么试 | 生成结果 |
| --- | --- | --- |
| **从旋律出发** | 哼一句或载入示例，点「转成 MIDI」。比较原始识别和连贯哼唱，选好后点「生成音乐」。 | 在识别出的旋律上做变奏，加入鼓、贝斯和键盘。[原录音](examples/creation/humming-source.wav) · [变奏试听](examples/creation/humming.wav) |
| **从律动出发** | 载入示例，或均匀拍四下定速后再拍一小节。点「识别节拍」，选跟随、加花或简化，再生成。 | 用拍点和重音编排乐队，可调力度和小节数。[跟随](examples/reference-creation/rhythm-follow.wav) · [加花](examples/reference-creation/rhythm-vary.wav) · [简化](examples/reference-creation/rhythm-simplify.wav) |
| **从一句话出发** | 输入「来一段，慢一点」，生成后再输入「去掉贝斯」。 | 根据要求生成第一段，并沿用作品状态编排下一段。[第一段](examples/reference-creation/text-first.wav) · [调整后](examples/reference-creation/text-adjusted.wav) |

首页也提供独立的风格样段。[Lo-fi](examples/reference-creation/lofi.wav) · [Funk](examples/reference-creation/warm_funk.wav) · [Indie Rock](examples/reference-creation/indie_rock.wav)

## 在 Windows 上运行

已在 Python 3.12、Node.js 24 和 pnpm 11 上验证。装好 Git 和这些工具后，在 PowerShell 运行以下命令。工作台从 Git 历史读取固定版本，请完整克隆。评审 PR 时，把第一行替换为 PR 描述里的分支克隆命令。

```powershell
git clone https://github.com/3123738092/aiaa3800_jammate.git
cd aiaa3800_jammate
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" -c requirements-input.lock.txt
pnpm --dir web/baseline install --frozen-lockfile
pnpm --dir web/baseline run build
pnpm --dir web/optimized install --frozen-lockfile
pnpm --dir web/optimized run build
.\start-input-lab.cmd
```

启动后在本机打开 [创作工作台](http://127.0.0.1:4188/)。以后可直接双击 `start-input-lab.cmd`。如果要在页面现生成风格样段，或使用 FluidSynth 音色，请先配置 FluidSynth。[音源配置与运行说明](docs/integration/RUN.md#音源与验收)

## 一起 Jam 的总麦克风

总麦克风使用讯飞语音听写大模型（中英）V2.0 把录音转成文字；Gemini 3.5 Flash-Lite 只接收转写文字，负责理解用户意图，不再接收音频。完成上面的环境安装和前端构建后，在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[llm]"
Copy-Item audio-router.example.json audio-router.secrets.json
Copy-Item xfyun-asr.example.json xfyun-asr.secrets.json
```

首次配置时，在 `xfyun-asr.secrets.json` 填写讯飞控制台中的 `app_id`、`api_key` 和 `api_secret`。该接口使用 `wss://iat-api.xfyun.cn/v2/iat`，三项凭据缺一不可。也可用 `JAMMATE_XFYUN_ASR_CONFIG` 指向仓库外的私有配置文件。

在 `audio-router.secrets.json` 填写 Gemini 网关的 `api_key` 和 `base_url`。该地址只需支持 OpenAI Chat Completions 兼容的文字请求和 JSON Schema 结果，不再需要音频输入能力。也可将 `JAMMATE_AUDIO_ROUTER_CONFIG` 指向仓库外的私有配置文件。两个 `*.secrets.json` 均被 Git 忽略。

如果讯飞因额度、授权或网络错误不可用，服务会读取已有的 `DASHSCOPE_API_KEY`，自动使用百炼 `qwen-audio-3.1-asr-flash` 转写同一段 WAV。可用 `JAMMATE_QWEN_ASR_MODEL` 覆盖模型；配置 `DASHSCOPE_WORKSPACE_ID` 后会优先使用北京地域的 workspace 专属域名。备用链路只在讯飞失败后调用，不会为同一段成功录音同时消耗两家额度。

运行 `.\start-jam-mic.cmd`，打开 [JamMate Studio](http://127.0.0.1:4221/jam.html)。从输入栏选择说话、哼唱或拍手，再点击麦克风录制。原版设备工作台可从设置打开。[Studio 操作说明](docs/JAM_STUDIO_UI.md)

## 文字入口的大模型选项

本分支新增 FluidSynth / MRT2 执行适配与规则/LLM 共用入口。MRT2 生成试听会明确报告无法保证的保留约束，不提交未经验证的音乐状态；独立输入文件、运行命令和官方接口说明见 [MRT2 后端](docs/MRT2_BACKEND.md)。服务默认加载当前工作目录代码，使用 `--implementation pinned` 可复现历史版本。

文字页默认用本地规则，装好环境后，三个入口都能在本地试用。开启「队友大模型 Agent」后，模型根据文字和作品状态给出编排动作，例如「移除贝斯」。JamMate 校验动作，再生成具体音符和音频。

要启用大模型，在项目目录运行 `.\.venv\Scripts\python.exe -m pip install -e ".[llm]"`，配置自己的 `OPENAI_API_KEY`，或让 `JAMMATE_LLM_CONFIG` 指向仓库外的私有配置。重启服务后，在文字页选择「队友大模型 Agent」。[配置与验证说明](docs/integration/REFERENCE_WORKBENCH.md#大模型配置)

「高级设置 → 音符编排」可单独启用 LLM，调整计划授权声部的实际音符。网页可查看编排摘要，模型请求、原始回复与采纳结果写入本地日志。[操作与日志说明](docs/LLM_ARRANGEMENT.md)

## 实现与来源

哼唱由 Spotify Basic Pitch 提取音符，接上我们的单声部整理和变奏。拍手与文字调用队友 `a63a0f5` 版本的节拍编排和文字 Agent。三种入口都按小节生成乐器音符，拍手使用原版内置合成器，哼唱和文字可以用 Kevin 渲染器及 FluidSynth 发声。[实现与验收](docs/integration/REFERENCE_WORKBENCH.md) · [团队原版命令行和研发记录](https://github.com/3123738092/aiaa3800_jammate/blob/a63a0f5f579cbc36ed0dfa31874c8888e4873cbd/README.md)
