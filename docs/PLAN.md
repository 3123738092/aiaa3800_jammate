# JamMate：可信度评估、设计与计划

> 2026-10-31 阅读提示：职责与 SPEC 对齐见第 9 节，InputEvent 见第 10 节，状态/计划闭环见第 11 节，最新 LLM Agent 接入与实测见第 12 节。第 1–11 节保留各阶段记录；“尚未实现”等状态应以第 12 节和当前代码为准。

> 依据：组内 Notion 页「Group Project」（2026-10-15 导出），以及表中各项技术的官方仓库和文档（同日核对）。

## 1. Notion 页面现状

页面分两部分：

- **上半：HCAI 选题 14 问模板。** 只填了「2. 情境」（学习、科研、游戏、运动、听歌、唱歌、老人独居等候选）和「4. 人体信号」中的一项（心电）。其余 12 项都是空的：用户是谁、痛点、AI 推断的人的状态、交互方式、自适应、人的主导权、基线、假设、评估、30 秒演示、新颖性。
- **下半：JamMate 构想。** 包括产品定位（有乐感、缺乐理的普通人，凑不齐乐队）、核心体验（"我抛一点东西出来，AI 接住；AI 又抛一点东西回来，我再接"）、输入方式（哼唱、语音、手势、弹奏），以及架构图和技术选型表。

**缺口：** 课程评分要求讲清动机、相关工作、方法、实验过程和关键发现（第 1 讲 p10）。目前这些都还没有：没有研究问题、基线、假设和评估指标。第 4 节给出建议。

## 2. 技术选型表逐条核实

结论分四档：✅ 与一手来源一致；⚠️ 部分成立或只有二手来源；❌ 对我们的硬件不成立；❓ 官方没写。

| # | 表中声明 | 一手来源怎么说 | 结论 |
|---|---|---|---|
| 1 | MRT2 Small（2.3 亿参数）能在任意 Apple Silicon Mac 上实时生成 | GitHub README："runs real-time on any Apple Silicon Mac, including Air models" | ✅ |
| 2 | MRT2 控制延迟约 200 ms | 官方 README 和应用页都没给数字；200 ms、40 ms 帧只见于第三方文章 | ⚠️ 需要在 Mac 上实测 |
| 3 | MRT2 能接受文本、音频、MIDI 引导 | 官方应用页确认三种都有：文本提示；短音频片段作为音色或风格提示；按住音符或和弦引导和声。但**没有说能用实时麦克风输入来引导** | ⚠️ 哼唱要先转成 MIDI 再送进去 |
| 4 | MRT2 Base（24 亿参数）需要 M3 Pro+ / M2 Max+ | 应用页："requires an M3 Pro / M2 Max or higher"。README 的表格和正文对此略有出入 | ✅ |
| 5 | MRT2 本地部署 | 实时流式**只支持 Apple Silicon**；NVIDIA GPU 只能做离线（非实时）生成 | ❌ 对本机（RTX 4060 Laptop，Windows）不成立 |
| 6 | 用 Python 接入 MRT2 | Python 库有 JAX 和 MLX 后端；实时流式靠 C++ 引擎、macOS app 和 AUv3 插件；`examples/jam` 是 Objective-C++ 写的 | ⚠️ 最现实的接法是"我们的指挥层 → 虚拟 MIDI 口 → DAW 里的 MRT2 插件"，还没验证 |
| 7 | MRT2 权重可以用 | 官方称"open-weights"，但两个官方页面都没写许可证 | ❓ 使用前要看 Hugging Face 模型卡 |
| 8 | CREPE 哼唱转音高，本地 CPU 几十到上百 ms | 小模型量级合理，完整模型在 CPU 上偏慢 | ⚠️ MVP 改用 librosa pYIN：不用下载模型，短语级分析够用 |
| 9 | Basic Pitch：Windows 用 ONNX，Linux 用 TFLite，macOS 用 CoreML | README 一致；只支持 Python ≤ 3.11；按文件转写，不支持流式 | ✅ 正好用于"一句唱完再转写"，与表中定位一致 |
| 10 | whisper.cpp base 约 388 MB、small 约 852 MB，支持 CPU、NVIDIA、Apple | README 内存表一致；有 `whisper-stream` 实时示例（约每 0.5 s 采样一次） | ✅ |
| 11 | MediaPipe 手势识别在 Pixel 6 CPU 上平均 16.8 ms | 官方 16.76 ms（GPU 20.87 ms）。**但默认模型只有 7 个预置手势** | ✅ "慢一点""更有力"这类指令要映射到这 7 个手势上，或者另外训练 |
| 12 | aubio / librosa 做节拍和起始点检测 | 成熟库 | ✅ 但用几下拍手估速度，得用拍间间隔的中位数；`beat_track` 需要更长的音频 |
| 13 | MIDI + SoundFont 做精确伴奏控制，"必须有" | 标准做法 | ✅ |
| 14 | LLM 乐队指挥每 1–4 小节调用一次 API | 可行，但 API 往返要 0.5–3 s，不能放在关键路径上 | ⚠️ 需要规则兜底，已实现 |
| 15 | Unity 虚拟乐手 | 展示层，可选 | 不进 MVP |

**总体判断：** 选型表的方向是对的，大部分数字和官方一致。最大的风险集中在 MRT2：

1. 实时生成只能在 Apple Silicon 上跑；
2. 约 200 ms 的延迟没有官方出处；
3. 哼唱不能直接作为音频提示送进去。

**对策：** 先搭一条不依赖 MRT2、在任何电脑上都能跑通的完整链路，也就是表里的 "MIDI / Loops 控制层"：

> 哼唱 / 拍手 / 文字指令 → 共享音乐状态 → 规则指挥 → MIDI 伴奏与回应 → 声音

MRT2 作为可替换的"生成层"，等组里确认有 Apple Silicon Mac 再接进来。

## 3. 前作与新颖性

"你抛我接"的人机即兴已经有不少前作：

