<aside>
🎛️

**本页是 JamMate 团队开发的主规范。** 后续可以迭代字段、阈值、模型、Renderer 映射和具体实现，但不要轻易改变这里定义的产品目标、模块边界、3 个 Contract 和职责分工。目标是让四个人可以独立探索，同时始终拼成同一个 JamMate。

</aside>

# 1. 产品目标

JamMate 是一个面向普通音乐爱好者的 **AI Jam 搭子**。用户不需要完整乐理或多种乐器能力，可以通过哼唱、节奏、简单乐器 / MIDI、自然语言等方式提出粗略 musical idea；AI 乐队会倾听、跟随、回应，也会在合适的时候主动抛出新的 idea，让音乐在一轮轮互动中逐步发展。

**核心区别：** JamMate 不是 `prompt → 一次生成整首歌`，而是持续的 **Human ↔ AI musical dialogue**。

核心循环：

```
Express → Listen → Respond → Develop → Repeat
```

产品成功的核心不是“生成的音乐单独有多好听”，而是用户是否感觉：

- AI **听到了我刚才做的东西**；
- AI 的回应和我有关系，而不是随机生成；
- 我仍然能主导音乐，不会被 AI 抢走；
- 我没想法时，AI 又能恰当地给我新的刺激；
- 整个 Jam 连贯、自然、有来有回。

# 2. 系统原则

## 2.1 三个公共 Contract

跨模块长期存在、需要团队共同遵守的中间表示只有：

```
InputEvent → JamState → BandPlan
```

它们分别回答：

| Contract | 核心问题 | 主要 Owner |
| --- | --- | --- |
| `InputEvent` | 用户刚刚做了什么？ | 徐文瀚 |
| `JamState` | 当前整个 Jam 是什么状态？ | 语义由 PM 定义；Wyman 实现和维护 |
| `BandPlan` | AI 乐队接下来准备做什么？ | Wyman |

`LyriaConfig`、MIDI event、MRT2 conditioning 等只属于 Renderer 内部执行格式，**不是新的公共 Contract**。

## 2.2 JamState 是唯一 Truth

> “当前 Jam 是什么状态？”答案只能从 `JamState` 读取。
> 

不能出现徐文瀚自己存一套 BPM、Wyman prompt 里再有一套、Runtime 又维护另一套语义 BPM。

其中精确的 sample / beat / bar 时钟由 Audio Runtime 产生，但通过 `JamState.transport` 暴露给其他模块读取。

## 2.3 MIDI 的定位

**MIDI 是很好的执行表示，但不是 Shared Musical State。**

它适合表达 note、velocity、timing，却不能表达：当前是谁主导、用户是否认可当前 motif、AI 正在 follow 还是 initiate、这一段是否正在 build 等交互语义。

因此：

```
JamState + BandPlan
        ↓
Renderer Adapter
   ┌────┴────┐
  Lyria     MIDI / MRT2
```

## 2.4 只做两次核心语义转换

```
PERCEIVE
Raw Input → InputEvent

UNDERSTAND & DECIDE
InputEvent + JamState → BandPlan

PERFORM
BandPlan → Renderer-specific command → Audio
```

不要层层创造新的公共中间态。

# 3. Contract 规范

## 3.1 InputEvent：用户刚刚做了什么？

**徐文瀚负责从 raw input 生成标准化 `InputEvent`。** 这一层负责 perception，不负责最终理解用户意图。

例如用户说“这个不错，鼓再猛一点”：

```json
{
  "event_id": "evt_0189",
  "source": "speech",
  "event_type": "speech",
  "started_at": 32.1,
  "ended_at": 34.0,
  "payload": {
    "text": "这个不错，鼓再猛一点"
  },
  "confidence": 0.96
}
```

不要在这里直接输出 `PRESERVE` / `BUILD`；这些属于 Wyman 的 Harness。

核心字段：

- `event_id` / `session_id`
- `source`：speech / humming / rhythm / instrument / system
- `event_type`：speech / rhythm_seed / musical_phrase / instrument_phrase / session_control
- `started_at` / `ended_at`
- `payload`
- `confidence`

对 rhythm / humming 可以在 `payload` 中加入 onset、BPM range、note / pitch contour、motif candidate 等 perception 结果。

徐文瀚可以自由选择模型和预处理方案，但输出必须满足同一 Contract。

