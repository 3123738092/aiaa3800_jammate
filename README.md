# JamMate：会接你话的 AI 乐队（AIAA 3800 课程项目原型）

对着麦克风拍几下定个速度，再哼一句。AI 乐队会听出你的速度和调，用你那句话的变形接回来（模进、倒影、逆行、错位）。你不出声，它就主动抛一个短动机等你接。也可以随时用文字指挥它："faster"、"更有力"、"停一下"、"your turn"。每个决定都附一句理由，告诉你它为什么这么做。

可信度评估、设计取舍、研究问题和下一步见 [docs/PLAN.md](docs/PLAN.md)。

## 环境

- Windows 10/11，Python 3.11（已在 conda 环境 `jammate` 中验证）。
- 项目放在纯英文路径下。

```bat
conda create -n jammate python=3.11 -y
conda activate jammate
pip install -r requirements.txt
pip install -e .
```

## 运行

```bat
python -m jammate demo-input --out demo_out\demo_hum.wav        :: 合成一段 4 下拍手 + E 大调哼唱
python -m jammate offline demo_out\demo_hum.wav --out demo_out\jam.wav --midi demo_out\jam.mid --log demo_out\session.jsonl
python -m jammate offline 你的录音.wav --tempo 90                  :: 没有拍手定速时，用 --tempo 指定
python -m jammate live --tempo 96 --log demo_out\live.jsonl        :: 实时模式，必须戴耳机
```

离线模式的输出：

- `jam.wav`：你的录音和乐队的混音；
- `jam.mid`：乐队的多轨 MIDI，可以导入 DAW 换音色；
- `session.jsonl`：分析结果，以及每个决定和它的理由。

同一个 `--seed` 每次跑出的结果完全一样。

## 测试

```bat
python -m pytest --cov=jammate --cov-report=term-missing
```

`live.py` 依赖真实声卡，不计入覆盖率，改用下面的清单手动验证。

### 实时模式手动验证清单

1. 戴上耳机，运行 `python -m jammate live`，能听到鼓、贝斯、键盘的循环（C 大调，96 BPM）。
2. 哼一句 2–4 秒的旋律，停下来。约一小节后，听到合成主音在用你的句子回应；控制台打出 `[follow] ...`。
3. 输入 `faster`：下一小节起速度变快。输入 `停一下`：只剩铺底。输入 `更有力`：鼓点变密。
4. 什么都不做，等两小节：AI 会主动抛一个动机（控制台打出 `[initiate] ...`）。
5. 输入 `q` 退出。`--log` 指定的 JSONL 里应该有 phrase、command、decision 三类记录。

## 代码结构

| 模块 | 职责 |
|---|---|
| `analysis/` | 拍手 → 速度，哼唱 → 音符，音符 → 调，麦克风流 → 乐句起止 |
| `director/` | 文字和手势 → 意图（Intent）；`policy.decide(state, event)` 返回决定和理由 |
| `generate/` | 和弦循环、三档能量的伴奏型、动机回应与主动抛出、逐小节编排 |
| `render/` | numpy 合成器、MIDI 导出、实时重叠相加缓冲 |
| `pipeline.py` / `live.py` | 离线（可复现）和实时两种运行方式 |
| `session_log.py` | 用户研究用的 JSONL 日志 |