- GenJam（Biles 1994，爵士轮奏）
- Continuator（Pachet 2003，用变阶马尔可夫链续写演奏者的乐句，和专业乐手、儿童都做过实验）
- AI Duet（Google 2016）
- BachDuet（NIME 2020，MIDI 键盘上的呼应式二重奏，13 名受过音乐训练的被试，加 48 人的图灵式听辨测试）
- ReaLJam（CHI EA 2025，用强化学习调过的 Transformer 实时伴奏，会把下一步计划显示出来）
- MoMusic（AAAI 2023，第 11 讲引用：动作驱动的人机共创作曲）

ReaLJam 在局限里明确写道：还需要研究**怎样同时支持专家和新手**。所以 JamMate 的差异点应该放在：

- **非专业用户**：不会乐器，只会哼、拍、说；
- **多模态引导**：哼唱、语音、手势都映射到同一组意图；
- **可理解、可撤销的 AI**：每个决定都带一句理由，用户可以喊停或换个方向。

14 问里的「14. 与直接用 ChatGPT 有何不同」可以这样回答：JamMate 是实时的、以声音为媒介的轮流互动，要在节拍上接住你的哼唱，这是文字聊天做不到的。

## 4. 研究问题（建议，需要组内讨论）

- **RQ1（核心）：** 对非专业用户，"回应式" AI（接住用户的句子，沉默时主动抛新动机）和"伴奏式" AI（只铺底跟拍）相比，哪个带来更强的共创感、主导感和乐趣？
  - 设计：被试内。基线就是同一个系统关掉回应（指挥只做 HOLD），其他部分完全一样。
- **RQ2：** 引导方式（语音或手势 vs 屏幕按钮，意图集相同）对心流和打断感有什么影响？
- **客观指标**（都来自会话日志）：
  - 回应延迟；
  - 接话率：AI 抛出动机后几个小节内用户是否开口；
  - 调内率：用户的音落在当前调内的比例；
  - 指令次数。
- **主观指标：** Creativity Support Index（CSI）、NASA-TLX、主导感量表，以及半结构访谈。
- **对应课程内容：**
  - L5 音乐生成（practice 3）和语音；
  - L3 手势识别（practice 3）；
  - L7 意图识别；
  - L8 社会互动（轮流）；
  - L11 人机协作中的艺术创作、人在环中；
  - L12 可解释性（每个决定都有 reason 字段）。

## 5. 已实现（MVP，2026-10-15）

```
麦克风/录音 ──► analysis
                 ├─ tempo.py   拍手 → 速度（拍间间隔中位数，不稳就放弃）
                 ├─ pitch.py   pYIN → 帧级音高 → 音符切分（去颤音和毛刺）→ 量化到 16 分音符
                 ├─ key.py     Krumhansl-Kessler 调性估计
                 └─ vad.py     实时流里的乐句起止检测
文字指令 ────► director/commands.py  中英文关键词 → Intent
手势名 ──────► director/gestures.py  MediaPipe 的 7 个预置手势 → Intent（要求持续若干帧）
                      │
                      ▼
            director/policy.py   decide(state, event) → Decision(action, 新 state, lead, reason)
                      │          follow / vary / build / break / initiate / adjust / hold / stop
                      ▼
            generate/   harmony（调内循环和弦） patterns（三档能量的鼓、贝斯、键盘）
                        response（重复 / 模进 / 倒影 / 逆行 / 错位，随机回应不会原样照抄）
                        arranger（逐小节编排）
                      │
                      ▼
            render/     synth.py（纯 numpy 合成器） midi_out.py（多轨 GM MIDI） timeline.py（实时重叠相加缓冲）
                      │
                      ▼
            pipeline.py（离线、可复现）   live.py（实时，sounddevice）   session_log.py（JSONL，用户研究的原始数据）
```

- 测试：160 个，覆盖率 98%。`live.py` 要接真实声卡，靠手动验证（另做过不接声卡的冒烟测试），和 GazeCopilot 的处理方式一样。
- 代码审查：python-reviewer 审查一轮，没有 CRITICAL；1 条 HIGH（`--tempo` 未校验导致哼唱与伴奏错位）和 11 条 MEDIUM/LOW 都已修复并补了回归测试；"否定句（don't stop）识别不了"记为已知局限。
- 离线演示：4 下拍手，接一句 E 大调哼唱（100 BPM）。结果：测得 99.4 BPM，7 个音全部转对，乐队从 C 大调转到 E 大调，用模进回应；沉默两小节后，AI 主动抛出一个新动机。

## 6. 下一步

1. **组内确认有没有 Apple Silicon Mac。** 这决定 MRT2 能不能进主线；没有的话，就用 SoundFont 渲染提升音色。
2. **真人录音测转写准确率**：男声、女声、跑调、气声；再和 Basic Pitch 对比。
3. **接上语音和手势输入。** whisper.cpp（语音 → 文字 → `parse_command`）和 MediaPipe 手势（→ `intents_from_gesture`）都只需要产出 `Intent`，指挥层不用改。
4. **实时模式实测延迟**（戴耳机）。目前决定要从下一小节才生效（96 BPM 时一小节 2.5 s），可以考虑改成按拍调度。
5. **写用户研究方案**，并确认课程对被试实验的伦理要求。
6. **（可选）LLM 指挥：** 只用来解释自由形式的指令，输出仍限定在同一组 `Intent`，规则层兜底。

## 7. 节奏生成补充（2026-10-22）

本节补充第 5 节的原型；前文 2026-10-15 的结果和技术调研保留为历史记录。