## 3.2 JamState：现在 Jam 是什么状态？

`JamState` 是 session 的唯一共享状态。第一版至少覆盖以下语义：

```
transport     BPM / beat / bar / playing
harmony       key / scale / chord progression
form          当前 section / 已持续多少 bars
feel          energy / density / brightness / tension / tags
motifs        当前 motif、motif owner（user / AI）及来源
arrangement   当前活跃 layer 与角色
interaction   LISTEN/FOLLOW/RESPOND/INITIATE、initiative、current lead、user activity、最近反馈
```

原则：

- 只保存跨模块真正需要的持续状态；临时计算结果不要全部塞进 JamState；
- 允许 `null / unknown`，不要为了“完整”强行猜 key、intent 等；
- JamState 要有 `state_version`，用于判断 BandPlan 是否基于过时状态；
- 正式 mutation 统一由 State Manager 完成。

## 3.3 BandPlan：AI 乐队接下来准备做什么？

BandPlan 必须 **Renderer-agnostic**，描述音乐和交互目标，而不是直接写 Lyria prompt 或 MIDI CC。

示例：

```json
{
  "plan_id": "plan_0104",
  "based_on_state_version": 42,
  "trigger_event_ids": ["evt_0189"],
  "strategy": "FOLLOW",
  "effective_at": {"bar": 21, "beat": 1},
  "valid_for_bars": 4,
  "preserve": ["tempo", "key", "groove", "motif:motif_A"],
  "actions": [
    {
      "action": "BUILD",
      "scope": "drums",
      "strength": 0.45,
      "parameters": {"duration_bars": 4}
    }
  ],
  "plan_confidence": 0.91
}
```

统一原则：

- `strategy` 只使用 Interaction Strategy；
- `actions` 只使用 Jam Action Vocabulary；
- `scope` 表示作用对象，如 band / drums / bass / guitar / keys / motif；
- `strength ∈ [0,1]` 统一描述动作强度；
- `preserve` 明确本轮不能被意外改变的核心内容；
- `effective_at` 说明在哪个 beat / bar 生效；精确执行由 Runtime 负责；
- `LISTEN` 时允许 `actions: []`，**不做新动作也是合法决定**。

# 4. Human ↔ AI Jam Logic

## 4.1 Interaction Strategy

Strategy 回答：**这一轮 AI 相对于用户应该采取什么姿态？**

| Strategy | 中文语义 | 典型触发 | 核心约束 |
| --- | --- | --- | --- |
| `LISTEN` | 倾听 / 留空间 | 用户正在连续表达；AI 刚抛完 idea 等回应 | 不要抢话；必要时主动简化伴奏 |
| `FOLLOW` | 跟随 / 支持 | 用户方向已明确，希望继续维持或轻微发展 | 不主动改变核心 idea |
| `RESPOND` | 回应 / 接招 | 用户刚提出可识别 motif / rhythm / Riff | 回应必须和用户上一轮有可感知联系 |
| `INITIATE` | 主动发起 / 抛 idea | 用户停滞、明确让 AI 来，或当前音乐适合推进 | 主动后必须把空间交还给用户 |

重要 transition：

```
INITIATE → LISTEN
```

AI 抛出一个 idea 后，不应连续自己发展到用户变成观众。

另外：**不确定用户想做什么时，优先降低 AI initiative，而不是更激进地猜。**

## 4.2 Jam Action Vocabulary

Action 回答：**为了实现当前 Strategy，音乐上具体做什么？**

优先支持以下核心 Action：

| Action | 音乐语义 | 典型触发 | 核心约束 |
| --- | --- | --- | --- |
| `PRESERVE` | 保留用户认可的 motif / groove / style / harmony | “就这个”“保持这样” | 不产生明显新方向 |
| `ECHO_MOTIF` | 接住用户 motif，做可辨识回应 | 用户刚哼 / 弹新 motif | 必须听得出来源 |
| `VARY_MOTIF` | 在已有 motif 上继续发展 | 同一 idea 重复较久 | 保留原 motif identity |
| `INTRODUCE_MOTIF` | AI 主动抛一个新的短 idea | 用户停滞 / 请求 AI 给 idea | 短、可回应；抛出后留空间 |
| `BUILD` | 提升能量、密度与期待感 | “再炸一点”“往上推” | 渐进，不应像突然换歌 |
| `SIMPLIFY` | 减少 arrangement 信息量 | “太满了”“留点空间” | 不等于单纯降低音量 |
| `ADJUST_FEEL` | 在保留核心 identity 下调整 feel | “更松”“更 funky”“更暗” | 默认保留 tempo / key / motif |
| `ADD_LAYER` | 加入新的音乐层 | arrangement 需要发展 | 不能盖掉核心内容 |
| `REMOVE_LAYER` | 移除某个音乐层 | 留空间 / 降低密度 | 保留必要 groove / harmony anchor |
| `FILL` | 在 phrase / section 边界做短暂装饰 | 4/8 bars 结束或准备 transition | 短暂、服务结构、不抢主线 |

