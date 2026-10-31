# 新输入批次与 Kevin 执行端验证（2026-10-31）

本轮已完成新的输入批次、实际 Basic Pitch / OpenAI 调用，以及 Kevin MIDI + FluidSynth 的离线闭环。测试发现并修复了两类实际音符执行错误，也发现了尚未解决的哼唱长音切分问题。**成功导出 WAV 不等于输入识别正确或现场系统验收完成。**

操作入口见 [KEVIN_RENDERER.md](KEVIN_RENDERER.md)；精简机器记录见 [acceptance_20261031_summary.json](acceptance_20261031_summary.json)。完整输入、失败和重放产物保存在本机 `demo_out/acceptance_20261031_v1/`，被 Git 忽略。组员可用下面的脚本重新生成；模型输出不保证逐次一致。

## 1. 输入与固定协议

[acceptance_cases.json](../examples/acceptance_cases.json) 在推理前写好，生成 WAV 后固定 `manifest.json` 和 SHA256。没有把理想音符 JSON 当成识别结果，也没有向 LLM 提供测试预期。

| 输入 | 数量 | 内容 |
|---|---:|---|
| 自行编写的自然语言 | 16 轮，4 个连续会话 | 力度与密度、撤销历史调整、移除后指代恢复、否定、停/继续、指定 BPM、不支持的音色/乐器 |
| 原始节奏 WAV | 7 | 84/101/132 BPM、切分、前导休止、轻微抖动与噪声、不稳定定速、静音 |
| 原始合成哼唱 WAV | 5 | 四音乐句、较长前导休止、单个持续音、较轻音量加噪声、静音 |
| 显式同时输入 | 1 轮 | 已实际识别的节奏 + 哼唱 + “保持 100 BPM”的原文 |

这是 **29 个用例**。两个无效节奏和一个静音哼唱在输入层拒绝，剩下 **26 轮**进入 Agent/Renderer。所有 WAV 均自行合成，不是人的真实录音；文字为自行编写的开发样例，不是留出用户研究。

固定的输入质量阈值：节奏 BPM 相对误差 ≤5%，十六分网格起音完全一致；哼唱按同音高、起音误差 ≤120 ms 做一对一匹配，precision/recall 均 ≥0.8、匹配音的平均时长误差 ≤250 ms。额外报告 10 ms 帧级音高占用 IoU，但不拿它代替音符边界指标。

实际运行环境：节奏分析、LLM 与 Renderer 使用 Windows `jammate` Python；哼唱使用 WSL Chromium，导入 WAV，打开组员 handoff 的原始 `dist`，实际运行本地 Basic Pitch 模型并下载 raw/cleaned JSON 和 MIDI。没有改组员交付目录；推理报告记录模型文件哈希。

## 2. 输入识别结果

节奏 **7/7 符合预期**：5 个有效样例恢复全部网格拍点；BPM 分别为 83.354、101.333、132.512、99.384、109.957；不稳定定速和静音被拒绝。此结果只覆盖这些合成音频，不构成真人拍手准确率。

哼唱静音正确拒绝；4 个非静音样例均返回了音符，但 **0/4 达到预先固定的音符级标准**：

| 样例 | 理想音符数 | 识别音符数 | Precision | Recall | 平均时长误差 | 帧级音高 IoU |
|---|---:|---:|---:|---:|---:|---:|
| h_phrase | 4 | 17 | 0.235 | 1.000 | 0.547 s | 0.889 |
| h_long_rest | 2 | 13 | 0.154 | 1.000 | 0.884 s | 0.930 |
| h_sustain | 1 | 18 | 0.056 | 1.000 | 2.661 s | 0.959 |
| h_quiet | 3 | 16 | 0.188 | 1.000 | 0.572 s | 0.925 |

主要现象是长音被反复切开，不是大面积唱错音高。下游原样响应了识别到的短音，因此也会听到反复触发。未通过修改识别 JSON、自动拼长音或替换理想答案来掩盖问题。

建议上游下一步对比 raw/cleaned 的 onset evidence，检查持续音的重复 onset、合并逻辑与真实重唱的区分；同时采集真人的连音/重复同音/轻声录音，防止为这个合成音色过拟合。当前未修改 Wenhan/哼唱组员的识别模型或清洗规则。

## 3. LLM 与 Renderer 首轮

模型保持 `gpt-5.4-mini`、reasoning effort `low`，没有为新用例改提示词或增加关键词。

- 26/26 轮都生成了 MIDI 和 FluidSynth WAV，包含一次 hold 降级的伴奏导出。
- 25/26 轮决策来自 LLM；`mix/turn_02` API connection 失败，保持当前伴奏，未完成要求的撤销。该失败保留在首轮报告中。
- 初始状态/动作检查为 25/26。恢复播放时有 1 次“同时要求 preserve playing”冲突，被预检发现，模型第二次输出修正。
- 决策耗时 1.578–4.703 秒，中位数 2.969 秒（包括降级），不等于实际声音响应延迟。
- 真实音源为 FluidSynth 2.6.1 + TimGM6mb；没有在这一测试中用 numpy 音色冒充外部渲染。

**25/26 只是初始状态/动作检查，不能写成端到端通过率。** 补充实际 MIDI 审计又发现下面两类问题；原始首轮结果完整保留，没有把修复结果覆盖进去。

## 4. 发现的问题与修复验证

### 稀疏和弦密度