- 新增 `RhythmContext`：BPM、输入拍点时间、一小节敲击位置、相对力度、置信度；当前支持 4/4。
- 新增节奏驱动的鼓、贝斯、键盘生成：跟随保留输入位置，变化保留输入并加装饰，简化选取主要重音。
- 新增独立离线路径：节奏 JSON 或“4 下均匀拍手 + 一小节节奏”的 WAV → 伴奏 WAV、三轨 MIDI、提取结果 JSON、日志。
- 实时模式可选 `--input-mode rhythm`，在播放网格上采集完整小节；低置信度/空输入保留旧节奏。实时切分节奏不独立估速，通过文字指令或上游节奏事件变速。
- 调度采用累积采样时钟；变速从未渲染的小节开始，渲染落后时按整小节跳过，丢失输入音频块时放弃不完整小节。增加输入、计划和设备 DAC 时间日志。
- A/B 对照例子均为 100 BPM：跟随模式分别保留 `[0, 1, 1.5, 3]` 和 `[0, 0.5, 2, 3.5]`；4 小节贝斯音符数在跟随/变化/简化模式下分别为 16/24/8。例子会重新读取导出的 MIDI 核对这些结果。
- 模拟拍手录音回读结果：99.384 BPM，节奏位置完整恢复为 `[0, 1, 1.5, 3]`，网格贴合置信度 0.942。此结果不代表真人录音准确率。
- WSL 独立环境完整测试：226 项通过，总覆盖率 97%（仍排除 `live.py` 的覆盖率）；调度和麦克风小节输入路径另有无声卡测试。完整示例和接口约定见 README。
- Windows 现有 `jammate` 环境（Python 3.11.17）完整测试同样为 226 项通过；仅出现已有音频读取依赖的弃用提示和损坏录音用例的预期警告。

剩余验证：Windows 真实声卡上的播放/录音闭环、回录延迟测量、真人拍手和噪声数据评估。MRT2、SoundFont 和更长节奏句不在本轮实现范围。

## 8. 流水线调试说明补充（2026-10-23）

- README 的节奏部分统一说明：默认/指定麦克风录音、定速前四下与正式一小节的区别、CV 0.20 的计算、漏检/误检的影响、JSON 字段、离线与实时的行为边界。
- 新增 JSON → `Note` 编排 → 合成器 → WAV 的过程说明；明确 MIDI 是同一组音符的并行导出，当前和弦默认来自规则，乐器/音量/音色的调整位置各不相同。
- 新增 `examples/make_debug_input.py`：重建 6 秒、100 BPM 的标准拍手输入及独立 `expected.json`。标准正式小节起点 3 秒，节奏位置 `[0, 1, 1.5, 3]`、相对力度 `[1, 0.55, 0.8, 1]`，用于和识别输出逐项核对。
- 生成文件在 `demo_out/` 重建，个人麦克风录音由 `.gitignore` 保留在本地。

## 9. 最新 SPEC、哼唱交接与 Wyman 工作对齐（2026-10-28）

### 9.1 核对依据与版本边界

本次核对了以下实际文件和代码：

- `D:\桌面\产品SPEC文档.pdf`，15 页：以其中三个 Contract、职责和优先级为本轮依据，尤其是第 2–5、9–13 页。
- `D:\桌面\新pdf.zip`：包含 10 页 Group Project、Tasks Tracker CSV 及图片。会议记录中“Wyman：节奏”是先前探索分工；后面的 proposal 和 SPEC 已将 Wyman 调整为 Agent / Orchestration / State Manager。
- 当前工作仓库 `D:\桌面\学\a26-27 autumn\AIAA 3800\project\aiaa3800-jammate`：本地 HEAD 为 `c85b8c4`，此前节奏实现仍在。
- 同目录哼唱交接 ZIP：应用 1.1.0、JSON 协议 1.0.0；交接包元数据记录来源提交 `6ea3457`。本次重新校验包内 97 项文件哈希，独立解压在仓库旁，原 ZIP 保留。
- **GitHub 已有一个本地尚未合入的提交**：`e6affe8992bab073688f132f07c6a7f5950768af`，Sponge，2026-10-24 22:21:23 +08:00，`Add renderer experiment module and examples`。该提交只新增 `renderer_experiments/`，没有升级旧指挥接口。本次执行 fetch 并检查远程快照，没有 merge、改写 main 或 push。

Tasks Tracker 中输入模块仍标为 Not started，但交接包已经提供可运行原型；Renderer 也有新代码和样例。因此任务表的状态不能直接当作实现证据。以下区分“组件已实现”“文件级验证”和“全系统验收”。

### 9.2 Wyman 当前具体负责什么

SPEC 第 9 页定义的核心交付是：

```text
Wenhan：原始录音 / 文字等 → InputEvent
                                  ↓
Wyman：             InputEvent + JamState → BandPlan
                       ↑ State Manager 维护唯一状态
                                                  ↓
Kevin：                                Renderer / Runtime → Audio
PM：         定义交互语义、Golden Scenarios，并验收音乐体验
```

Wyman 负责用户意图理解、LISTEN/FOLLOW/RESPOND/INITIATE 策略选择、Jam Action、Agent Harness、规则兜底、模型 routing、状态版本和决策追踪。原始音频识别归 Wenhan；音色、MIDI/Lyria/MRT2 参数及播放时钟归 Kevin。

已有节奏工作有复用价值：`analysis/` 可交接给输入模块，`generate/` 和 `render/` 可作为执行基线；Wyman 接下来重点维护 `state.py` 和 `director/`。SPEC 第 10–11 页要求渐进迁移、保留旧 demo，不为整理目录重写底层。

**此前“大家用 MIDI 当共同中间态”的讨论，需要以本 SPEC 更新：** 公共 Contract 只有 `InputEvent → JamState → BandPlan`。MIDI 是执行和导出表示；哼唱 Melody JSON、Renderer 的 MusicIntent / RenderPlan 可以保留在各模块内部，由 adapter 对接。共享状态仍是结构化对象，可序列化为 JSON；自然语言属于事件 payload 或解释信息，不能替代状态字段。

### 9.3 当前已经实现，以及还差什么