后续若 Golden Scenarios 出现当前 Vocabulary 无法表达的自然 Jam 行为，再新增 Action；不要为了“完整”提前造很多词。

# 5. Golden Jam Scenarios

这 8 个场景是团队共同的 **产品行为基准和端到端验收集**，不是固定剧本。

| Scenario | 用户行为 | 主要 Strategy / Action | 成功体验 |
| --- | --- | --- | --- |
| Seed Rhythm | “我给你个节奏” + clap / snap / tapping | `RESPOND`；建立 groove，`PRESERVE`  • `ADD_LAYER` | “对，就是我拍的那个 feel。” |
| Seed Motif | humming / Riff A | `RESPOND`；`ECHO_MOTIF`，必要时轻度 `VARY_MOTIF` | 明显听出 AI 接到了自己的那一句 |
| Positive Feedback | “Nice，就这个。” | `FOLLOW`；`PRESERVE` | 系统少改，不因收到反馈反而重新生成 |
| Build | “再炸一点。” | `FOLLOW`；`BUILD`，可配合 `ADD_LAYER / FILL` | 能量明显起来，但仍是同一个 Jam |
| Simplify | “太满了，留点空间。” | `LISTEN/FOLLOW`；`SIMPLIFY / REMOVE_LAYER` | arrangement 真的变空，用户能自然加入 |
| User Leads | 用户连续 humming / solo / 新 Riff | `LISTEN`；必要时 `PRESERVE / SIMPLIFY` | AI 托着用户而不是抢话 |
| User Stalls | 用户连续若干 bars 无新输入 | `INITIATE → LISTEN`；`INTRODUCE_MOTIF` | AI 给刺激，但主动后把空间交回来 |
| Call & Response | AI 抛 motif B，用户回应 B′ | `INITIATE → LISTEN → RESPOND`；`INTRODUCE_MOTIF → ECHO/VARY` | 明显感到“你一句，我一句”的音乐对话 |

所有模块后续至少要能回答：自己的实现如何支撑这些场景、怎么判断成功、当前 failure cases 是什么。

# 6. 团队分工与接口

## PM / Musical Interaction

**Own**

- 产品目标与核心体验；
- Human ↔ AI Jam Logic；
- Interaction Strategy、Jam Action Vocabulary、Golden Scenarios；
- `JamState` / `BandPlan` 的产品语义；
- 产品级验收标准与最终音乐体验 QA；
- 跨模块 Contract 与优先级管理。

**Collaborate**

- Renderer capability 决策；
- Contract 字段是否够用；
- 各模块方案 trade-off。

**Do Not Own**

- 具体 perception model；
- 具体 Agent backend；
- Audio Runtime 实现。

## 徐文瀚 — 多模态输入 / Perception @Wenhan

**Own**

- microphone / audio capture 与多模态输入处理；
- speech、humming、rhythm、instrument phrase 的识别与预处理；
- phrase start / end、pitch / onset / tempo 等 perception；
- raw input → `InputEvent`；
- 自己模块的 benchmark / failure case 分析。

**Collaborate**

- 冷启动和输入模态切换方式；
- `InputEvent` schema；
- confidence / uncertainty 的表达。

**Do Not Own**

- “这个不错”最终意味着 `PRESERVE` 还是别的 Action；
- 乐队下一步该怎么回应；
- Renderer。

## Wyman — Agent / Orchestration / State Manager @Yuhan Chen

**Own**

- `InputEvent + JamState → BandPlan`；
- State Manager：维护唯一 `JamState`；
- 用户意图理解与 Interaction Strategy 选择；
- Jam Action 选择；
- Agent Harness、规则 fallback、模型 routing；
- Agent 自身 benchmark / failure case；
- decision / state tracing。

