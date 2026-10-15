# JamMate：可信度评估、设计与计划

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

## 来源

- MRT2：<https://github.com/magenta/magenta-realtime>，<https://magenta.tensorflow.org/mrt2>；延迟数字只见于第三方：<https://mer.vin/2026/06/magenta-realtime-2-open-local-live-music-model-with-midi-and-200ms-latency/>
- Basic Pitch：<https://github.com/spotify/basic-pitch>
- whisper.cpp：<https://github.com/ggml-org/whisper.cpp>
- MediaPipe Gesture Recognizer：<https://developers.google.com/edge/mediapipe/solutions/vision/gesture_recognizer>
- ReaLJam：<https://arxiv.org/abs/2502.21267>；Continuator：<https://www.francoispachet.fr/?p=4360>；BachDuet：<https://labsites.rochester.edu/air/publications/benetatos20bachduet_slides.pdf>