| 部分 | 已有代码 / 本次证据 | 当前缺口与责任归属 |
|---|---|---|
| 节奏输入与伴奏 | 拍手 WAV/节奏 JSON → RhythmContext → 鼓、贝斯、键盘；follow/vary/simplify；WAV/MIDI 导出；单小节采集与调度测试 | 仍限单小节 4/4；真人容错、口技输入和自动模态切换待输入组验证；公共 InputEvent 包装尚无 |
| 旧哼唱路径 | pYIN、音符切分、UserPhrase、调性估计和规则回应 | 可作为基线保留；并非 Basic Pitch 交接包已接入 |
| 新哼唱模块 | 浏览器录音/导入、质量检查、可选裁剪、Basic Pitch、raw/cleaned、试听与 JSON/MIDI 导出；本次 10 项核心测试通过 | 独立应用，尚无正式 InputEvent；无自动 BPM/调性识别、实时逐音符转写；碎音/八度音仍需评估，归输入组 |
| 旧 JamState | BPM、key、energy、和声 variant、最近 motif、rhythm、playing | 缺 state_version、transport 暴露、form、motif owner/source、active layers、interaction 与统一 State Manager，归 Wyman |
| 旧指挥 | UserPhrase/UserRhythm/UserCommand/Silence → Decision；关键词命令、回应和主动 motif；有规则 fallback | Strategy 与 Action 混用，Decision 还直接带新 state 和 lead notes；无正式 BandPlan、模型 routing 和完整抢话控制，归 Wyman |
| 新 Renderer 实验 | 远程有 MusicIntent、演奏型 MIDI/CC/program、FluidSynth 输出入口、Lyria adapter 和历史 WAV/session；本次 11 项离线测试通过 | 尚未接入 BandPlan 和唯一 transport；实验内部模型不能成为第二套共享状态，归 Kevin，与 Wyman 联调 |
| 旧实时运行与日志 | BarClock、timeline、下一未渲染小节生效、decision/bar 日志 | 仍缺 InputEvent ID → state_version → plan_id → execution 的关联，以及真人录音与声学延迟验收 |
| 三个 Contract 和 8 场景验收 | SPEC 已定义语义；原型有可复用行为 | 仓库尚无正式 InputEvent/BandPlan/StateManager，实现与端到端验收仍是下一阶段工作 |

### 9.4 新哼唱输出应该怎样接进来

组员交接文档明确建议读取顶层 `notes`，同时保留 `selectedResult`、`schemaVersion` 和原始结果。不要把这份旋律 JSON 直接交给现有节奏 JSON loader。

| 哼唱输出 | 当前 Python 兼容映射 | 正式接口注意事项 |
|---|---|---|
| `pitchMidi` | `Note.pitch` | 保留 MIDI 音高数字 |
| `startSec` / `durationSec` | 按会话 BPM 转为拍 | 输入零点是原录音开始；会话位置需另外提供 |
| `velocity01` | `round(value * 127)` | 目前固定为 90/127，不是识别到的歌唱力度 |
| `tempo.bpm = null` | 不能直接作为当前速度 | 从 JamState 或明确的初始化选择获得速度 |
| `midiExport.referenceBpm = 120` | 仅文件编码参考 | **不能用来覆盖 JamState 的 BPM** |
| `modelActivation` / `onsetActivation` | 保留在输入证据中 | 不是校准后的置信度；不能直接填成 InputEvent.confidence，未知值约定需与输入组确认 |
| `status = no_notes` | 不产生虚构的用户旋律 | 保留当前音乐、记录原因；具体事件语义由 Contract 规定 |
| `id` / `createdAt` | 来源关联信息 | id 属于加载的录音，重复转换可复用；不应不加区分地作为唯一 event_id；createdAt 不是 session 时间 |

恒定 BPM 下，原录音第零拍时刻为 `t0`：

```text
startBeat    = (startSec - t0) * sessionBpm / 60
durationBeat = durationSec * sessionBpm / 60
```

这是坐标转换，不是节拍量化。若跟随已在播放的乐队，还需录音开始时的 session beat/时间对应关系；录音跨变速时需 Runtime 的 tempo map，不能整段套用一个 BPM。裁剪偏移已由哼唱模块加回，adapter 不应重复添加。

正式路径应为：

```text
Melody JSON / RhythmContext / 语音文字
             ↓ 各自的输入 adapter
InputEvent(source=humming/rhythm/speech，带时间基准与来源)
             ↓
Wyman Harness + State Manager → BandPlan
             ↓
Kevin Renderer Adapter → MIDI / Lyria 等内部执行格式
```

组员原交接建议中的 `JSON → Note/UserPhrase` 可用于兼容旧 demo，但不能用它代替新的 InputEvent Contract。

### 9.5 本次文件桥接验证，以及不能忽略的行为差异

本次在本地忽略目录 `demo_out/alignment_20261028/` 放置诊断脚本和结果，没有改动正式输入、指挥或渲染实现。该脚本不是生产适配器，也没有定义第四种公共协议。

- 随包合成例子：raw 14 个音符，cleaned 12 个；JSON 与原 MIDI 的音符数和音高一致，最大起止时间差 **0.736 ms**，小于其 **1.042 ms** tick 容差。这仅验证导出一致性。
- 明确使用测试假设 **100 BPM、原录音起点为第零拍、无量化、保留重叠**，12 个音符可转换为 Note 并送入旧 UserPhrase/指挥/编排器，生成 WAV/MIDI。
- 首音在 `0.325079 s`，对应 `0.541798 beat`；旧 policy 调用 `normalize()` 后保存为第 `0` 拍。作为独立回答 motif 的相对化可以合理，但不能因此丢失原 InputEvent 的会话位置或前导休止。
- 示例仍有 **4 对重叠音**，包括同起音的 60/72；组员文档也记录原始合成旋律只有 5 个音符。cleaned 不等于“旋律已完全正确”。此处没有擅自删除泛音或强制单声部；应由输入组给出识别/筛选策略，Wyman 保留 provenance 与不确定性。
- 旧 `answer_motif()` 默认倾向于选择改变音高关系的变换，本次得到 sequence。Seed Motif 的首轮 ECHO 与 VARY 应由 BandPlan 明确选择，不能一收到旋律就默认强变奏。
- 实测 `Nice，就这个。` → KEEP/HOLD；但 `再炸一点。`、`太满了，留点空间。` → 空 Intent/HOLD；`这个不错，鼓再猛一点` 只识别 KEEP，忽略了 drums 范围；`不要停` 被误识别为 STOP。
- 旧 `渐强` 一次把 energy 加 1，尚无持续若干小节的 BUILD 曲线；`简化` 只改 rhythm_mode，没有节奏上下文时不会改变普通伴奏编排。
- 连续两次对 policy 传入 `Silence(2)` 都返回 INITIATE。实时外壳有 lead_pending/quiet_bars 间隔处理，但没有显式 `INITIATE → LISTEN` 状态或用户主导语义；本次未把这个函数探针当作实际播放抢话测量。