**Collaborate**

- `JamState` / `BandPlan` 字段；
- Golden Scenarios；
- Renderer 是否能执行某个 Action。

**Do Not Own**

- raw audio perception；
- Lyria / MIDI / MRT2 的具体执行参数；
- Audio Runtime。

### Wyman 现有 repo 的处理方式

现有仓库：

GitHub - 3123738092/aiaa3800_jammate

**原则：不推翻，按照本 SPEC 做拆解、修改、删减和增加。** 现有 prototype 已经跑通不少重要能力，应作为迁移基础，而不是重写。

应保持：

- `analysis/` 中 pitch / rhythm / tempo / VAD 等已有能力；
- 单一 `JamState` 的思想；
- `director/policy.py` 的 rule-based fallback；
- motif response / initiate、MIDI accompaniment 等已有生成能力；
- `render/clock.py`、timeline、next-bar scheduling；
- offline reproducibility、tests、session log。

需要调整：

- 当前 `Decision` 同时混合 Strategy、Action、state mutation、具体 lead notes，需要逐步拆成正式 `BandPlan`；
- `FOLLOW / INITIATE` 等不要继续既当 Strategy 又当 Action；按本 SPEC 分为 Interaction Strategy 和 Jam Action；
- `policy.py` 负责决定“做什么”，不要直接承担最终 MIDI note / Renderer-specific output；
- `state.py` 保留并升级为本 SPEC 的 `JamState`，同时将 Action 定义与 State 分离；
- 现有 `UserPhrase / UserRhythm / UserCommand` 可以先通过 adapter 兼容，再逐步统一到 `InputEvent`；
- `MusicIntent / ActionIntent / RenderPlan` 等 renderer experiment 中的对象不能成为新的全局 Truth：`MusicIntent / RenderPlan` 只能作为 Renderer 内部结构；
- `live.py` 长期定位为 integration / runtime shell，不应同时拥有 perception、director、state 和 renderer 的全部业务逻辑；
- session log 后续统一能 trace `InputEvent → state_version → BandPlan → renderer execution`。

迁移时优先通过 adapter 保持旧 demo 可运行，**不要为了目录整洁先大规模移动代码**。先完成接口迁移，再逐步清理结构。

## 腿子 — Renderer / Runtime / System Integration @Kevin

**Own**

- `BandPlan → Audio`；
- Lyria / MIDI / MRT2 adapter；
- MIDI 的音色、voicing、articulation、layering 与演奏自然度；
- Lyria 的 prompt / config steering 和能力边界；
- MRT2 测试；
- transport clock、buffer、audio playback、latency、next-bar scheduling；
- frontend 与端到端整合。

**Collaborate**

- BandPlan 可执行性；
- Renderer capability matrix；
- Golden Scenario 的端到端体验。

**Do Not Own**

- 重新解释用户原始自然语言；
- 自己决定 Interaction Strategy；
- 维护另一套 Shared State。

### Renderer 当前交接结论（海绵测试后总结的）

- **Lyria**：生成质量和整体 groove / texture 强，适合 style、vibe、density、brightness、BPM 等 soft steering；精确 note / motif 控制弱。
- **MIDI**：节奏、note、layer、entry、fill 等 hard control 强；当前听感容易机械，需要继续优化 instrument、voicing、articulation、dynamics、timing 和 phrase-level development。
- **MRT2**：继续作为 MIDI / text / audio conditioning 的候选生成式 Renderer 测试，但不要让主线依赖单一 backend。

因此系统抽象应保持：

```
BandPlan
  ├─ Generative Renderer Adapter → Lyria / MRT2
  └─ Precise Renderer Adapter    → MIDI / Loop
```

# 7. 产品决策与技术探索方式

遇到尚未明确的问题，不由 PM 预先规定所有实现，也不让 owner 自己默默定成产品规则。统一采用：

```
PM / Team 定产品约束
        ↓
Owner 探索 solution space
        ↓
Owner 用测试、体验和 failure cases 给出 evidence
        ↓
团队讨论 trade-off
        ↓
PM / Team 冻结方案
```

例如：产品要求“用户进入 Jam 后不应该手动切 Speech / Humming / Rhythm Mode”；徐文瀚负责探索 verbal priming、自动分类、hotword、button-assisted 等实现，再带结果回来决定。

