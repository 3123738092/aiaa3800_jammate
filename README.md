# JamMate

AIAA 3800 课程项目。这个分支提供从键盘文字、哼唱和自由拍手生成标准 `InputEvent` 的工作台。三个入口均可独立验收。[输入接口、可运行样例和验收命令](examples/input-event-handoff/README.md)

## 三种创作方式

| 入口 | 怎么试 | 生成结果 |
| --- | --- | --- |
| **从旋律出发** | 哼一句或导入音频，点「转成 MIDI」。可切换对比版本、试听、增删音符、改起止时间、音高和力度，或撤销编辑。 | 点「确认旋律」后，可下载本次确认的 MIDI 和 InputEvent。 |
| **从律动出发** | 自由拍一段或导入音频，点「识别节奏」。 | 调整拍点和力度，试听并下载 MIDI 和 InputEvent。旧版四拍定速编排可从首页另行进入。 |
| **从一句话出发** | 输入「来一段，慢一点」，点「下载 InputEvent JSON」。 | 下载原文事件，也可另点「生成第一段」体验团队已有的文字编排。 |

首页也提供独立的风格样段。[Lo-fi](examples/reference-creation/lofi.wav) · [Funk](examples/reference-creation/warm_funk.wav) · [Indie Rock](examples/reference-creation/indie_rock.wav)

## 在 Windows 上运行

已在 Python 3.12、Node.js 24 和 pnpm 11 上验证。装好 Git 和这些工具后，在 PowerShell 运行以下命令。工作台从 Git 历史读取固定版本，请完整克隆。评审 PR 时，把第一行替换为 PR 描述里的分支克隆命令。

```powershell
git clone --branch codex/input-event-handoff https://github.com/3123738092/aiaa3800_jammate.git
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

## 文字入口的大模型选项

本分支新增 FluidSynth / MRT2 执行适配与规则/LLM 共用入口。MRT2 生成试听会明确报告无法保证的保留约束，不提交未经验证的音乐状态；独立输入文件、运行命令和官方接口说明见 [MRT2 后端](docs/MRT2_BACKEND.md)。服务默认加载当前工作目录代码，使用 `--implementation pinned` 可复现历史版本。

文字页默认用本地规则，装好环境后，三个入口都能在本地试用。开启「队友大模型 Agent」后，模型根据文字和作品状态给出编排动作，例如「移除贝斯」。JamMate 校验动作，再生成具体音符和音频。

要启用大模型，在项目目录运行 `.\.venv\Scripts\python.exe -m pip install -e ".[llm]"`，配置自己的 `OPENAI_API_KEY`，或让 `JAMMATE_LLM_CONFIG` 指向仓库外的私有配置。重启服务后，在文字页选择「队友大模型 Agent」。[配置与验证说明](docs/integration/REFERENCE_WORKBENCH.md#大模型配置)

## 实现与来源

哼唱使用 Spotify Basic Pitch 和单声部整理。自由拍手复用团队的 librosa 起音识别函数。键盘文字使用团队已有的原文适配器。旧版编排与渲染的说明见 [团队工作台](docs/integration/REFERENCE_WORKBENCH.md)。