本地结果：

- [详细桥接与命令探针结果](../demo_out/alignment_20261028/report.json)
- [验证与版本记录](../demo_out/alignment_20261028/verification.json)
- [试听 WAV](../demo_out/alignment_20261028/bridge_preview.wav)：合成器重放识别音符，加旧伴奏及回答，不含真人原音；AI 回答从第 3 小节开始（代码索引 2）。
- [对应 MIDI](../demo_out/alignment_20261028/bridge_preview.mid)
- [诊断脚本](../demo_out/alignment_20261028/probe.py)：在仓库目录运行 `D:\anaconda3\envs\jammate\python.exe -X utf8 demo_out\alignment_20261028\probe.py`；读取仓库旁原 ZIP，固定样例与假设可重跑。

这些 `demo_out/` 文件不随 Git 提交，本节记录了输入版本、假设与结论。正式交付时应将通用 adapter、可共享 fixture 和相应回归测试另行纳入版本控制。

### 9.6 与新增 Renderer 实验如何对齐

远程 `renderer_experiments/app/models.py` 的 MusicIntent 包含 tempo/harmony/semantic/parts/prompt；`upstream_intent_adapter.py` 接收实验性 Action Intent + baseline。它们能证明渲染能力和受控参数更新已有基础，但接口不是 SPEC 的 BandPlan。

对接应由 Kevin 将 **同一 JamState 快照 + BandPlan** 转成内部 MusicIntent/overrides，Wyman 输出 strategy、action、scope、strength、preserve、effective_at 等语义。不要在 Wyman 层填 GM program、CC 或某后端 prompt，也不要让 Renderer 重新判断用户原始话语的意图。

需要双方核实的具体差异：

- 旧 energy 是 0–3，Renderer 的 energy/activity 是 0–1；需要明确转换，而不是照搬数值。
- 旧默认速度为 96 BPM、和弦循环为 C/G/Am/F；实验默认速度为 100 BPM、和弦为 Cmaj7/Am7/Dm7/G7。禁止由于使用默认构造函数而悄悄重置会话速度和和声。
- 现有实验 MIDI 验证可复现两份渲染之间相同的鼓事件。本次独立比较 baseline/adapted 数据，**384 个鼓事件签名一致**。但该实验不等于已支持保留任意用户 RhythmContext 或任意输入 motif。
- `timing: next_bar` 在实验 adapter 中主要记录到日志；MIDI 实时类的 update 使用 stop/start，示例也以独立段落对比。它尚未接入旧共享 BarClock，不能直接视作跨模块精确小节调度已完成。
- 严格保留 note/groove 的计划需要能力检查；当前实验区分 MIDI 精确执行与 Lyria 语义控制，不能把“发送了风格提示”记成“严格 preserve 已满足”。
- 示例会话包含 Lyria 历史成功记录和 WAV；本次未重连云 API。`demo_upstream_intent.py` 引用的 `examples/upstream-intent/configs/source_intent.json` 未包含在该提交中，FluidSynth/SoundFont 也需外部提供，README 的该示例入口尚不具备直接复跑的全部条件。这些打包/运行问题可交给 Renderer Owner 补齐。

### 9.7 Wyman 推荐实施顺序和验收条件

**第一步：在最新远程基线上落地三个 Contract 和旧接口 adapter。**

- 从已检查的 `origin/main` 建立独立迁移分支，保留旧命令和 demo。
- 为 InputEvent / JamState / BandPlan 实现明确类型、JSON 序列化/反序列化和边界校验。自然语言仅在 speech payload 中存在，策略和动作使用枚举。
- 与 Wenhan 固定 melody/rhythm/text 样例，明确 seconds/beat、session 原点、bar/beat 是否零基、未知 confidence/BPM/key、空结果和来源 ID；未知值不强猜。
- 与 PM/Kevin 对齐 BandPlan 的策略/动作分离、scope、strength、preserve、effective_at、valid_for_bars、based_on_state_version、trigger_event_ids。底层音符和后端 config 留在执行侧。
- 验收：同一组样例能通过 schema 校验、往返 JSON；旧 UserPhrase/UserRhythm/UserCommand 可通过兼容层运行，当前测试保持通过。

**第二步：补 State Manager，集中管理唯一状态。**

- 升级 transport / harmony / form / feel / motifs / arrangement / interaction 与 state_version；State Manager 是正式语义修改入口。
- Runtime 仍产生精确时钟，向 JamState.transport 发布读数；不要重新造一个语义 BPM 或第二套时钟。高频时钟刷新与语义版本如何区分，应在接口中写明。
- 处理重复/乱序事件、过期计划、停止后迟到结果、未知值；区分待执行计划和已经执行的状态，通过执行回执更新，避免“计划加 bass”被误记成“bass 已经开始播放”。
- 验收：状态变更可追溯、事件不会重复生效、过期计划不会覆盖新状态，用户停止不会被迟到任务撤销。

**第三步：把旧 Decision 渐进迁移为规则版 BandPlan。**

- 保留 policy 的规则基础，拆开 Strategy 与 Action，分离 note generation 和 state mutation。
- 先覆盖 PRESERVE、ECHO_MOTIF、BUILD、SIMPLIFY、INTRODUCE_MOTIF；结合 ADD/REMOVE_LAYER 支撑场景。保留其他动作的扩展接口。
- 明确 INITIATE 后转 LISTEN，用户连续表达时留空间；低置信度降低主动性；支持目标范围、否定句和明确 keep 约束。
- 先用固定 InputEvent 样例测规则，不必等待麦克风、Lyria 或 LLM。随后再给自由表达加模型解释、schema 校验、超时 fallback 和过期结果处理。
- 验收：下面 8 场景的策略与动作测试通过；“这个不错，鼓再猛一点”能表示 FOLLOW + PRESERVE + drums 范围 BUILD，保留其他核心内容。

