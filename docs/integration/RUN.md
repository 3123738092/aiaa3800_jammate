# 运行 JamMate 创作工作台

在已准备好的 Windows 工作区，双击根目录的 `start-input-lab.cmd`，打开 [创作工作台](http://127.0.0.1:4188)。三张卡片可以各自开始一次创作。使用自己的完整 Git 检出，队友代码从固定的历史提交读取。[新版验收步骤](REFERENCE_WORKBENCH.md)

1. 打开「从旋律出发」。载入示例、导入短录音，或哼一句后点「转成 MIDI」。先试听识别结果，再点「生成音乐」，比较乐队怎样变奏。此入口以 100 BPM 和 C 大调开始编排。
2. 打开「从律动出发」。均匀拍四下定速，再拍完整的一小节，也可以载入示例。点「识别节拍」，选择跟随、加花或简化，再点「生成音乐」。已有速度时可以指定 BPM 和小节起点。
3. 打开「从一句话出发」。先用「来一段，慢一点」生成第一段，再用「去掉贝斯」继续调整。配置模型时可选择队友大模型 Agent，也可显式选择队友本地规则。

每次生成只使用本次输入。在「听创作结果」中播放、下载 WAV 或 MIDI。哼唱页上方的 MIDI 是识别出的单轨旋律，作品 MIDI 在生成结果下方。想保留录音或结果时，在离开页面前下载。

## 新电脑安装

本次在 Windows、Python 3.12、Node 24 和 pnpm 11.25 上验证。克隆仓库后，在仓库根目录打开 PowerShell 并运行以下命令。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" -c requirements-input.lock.txt
pnpm --dir web/baseline install --frozen-lockfile
pnpm --dir web/baseline run build
pnpm --dir web/optimized install --frozen-lockfile
pnpm --dir web/optimized run build
.\start-input-lab.cmd
```

服务只监听 `127.0.0.1`，启动器默认用 4188 端口。需要在终端查看日志时可运行 `.\.venv\Scripts\python.exe -m jammate.input_server --port 4188`。本地规则、原版节拍和哼唱可离线使用。

## 音源与验收

内置 NumPy 合成器可直接生成声音。要使用 Kevin 的 MIDI / FluidSynth 渲染，在启动服务前将 FluidSynth 放入 PATH，或将 `JAMMATE_FLUIDSYNTH` 设置为其可执行文件的完整路径。默认音色库为 pretty_midi 附带的 TimGM6mb，可通过 `JAMMATE_SOUNDFONT` 指定其他 SF2。本机启动器也会使用工作区内已准备好的 FluidSynth 2.6.1。更换配置后需重新启动服务。

[新版示例](../../examples/reference-creation/)与[验收记录](REFERENCE_WORKBENCH.md)说明实际验证过的行为和待验项。[上一版样例](../../examples/creation/)仍可回看。

运行浏览器验收需要 Edge 和 Playwright。如果 Playwright 安装在其他目录，设置 `PLAYWRIGHT_MODULE_PATH` 为其 npm 包路径。运行完整渲染器测试还需要 Python 的 `google-genai` 和 `python-dotenv`。以下命令从仓库根目录执行，先启动工作台：

```powershell
$env:PYTHONPATH = ".;src;renderer_experiments"
$env:INPUT_LAB_URL = "http://127.0.0.1:4188"
.\.venv\Scripts\python.exe -m pytest tests renderer_experiments/tests --basetemp=tmp/pytest-review
node --test web/baseline/tests/*.test.mjs web/optimized/tests/*.test.mjs
node tests/input-browser.mjs
node tests/input-lifecycle.mjs
node tests/input-navigation.mjs
node tests/input-async-browser.mjs
node tests/creation-text-browser.mjs
```

浏览器验收使用合成音频和模拟录音权限。脚本回读 MIDI 和 WAV，检查旋律变化、节拍保留和加花、文字改变速度，以及切换入口和失败重试。另有上传持续音和更快节拍的用例。真人麦克风效果和音乐听感需另行试听。

10 月 28 日的[旧输入格式](INPUT_EVENT.md)、[优化记录](ITERATIONS.md)和[识别对比附录](http://127.0.0.1:4188/appendix.html)可供回溯。当前传给乐队的文档按[新版契约](../INPUT_EVENT_V1.md)校验。