`space/turn_02` 请求键盘少弹。旧实现每小节只剩一个起音组时仍强制保留它，导致 density 降了，实际和弦次数不变。

已修改为：单起音组按绝对小节分配完整休止，和弦音一起保留/删除，其他声部不受影响。该 phase 不随着计划边界重启。新增测试检查实际音符数量和完整和弦；验收脚本也增加音符级检查。

`space_replay_after_fix/report.json` 重放首轮保存的 4 轮计划：**4/4 通过**，不重新请求 LLM。回读旧 MIDI 明确抓到 turn_02 的问题；新 MIDI 已真正减少键盘起音。

### 相邻同音高 MIDI 的 tick 顺序

哼唱 JSON 的六位小数会让相邻音符产生约 1 μs 的重叠。它们导出到同一 tick 时，若新 note-on 在旧 note-off 之前，合成器可能立即关闭刚触发的声音。

已按量化后的绝对 tick 排序，并在同 tick 先 note-off 再 note-on。此前另已修复逐段 delta 取整累积漂移和末尾休止丢失。真正跨 tick 的同音高重叠仍明确拒绝，不伪造声部独立性。

`original_midi_reaudit.json` 用修正后的检查回看，4 个哼唱和 1 个多模态旧 MIDI 均发现此顺序问题。`tick_replay/` 对这 5 个保存计划重新导出：**5/5 通过**；MIDI 回读时间误差不超过半个 tick。原文件保留。

### API 连接失败后的复测

`mix_recovery/` 单独重新执行同一组 4 轮自然语言，真实调用 API 并走 Kevin 导出：**4/4 来自 LLM，4/4 状态语义检查通过**；第二轮恢复初始鼓力度，第四轮恢复刚才移除的贝斯。`evaluation.json` 记录结果。它是复测，不与首轮混成一个成功率。

按补充审计回看首轮 26 轮，有 1 轮连接降级、1 轮密度未兑现和 5 轮 MIDI 顺序问题；它们是 7 个不同轮次。所有本轮定位到的执行器问题已修复，并对相关 9 轮保存计划重放；**哼唱音符切分质量问题仍然存在**。没有把重放称为新的盲测或真人验收。

## 5. 自动化测试

最终在 Windows 环境执行根测试与 Kevin 测试：**392 passed，1 deselected，5 个已有警告，22.54 秒**。其中根测试 381 项，Kevin 原测试 11 项；未选择的 1 项是依赖缺失 Google SDK 的 Lyria backend_input 测试。没有验证云端 Lyria。

本轮新增 13 项：12 个 Kevin/MIDI/执行回执测试和 1 个稀疏和弦测试。普通 pytest 不调用 OpenAI，不使用真人麦克风或声卡；它与上述真实服务/模型批次分开报告。

```bat
python -m pytest -p no:cacheprovider
python -c "import sys; sys.path.insert(0, 'renderer_experiments'); import pytest; sys.exit(pytest.main(['-p', 'no:cacheprovider', 'renderer_experiments/tests', '-k', 'not backend_input_includes_chords_and_energy_prompt']))"
```

## 6. 重新生成并测试

使用一个新的输出目录，脚本会拒绝覆盖旧批次：

```bat
python examples/prepare_acceptance_inputs.py --out demo_out/acceptance_local_01
```

Basic Pitch 浏览器推理需要 Playwright 和 Chromium。Windows 与 WSL 都可运行此脚本，但路径应匹配所用环境：

```bat
python -m pip install playwright
python -m playwright install chromium
python examples/transcribe_acceptance_humming.py ^
  --batch demo_out/acceptance_local_01 ^
  --dist "../jammate-hum-stable-handoff-2026-10-24/project/dist"
```

然后运行实际 Agent 与 Kevin；此步骤会调用已配置的 OpenAI API：

```bat
python examples/run_acceptance_batch.py ^
  --batch demo_out/acceptance_local_01 --out demo_out/acceptance_local_01/run_01 ^
  --fluidsynth "%USERPROFILE%\.local\share\jammate\renderer\fluidsynth\fluidsynth-v2.6.1-win10-x64-cpp11\bin\fluidsynth.exe" ^
  --soundfont "%USERPROFILE%\.local\share\jammate\renderer\soundfont\usr\share\sounds\sf2\TimGM6mb.sf2"
```

只复核执行器、避免再次请求模型：

```bat
python examples/replay_acceptance_renderer.py ^
  --batch demo_out/acceptance_local_01 --source-run demo_out/acceptance_local_01/run_01 ^
  --session space --out demo_out/acceptance_local_01/space_replay ^
  --fluidsynth "<FluidSynth程序路径>" --soundfont "<SoundFont路径>"
```

`--session` 可选本批次的 mix、space、transport、limits、r_sync、h_phrase、multimodal 等。只重放保存计划，不重新调用模型或运行输入识别。

## 7. 现在优先做什么

1. **输入组**：先解决并实测哼唱长音切分。收集连音、重复同音与休止，不要仅用合成输入调整阈值。
2. **Wyman + Kevin**：把目前验证过的计划/回执接到共享 transport 和真实播放队列。模型调用必须异步，计划过期要丢弃，声音停止要释放活跃音符。
3. **联合验证**：真人输入、多输入同时完成、播放中更新/停止、真实声卡延迟与长会话。当前离线导出已经可用，现场连续演奏尚未完成。