**第四步：与 Kevin 完成 BandPlan → 既有 Renderer 的一个闭环。**

- Wyman 交付事件、状态快照、计划和预期结果；Kevin 接受计划，返回执行结果与实际生效位置。
- 优先用现有可控 MIDI/规则执行端验证保持节奏、回应 motif 和小节入场；再扩展其他后端。
- 日志关联 `event_id → state_version → plan_id → execution`，分开记录输入结束到决策、到调度、到实际声音的延迟。设备 DAC 时间不能替代声学回录测量。
- 验收：既能看见正确计划，也能听见对应执行；计划和真实输出不一致时能定位到具体阶段。

### 9.8 八个场景：现状和 Wyman 应补的断言

| SPEC 场景 | 现状 | 应补的决策/状态验证 |
|---|---|---|
| Seed Rhythm | 旧节奏伴奏路径已具备 | rhythm_seed → RESPOND，建立并保留 groove；缺省/低置信度不乱改速度 |
| Seed Motif | 旧回应函数和新识别输出分别具备 | musical_phrase → RESPOND/ECHO_MOTIF；来源、时间和 motif identity 可追踪 |
| Positive Feedback | nice/不错可 KEEP，部分同义表达缺失 | FOLLOW/PRESERVE，记录被认可对象，tempo/key/motif 不无故变化 |
| Build | 已有 energy 加 1 的旧行为 | 自然表达、scope、强度及持续时间；保持原 groove 和 harmony |
| Simplify | rhythm_simplify / pads-only 有基础 | arrangement 信息量实际减少；无 rhythm 上下文时也生效；不等同单纯变小声 |
| User Leads | 有 VAD 和 phrase 结束输入 | 输入开始/活动状态 → LISTEN；已有 AI lead 如何让出空间 |
| User Stalls | Silence 可 INITIATE | 主动一次后 LISTEN，等待回应，避免不断自我续写 |
| Call & Response | 有回应/主动函数 | 跨轮 motif owner/source、触发关联和轮流策略；可追踪 B → 用户 B′ → 回应 |

这张表是迁移和验收清单，不表示八个场景已经完成端到端产品验收。

### 9.9 本次验证结论

- 搬迁后的原仓库：Windows jammate 环境运行完整 pytest，**226 passed，5 warnings，24.60 s**；警告来自已有依赖弃用和损坏音频输入用例。
- 哼唱包：在临时副本中用 Node 20.20.1 和单独安装的 `@tonejs/midi@2.0.28` 运行原核心测试，**10/10 passed**；97 项 manifest 校验及示例 JSON/MIDI 核对通过。
- 远程 Renderer 快照：**11 项离线测试通过**，另 1 项 backend_input 测试因当前环境未安装 Google GenAI SDK 主动排除；未调用收费/云端 API，也未重新用 FluidSynth 渲染。
- 哼唱桥接与命令探针：结果保存在上方 report.json；生成了约 11.1 秒的本地合成试听。
- 本轮没有重跑完整浏览器推理，没有录制真人声音，没有测量真实声卡/声学延迟，也没有宣称正式三个 Contract 已经完成整合。

## 10. InputEvent v1 下游接收基线（2026-10-31）

用户确认公共包装可以先由下游确定。本轮在基于远程 `e6affe8` 的本地分支 `feat/input-event-v1` 实现接收契约，保留此前第 9 节的对齐记录和旧 demo。尚未提交或推送本轮改动。

### 已实现

- `src/jammate/contracts/input_event.schema.json`：可共享的 JSON Schema，公共字段包括 schema_version/event_id/session_id/source/event_type/started_at/ended_at/confidence/payload，以及可选 provenance。
- `InputEvent` 不可变快照、JSON 读写与校验；未知字段、类型不匹配、NaN/Infinity、重复 JSON 键、错误时间映射和损坏的音符来源会被拒绝。
- 时间采用会话秒；音频事件外层对应完整原始分析片段，内部继续保留录音秒或小节拍。离线文件缺少时间映射时允许两个时间均为 null，不使用处理完成时间猜测。
- 自然语言保留原文、否定和复合指令，不转换为关键词 Intent。source=text/speech 区分键盘与 ASR，统一使用 event_type=speech。
- 节奏适配保留 RhythmContext，注明 tempo_source、audio_duration_s、confidence_kind；session 网格结果通过旧接口时不覆盖已有 BPM。
- 哼唱采用 `payload.melody` 完整保留交接 JSON，包括 raw/cleaned、质量/模型信息和来源。未知 BPM/调性仍为 null，不从 MIDI referenceBpm=120 推断速度；旧接口秒转拍要求显式提供会话 BPM，保留前导休止和重叠。
- `python -m jammate.contracts` 提供 validate、wrap-rhythm、wrap-humming、text；`examples/input_events/` 提供三类可共享事件与未改动的合成哼唱源文件。
- `examples/input_event_demo.py --preview` 验证文件往返，并可通过旧指挥/编排器生成兼容试听。它没有执行文本事件的音乐要求，也没有自动合并多模态轮次。

字段、调用方式、边界和对接命令以 [INPUT_EVENT_V1.md](INPUT_EVENT_V1.md) 为准。这里确定的是下游接收基线，Wenhan 可以用自己的模块实现产生同样的事件；本轮没有修改其原交接包。

### 验证结果

- 新增接口测试：**51 passed**；覆盖原文无损、嵌套不可变、未知时间/评分、session 与录音坐标、速度来源、重叠/休止、空输入、数据损坏和命令行转换。
- 主仓库完整回归：**277 passed，5 warnings，32.34 s**，使用 Windows jammate Python 环境；5 条为既有依赖弃用/损坏音频用例警告。
- 三个事件样例均通过独立 CLI validate；JSON Schema 与接收校验一致接受合法样例。
- 合成哼唱源文件与交接包字节一致，SHA-256 为 `cf10e196e17da72d45a70711983b31e8cbe515926c0b06ca07d0daba164722a0`。
- 兼容预览读取 12 个哼唱音符，首音保留为约 0.541798 拍（显式 100 BPM），生成约 10.95 秒的 WAV/MIDI；输出在本地忽略目录 `demo_out/input_events/`。
- 未录音、未调用 LLM 或云端 Renderer，未声明真人识别质量、实时同步精度或完整 Agent 已完成。

