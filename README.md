# JamMate

AIAA 3800 课程原型。用哼唱、拍手或一句话，各自生成一段音乐。结果可试听，也可下载 WAV 和 MIDI。

## 运行

1. 用有仓库权限的 GitHub 账号克隆 `integration/style-llm-creation` 分支。工作台会读取仓库里的历史提交，请使用 Git 克隆。
2. 首次运行按 [Windows 安装说明](docs/integration/RUN.md#新电脑安装)准备环境。
3. 双击 `start-input-lab.cmd`。浏览器会打开本机工作台 `http://127.0.0.1:4188/`。

## 试一下

- **哼唱**　载入示例或录一句，点「转成 MIDI」，再点「生成音乐」。[试听变奏](examples/creation/humming.wav)
- **拍手**　载入示例，点「识别节拍」，再试跟随、加花或简化。[试听加花](examples/reference-creation/rhythm-vary.wav)
- **文字**　先输入「来一段，慢一点」，再输入「去掉贝斯」。[试听调整后](examples/reference-creation/text-adjusted.wav)

## 大模型

「从一句话出发」默认用本地规则，三个入口都可以直接试。文字页也可以选「队友大模型 Agent」。模型把文字和作品状态转成编排指令，由 JamMate 生成音符。

要启用大模型，在本机安装项目的 `llm` 可选依赖，给服务配置自己的 `OPENAI_API_KEY` 或仓库外的私有配置，然后重启服务。仓库提供接入代码，API 凭据由各自的运行环境提供。[配置说明](docs/integration/REFERENCE_WORKBENCH.md#大模型配置)

[旧版命令行和研发记录](https://github.com/Farewellxxxx/3800-HAI-Project/blob/5b1eacad1de81df55c232cf0c17a0374f50a7865/README.md)
