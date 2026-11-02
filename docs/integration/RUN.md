# 从输入页面生成伴奏

在这台已准备好的 Windows 电脑上，双击集成工作区根目录的 `start-input-lab.cmd`。它启动本机服务并打开 [工作台](http://127.0.0.1:4185)。基线哼唱页供比较识别效果，另外两张卡片可以生成伴奏。

1. 打开「哼唱与乐队回应」，使用示例、导入短录音或录一段单人哼唱。点击转换，选择「连贯哼唱」，然后点击「生成伴奏」。乐队主旋律会按识别出的音高、起音和时长重现这句话。哼唱保留自由时间，伴奏用 100 BPM 起步。
2. 打开「节拍与乐队回应」。先拍四下均匀的定速拍，再拍完整的一小节，也可以载入示例。点击「识别节拍」和「生成伴奏」。这次独立会话以识别出的 BPM 起步，乐队沿输入拍点编排。已有速度时也可以在设置中指定 BPM 和小节起点。
3. 在「听乐队回应」区域播放或下载整段 WAV、MIDI。这里下载的乐队输入 JSON 可以再次交给命令行 Agent。哼唱页上方的 MIDI 是识别出的单轨旋律。

每次生成使用一个独立会话。本地规则 Agent 选择音乐动作，通过 StateManager 交给渲染器；完成后，浏览器显示使用的音源。网页不需要 API key。队友的[多轮 LLM 命令行入口](../LLM_AGENT.md)也能使用此分支导出的乐队输入。

## 新电脑安装

本次在 Windows、Python 3.12、Node 24 和 pnpm 11.25 上验证。在此集成分支根目录打开 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" -c requirements-input.lock.txt
pnpm --dir web/baseline install --frozen-lockfile
pnpm --dir web/baseline run build
pnpm --dir web/optimized install --frozen-lockfile
pnpm --dir web/optimized run build
.\start-input-lab.cmd
```

服务只监听 `127.0.0.1`。启动器会复用 4185 端口上已启动的 Band Lab。需要在终端查看日志时可运行 `.\.venv\Scripts\python.exe -m jammate.input_server --port 4186`，然后打开相应端口。合成示例、模型和前端依赖在构建完成后可以离线使用。

## 音源与验收

内置 NumPy 合成器可直接生成声音。要使用 Kevin 的 MIDI / FluidSynth 渲染，在启动服务前将 FluidSynth 放入 PATH，或将 `JAMMATE_FLUIDSYNTH` 设置为其可执行文件的完整路径。默认音色库为 pretty_midi 附带的 TimGM6mb，可通过 `JAMMATE_SOUNDFONT` 指定其他 SF2。本机启动器也会使用工作区内已准备好的 FluidSynth 2.6.1。更换配置后需重新启动服务。

[两份合成示例的输入、计划与伴奏](../../examples/web_band/)通过网页生成。要使用命令行重放哼唱输入，可运行以下命令。默认输出到一个新目录。

```powershell
.\.venv\Scripts\python.exe examples/agent_demo.py --agent rule --batch examples/web_band/humming.agent.json
```

运行浏览器验收需要 Edge 和 Playwright。如果 Playwright 安装在其他目录，设置 `PLAYWRIGHT_MODULE_PATH` 为其 npm 包路径。运行完整渲染器测试还需要 Python 的 `google-genai` 和 `python-dotenv`。以下命令从仓库根目录执行，先启动工作台：

```powershell
$env:PYTHONPATH = ".;src;renderer_experiments"
.\.venv\Scripts\python.exe -m pytest tests renderer_experiments/tests --basetemp=tmp/pytest-review
node --test web/baseline/tests/*.test.mjs web/optimized/tests/*.test.mjs
node tests/input-browser.mjs
node tests/input-lifecycle.mjs
```

浏览器验收使用合成音频和模拟录音权限。脚本回读下载的多轨 MIDI、解码试听用 WAV，检查五音旋律的音高、起音与时长，以及贝斯第一小节的拍点。另有上传持续音和更快节拍的用例，产物在 `tmp/browser/`。真人麦克风效果与现场播放延迟需另行实测。

10 月 28 日的[旧输入格式](INPUT_EVENT.md)、[优化记录](ITERATIONS.md)和[识别对比附录](http://127.0.0.1:4185/appendix.html)可供回溯。当前传给乐队的文档按[新版契约](../INPUT_EVENT_V1.md)校验。