### 下一步

先以固定 InputEvent 样例开发带 state_version 的 State Manager 和 BandPlan/规则 Harness，补事件去重、过期计划及执行回执；再实现多模态事件关联、输入活动状态和 INITIATE → LISTEN。最后与 Wenhan、Kevin 对接真实输入和执行端，按八个 Golden Scenarios 验收。第 9 节中“正式 InputEvent 尚无”的状态由本节更新，正式 State Manager/BandPlan 仍待实现。

## 11. 规则 Agent 与状态闭环基线（2026-10-31）

继续在 `feat/input-event-v1` 实现，无 push。完整接口说明与可直接执行的步骤见 [AGENT_V1.md](AGENT_V1.md)。本节更新第 9–10 节关于“State Manager/BandPlan 尚无”的历史状态。

### 本轮实现

- 升级现有 JamState，加入状态版本、transport 读数、分层设置、motif/节奏来源和互动状态；支持严格 JSON 往返，发布 JamState schema。
- BandPlan schema 和不可变类型分离 strategy/action，明确来源批次、状态版本、生效范围、preserve、执行控制和未解决要求；不携带生成音符或后端参数。
- StateManager 原子接收显式批次，去重/拒绝 ID 冲突、隔离会话、记录迟到事件、取消/过期 pending 计划；成功执行回执才提交编排效果。输入观察与乐队执行分别递增版本。
- 规则 Agent 支持保持、增强、简化、motif 回应/变奏/主动、声部开关和停止/继续；增加 scope/否定和冲突处理。原自然语言事件始终保留，未知表达产生 LISTEN/unresolved。
- InputEvent 1.1 增量加入 system/session_control，支持 user_activity、idle、start、stop；原 1.0 事件继续接受。手工活动信号与主动后等待状态已具备，真实检测器未接入。
- OfflineRuntime 复用旧小节编排器，实际执行鼓力度变化和伴奏密度删减；校验被保留声部/节奏的音符。INTRODUCE 成功登记 ai motif 后进入 LISTEN，连续 idle 不连续生成。
- 八轮 demo 输出逐轮和全段 WAV/MIDI、事件/计划/状态 JSON 及关联 trace；旧 demo/live 仍走原路径，未宣称已经迁移实时入口。

### 实际验证

- Windows conda `jammate`：完整根测试 **324 passed，5 warnings，29.04 s**；其中本轮新增 orchestration 47 项，上轮 InputEvent 51 项。
- 最后调整“继续，同时只增强鼓”的保留层比较后，相关 InputEvent/orchestration **98 项复验通过**；文档中的自定义两轮命令与 system JSON 样例校验也通过。
- `python examples/agent_demo.py --out demo_out/agent_v1/validated_20261031` 成功，8 轮/29 小节，最终 state_version=16。独立读取 WAV/MIDI 确认为 71.1 秒/554 个音符。第三轮简化使伴奏从 96 降至 72 个音符；第四轮主动，第五轮等待；停止轮为 0 个音符，下一轮显式恢复。
- 测试覆盖原文/否定、scope、真实音符保留、前导休止、同轮输入、事件重试、原子拒绝、旧状态与回执、时钟越界、执行失败和日志关联。
- 这些是固定样例和离线合成验证，不是八个 Golden Scenarios 的完整真人产品验收；未调用 LLM、云 Renderer 或真实声卡。

### 后续缺口

先和 Wenhan/Kevin 确认契约与运行时回执，再接真实活动事件、多模态自动关联、唯一播放时钟及新 Renderer adapter。规则之外的自由表达需要 LLM/模型解释与 timeout fallback。当前幂等状态只存在进程内，未实现持久恢复；自由哼唱跨 tempo map、真实输入质量和物理延迟尚未验收。

## 12. LLM 作为 Agent 主决策者（2026-10-31）

用户授权接入 LLM，并提供本机 Word 文档中的 API key。原文档只读，凭据保存在仓库外的用户配置；代码、提示词和日志均不包含实际 key。本轮仍在 `feat/input-event-v1`，未 push。

### 已实现

- 新 Agent 演示默认 LLMAgent；直接读取当前 InputEvent 原文/音乐证据、JamState 和最近六轮带执行状态的历史，不先调用旧关键词解析。
- 官方 OpenAI Responses + strict JSON Schema；每种动作的参数独立，允许引用的 motif/rhythm/preserve 值从当前状态生成。Chat Completions 兼容路径有模拟 HTTP 测试，未实网验证。
- 宿主绑定事件/计划 ID、状态版本和执行小节；StateManager 继续负责唯一状态与回执。LLM 不生成具体 MIDI 音符。
- schema/语义校验和 Renderer 的无音频预检；验证失败最多修正一次。预检一次返回所有受影响的保留层，方便模型完整修正。
- 超时/认证/限流/拒答/连接失败明确降级，默认 hold，不默认回到关键词规则。日志记录来源、模型、token、耗时、验证错误；不打印 provider 错误正文。
- 保留旧 RuleAgent 为 --agent rule 基线，以及可显式选择的 --fallback rule。Runtime/system stop 不等待网络。
- 修复离线 density 取整：稀疏伴奏中小幅 SIMPLIFY 也减少起音组，正密度至少保留一个起音；避免状态字段变了但实际音符不变。

### 验证与证据边界