**Benchmark 原则：** 每个 owner 自己回答三件事：

1. 我的模块什么算“做好”？
2. 我怎么测？
3. 当前主要 failure cases 是什么？

PM 不替每个人把所有技术指标定死，但保留端到端体验验收标准。

# 8. 当前开发优先级

## 第一阶段：接口对齐

- 代码中正式落地 `InputEvent / JamState / BandPlan`；
- Wyman 将现有 Decision architecture 渐进迁移到 BandPlan；
- 徐文瀚让不同输入统一产出 InputEvent；
- 腿子让 Renderer 接收 BandPlan，不再重新理解用户意图；
- session log 能完整 tracing 一轮 interaction。

## 第二阶段：跑通 Golden Scenarios

优先跑通：

```
Seed Rhythm
Seed Motif
Positive Feedback
Build
Simplify
User Leads
User Stalls
Call & Response
```

每个 Scenario 至少记录：输入、InputEvent、关键 JamState 变化、BandPlan、实际 Renderer 输出、延迟与体验判断。

## 第三阶段：迭代质量

- 多模态输入的容错和自然切换；
- LLM / rule hybrid Harness；
- MIDI 演奏自然度；
- Lyria / MRT2 steering；
- AI initiative 的体验与 HAI study；
- frontend 与正式用户测试。

# 9. 当前不做 / 暂缓

为了避免四个人同时把系统做复杂，当前不优先：

- 完整 DAW；
- 四个独立 LLM 乐手；
- 任意 genre / 任意歌曲结构；
- 完整自动编曲 / 混音 / mastering；
- 复杂长期用户画像；
- 为了“架构漂亮”重写已经工作的底层模块。

# 协作与记录规则

## GitHub：代码与可运行实现的唯一来源

所有会影响系统行为的代码更新统一进入 GitHub：

- 模块实现、接口适配、测试、脚本、配置与可复现 Demo 都提交到 repo；
- 不以本地版本、聊天附件或临时文件作为“最新版”；需要交接时以 GitHub 中可定位的 commit / branch / PR 为准；
- 修改公共 Contract 或跨模块接口时，代码、测试和相关文档应一起更新，避免“代码已经变了，但其他人不知道”；
- 尽量保持主线始终可运行。较大的修改先在独立 branch 中完成并验证，再合回主线。

> **GitHub 回答：现在系统实际上是怎么实现的？**
> 

## Notion：思考、决策与项目知识的共同记录

不是所有重要产出都会体现在代码里。以下内容应及时记录在 Notion 对应任务页、实验页或 SPEC 中：

- 为什么要这样设计，而不是另一种方案；
- 探索过哪些方案，各自优缺点和测试结果；
- benchmark / failure cases / 用户听感与观察；
- 新的 idea、尚未验证的假设和待讨论问题；
- 阶段性结论、被否决的方向以及否决原因；
- 会影响其他模块的产品或技术决策。

不要求把每一次尝试都写成长报告，但重要的**判断依据和结论要留痕**，不要只存在某个人脑子里或聊天记录里。

> **Notion 回答：我们为什么这么做？试过什么？现在得出了什么结论？**
> 

## GitHub × Notion 的联动

一次有价值的开发工作最好能同时回答两个问题：

```
GitHub：我具体做了什么？
Notion：为什么这样做，结果说明了什么？
```

推荐的最小闭环：

```
提出问题 / idea
      ↓
Notion 记录目标、假设或待验证点
      ↓
GitHub 实现并测试
      ↓
Notion 回填结果、failure cases 和结论
      ↓
需要时更新 SPEC / Contract / 后续任务
```

如果一个实验对应具体实现，可以在 Notion 中附上相关 commit / PR / 文件路径；GitHub 中较重要的 PR / commit 也可以引用对应的 Notion 任务或决策背景。

这样做的目的不是增加文档工作量，而是保证后续整合、debug、presentation、用户研究和复盘时，团队能够快速回答：**谁做了什么、为什么这么做、结果怎么样、接下来该怎么改。**

---

<aside>
✅

最终判断标准：**如果更换 perception model、LLM 或 Renderer，核心产品逻辑仍然能够通过同一组 Contract 和 Golden Scenarios 工作，那么我们的系统边界就是健康的。**

</aside>