- Windows `jammate` 环境，OpenAI SDK 2.54.0；完整根测试 **368 passed，5 warnings，24.27 s**。本轮新增 LLM 相关 44 项，普通 pytest 无真实 API 请求。
- 最终使用 `gpt-5.4-mini` + low，八轮真实 API 开发场景 **8/8**；全为 LLM 输出，0 次修正、0 次 fallback。单轮 1.922–4.281 秒，中位数 2.196 秒。
- 产物：`demo_out/agent_llm/gpt54mini_low_20261031/`；独立读取确认 WAV 73.5 秒、MIDI 676 音符。第三轮从 96 降到 88 音符且鼓力度不变，第四轮按历史恢复初始鼓 level，最后一轮正确恢复指代的贝斯。
- 新增 `examples/evaluate_agent_demo.py`，只读取计划/状态/音符统计复核八个开发场景，不调用模型。结果与运行方法见 [LLM_AGENT.md](LLM_AGENT.md)。
- 早期失败、降级和 6/8 记录保留在相邻 demo_out 目录；不是每次调试都通过。prompt、模型设置和执行器均曾调整，因此不构成公平模型比较。最终 8/8 是调试样例的一次结果，不是盲测准确率或任意表达保证。
- 本轮未接真实录音活动检测、新 Renderer、真实声卡或异步音频回调；原 live 命令仍是旧实现。下一步需要上游/执行端联调、留出评测和带 lookahead 的异步调度。

## 14. 新输入批次与 Kevin 执行端（2026-10-31）

本节更新前面各阶段的当前状态；之前的测试记录保留为历史证据。

- 新增 KevinRuntime：直接消费 JamState + BandPlan，复用现有音符编排与 Kevin 的 MIDI/program/FluidSynth 导出。实际回读 MIDI、检查 WAV 后提交回执；不经过另一个 MusicIntent 默认状态。
- Agent 演示增加 `--renderer kevin`、`--instrument-profile`、`--lead-program`，支持实际 SoundFont 音色。仍是离线执行，不代表已接好真实声卡的共享时钟。
- 在推理前固定 16 条自然语言、7 个节奏 WAV、5 个合成哼唱 WAV 和 1 个同时输入用例。实际运行组员 Basic Pitch 页面、OpenAI 与 FluidSynth。
- 节奏 7/7 符合预期；哼唱静音被拒绝，4 个非静音片段均出现长音过度切分，未达到预先定义的音符级标准，保留为上游待解决问题。
- 首轮 26 轮完成导出，25 轮为 LLM 决策，1 轮 API 连接失败后 hold；初步状态/动作检查 25/26，但实际 MIDI 审计另发现 1 轮稀疏和弦密度和 5 轮同 tick 事件顺序问题，不能将 25/26 当端到端通过率。
- 修复单起音组跨小节休止、绝对 tick 取整、同 tick 先 note-off、完整小节尾部休止及 CC123。修复后相关 9 轮保存计划重放通过；连接失败场景另外 4 轮真实 API 复测通过。各轮失败、重放和复测分开保留。
- 最终根测试 + Kevin 原测试：**392 passed，1 deselected，5 warnings**。未选择项依赖 Google SDK；未测试云 Lyria、真人录音或物理音频延迟。

完整协议、结果、可复现命令见 [ACCEPTANCE_20261031.md](ACCEPTANCE_20261031.md)；接口、回执、音色配置和下一步实时工作见 [KEVIN_RENDERER.md](KEVIN_RENDERER.md)。本地试听：`demo_out/acceptance_20261031_v1/band_multimodal_after_fix.wav` 与 `band_text_conversation.wav`。

## 15. 诊断迭代与多轮稳定性（2026-10-31）

- 修复失败后原输入无法重规划、Renderer 忽略终结回执返回值、目录冲突改变共享计划状态、日志错误打断提交、提交后持久化错误被误报为执行失败等路径。
- 完整 JamState 保留，模型读取有限音乐视图；schema 与可见 ID 对齐，完整请求预算包含系统提示和修正消息。历史快照不再重复持有全部音乐库。
- 300 轮受控故障运行通过：42 次执行失败重试、24 次替换、23 次连接故障、16 次非法 JSON 修正、15 次迟到输入；重复事件和回执不重复应用。该测试没有真实 API 或 WAV 合成。
- 紧凑历史前后相同 300 轮的 Python 跟踪保留内存约 50.3 MB → 7.3 MB，仍随证据/目录线性增长，不是无限长会话内存保证。
- 真实 API + Kevin 连续 26 轮：25/26 行为通过；第 7 轮超时 hold，之后恢复。成功 LLM 决策中位数 1.992 秒；仍未接真实声卡。
- 哼唱 2×2 对照：onsetThreshold 0.25 → 0.5 后合成样例从 1/7 → 6/7（含静音）；单独关闭 inferOnsets 没有改善数量。轻声仍失败；保留同音重唱对照，未修改组员 handoff 默认参数。
- 最终测试 **407 passed，1 deselected，5 warnings**；另补齐回执与状态快照在同一锁内获取的并发检查。诊断证据、图与复现入口见 [DIAGNOSTIC_ITERATION_20261031.md](DIAGNOSTIC_ITERATION_20261031.md)。

下一阶段以 [PROJECT_IMPROVEMENT_PLAN.md](PROJECT_IMPROVEMENT_PLAN.md) 为当前计划：输入批次协议 → 异步决策/共享 transport → 真人哼唱验证 → 持久化/归档/恢复 → 真实演奏验收。该顺序明确了 Wyman、输入负责人和 Kevin 的协作边界，不以新增后端替代稳定性工作。

## 来源

- MRT2：<https://github.com/magenta/magenta-realtime>，<https://magenta.tensorflow.org/mrt2>；延迟数字只见于第三方：<https://mer.vin/2026/06/magenta-realtime-2-open-local-live-music-model-with-midi-and-200ms-latency/>
- Basic Pitch：<https://github.com/spotify/basic-pitch>
- whisper.cpp：<https://github.com/ggml-org/whisper.cpp>
- MediaPipe Gesture Recognizer：<https://developers.google.com/edge/mediapipe/solutions/vision/gesture_recognizer>
- ReaLJam：<https://arxiv.org/abs/2502.21267>；Continuator：<https://www.francoispachet.fr/?p=4360>；BachDuet：<https://labsites.rochester.edu/air/publications/benetatos20bachduet_slides.pdf>
