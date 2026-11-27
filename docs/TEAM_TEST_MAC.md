# Mac 团队测试环境

浏览器通过 HTTPS 打开 Studio，使用测试者自己的麦克风、MIDI 和耳机。Mac 负责转写、Agent、音符编排、MRT2、会话状态和 Inspector。JamMate 监听 Mac 的 `127.0.0.1:4215`，Caddy 在 `127.0.0.1:8420` 验证团队登录，HTTPS 隧道只连接 Caddy。

## 日常联调与发布

- 每个成员在自己的分支修改 `web/`，从仓库根目录运行 `python scripts/team_frontend.py`。在终端粘贴固定团队邀请链接，再打开 [本地 Studio](http://127.0.0.1:4188/jam.html)。修改前端文件后刷新页面。该程序只提供本机页面与转发 API，无需安装音频模型或保存供应商密钥。也可传 `--port 4190` 或 `--invite-file <团队邀请文本>`。
- 本地页面的「设置」展示 Mac 当前后端提交号。团队共享 Mac 上的最新通过检查版本。需要联调尚未合入 `main` 的后端改动时，应在单独的服务环境测试。
- 代码进入 GitHub `main` 后，配有 Git 缓存的 Mac 每 20 秒检查一次，只拉取缺少的 Git 对象。前台网页每 5 秒检查发布状态，切回标签页会立刻检查。发现提交后显示「正在准备更新」，失败时保留旧版本并提示自动重试。候选版本在独立端口检查并合成开场。已有会话可以继续演奏，全部结束后自动切换，启动失败自动恢复上一版本。网页提示刷新时，可保存当前演奏后手动刷新。本地联调的前端文件随自己的 Git 检出更新。

Mac 管理者在已有 `~/jammate-team` 安装上，从仓库运行 `~/jammate-team/python/bin/python3.12 deploy/macos/install-updater.py` 注册发布任务。发布与候选检查日志位于 `~/jammate-team/logs/com.jammate.team.update.{out,err}.log` 及 `candidate.log`。手动回退运行 `~/jammate-team/python/bin/python3.12 ~/jammate-team/bin/update.py --rollback`。回退会暂停自动发布。解决问题后删除 `~/jammate-team/config/deploy-paused` 即可恢复。

安装发布用的 Git 客户端后重新运行注册脚本。`~/jammate-team/bin/uv pip install --target ~/jammate-team/bin/release-deps dulwich==1.2.17`。第一次拉取会创建 `deploy-source.git`，已有的浅克隆缓存也可以直接使用。如果 Mac 默认解析到的 GitHub 节点不可达，可在 `config/github-addresses` 按顺序填写实测可用的 GitHub IPv4，每行一个。连接继续校验 github.com 的 TLS 证书。没有安装 Git 客户端的环境沿用每 90 秒查询及整包下载。

## Mac 运行环境

Apple Silicon 上使用 Python 3.12。从仓库根目录执行以下命令。使用 Homebrew 安装 FluidSynth、Caddy 和 cloudflared，并确认命令在服务的 PATH 中。

```bash
brew install fluidsynth caddy cloudflared
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[llm]' 'magenta-rt[mlx]==2.0.3'
export MAGENTA_HOME="$HOME/Library/Application Support/JamMate/models"
.venv/bin/mrt models init --download-path "$MAGENTA_HOME/magenta-rt-v2"
.venv/bin/mrt models download mrt2_small --download-path "$MAGENTA_HOME/magenta-rt-v2"
mkdir -p tmp
JAMMATE_MRT2_ENGINE=mlx .venv/bin/python -m jammate.render.mrt2 \
  --prompt 'bossa nova, nylon guitar, light percussion' --duration 2.4 --out tmp/mac-preflight.wav
```

国内和海外网络可能需要为资源下载使用不同的官方源。以实际生成的 WAV 和推理回执确认模型可用。设置页里的可选项只说明依赖存在。[MRT2 安装与模型说明](https://github.com/magenta/magenta-realtime/blob/694a545e4ba0b88bf1150137b129582166d3e07f/docs/installation.md)

把讯飞和 LLM 的配置放在仅服务账号可读的目录。用 `JAMMATE_XFYUN_ASR_CONFIG`、`JAMMATE_LLM_CONFIG` 指向这些文件，在同一服务环境中设置 `DASHSCOPE_API_KEY`。有独立总麦克风分类配置时，可以额外指定 `JAMMATE_AUDIO_ROUTER_CONFIG`。配置文件与环境变量均留在 Mac 服务端。

## HTTPS 入口

1. 为团队生成单独的测试口令，用 `caddy hash-password` 取散列，设置 `JAMMATE_TEAM_PASSWORD_HASH`。登录名缺省为 `jamteam`。运行 `caddy run --config deploy/macos/Caddyfile --adapter caddyfile`。
2. 用已启用的 Tailscale Funnel 把 HTTPS 流量转给 `http://127.0.0.1:8420`，例如 `tailscale funnel --bg http://127.0.0.1:8420`。也可使用现有 HTTPS 反向代理或已配置域名的 Cloudflare Tunnel。[Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel) · [Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/get-started/)
   团队没有可管理的域名时，可用 ngrok 账号分配的固定开发地址。把 ngrok Agent 放在 Mac 上，转发同一个 Caddy 端口，启动时加 `--inspect=false`。固定地址填入下一步的 HTTPS 来源。ngrok 的本地请求查看功能和 Studio 自带的 Inspector 分开配置。[ngrok 固定开发地址](https://ngrok.com/docs/gateway/domains)
3. 把实际 HTTPS 起源设置为 `JAMMATE_PUBLIC_ORIGIN`，例如 `https://jam.example.com`。有多个入口时用逗号分隔。代理应保留原始 Host 和 HTTPS 协议信息。在同一服务环境中设置 `JAMMATE_ASR_PRIMARY=qwen`、`JAMMATE_MRT2_ENGINE=mlx`、`JAMMATE_MRT2_MODEL=mrt2_small` 和之前的 `MAGENTA_HOME`，然后从仓库根目录启动服务。

   ```bash
   .venv/bin/python -m jammate.input_server --port 4215 --implementation workspace
   ```

4. 联调期间让 JamMate 和 Caddy 由部署账户的 LaunchAgent 托管，存好各自日志路径。如果走 Tailscale Funnel，用 `tailscale funnel status` 检查转发状态。如果走其他隧道，将隧道进程也交给 LaunchAgent。重启后检查实际 HTTPS 页面、API 和流式音频。

## 用同一测试入口验收

未登录访问网页、API 和 MRT2 流式地址应得到 401。登录后打开 Studio，以实际声音和 Inspector 中对应的回执为准。

| 环节 | 验收 |
| --- | --- |
| 冷启动和语音 | 首次允许浏览器使用麦克风。刷新后无须点 JamMate 的麦克风按钮，看到「在听」后说「Hey JamMate」或「Hey 旋律组」，再说「加一段 Bossa Nova 旋律」。用 `/jam.html?asr=xfyun`、`/jam.html?asr=qwen` 分别测试两家转写。浏览器如限制自动播放，页面仍可识别并显示指令。 |
| Agent 和编排 | 在 Studio 直接发送「先来三拍子的鼓点」。演奏中改为 4/4，核对 InputEvent、JamState、BandPlan 和播放回执。 |
| 拍手、哼唱、MIDI | 分别提交三种输入。在 Inspector 查看识别或导入结果，并确认乐队实际使用了素材。 |
| 内置伴奏与 MRT2 | 在 Studio 听实时伴奏，并等待 MRT2 试听 WAV。需要单独比较 FluidSynth、MRT2 流式和独立生成时，从「设置」打开经典工作台。流式需收到连续 PCM 并验证可播放。 |
| 并行测试 | 两个浏览器分别开场、发指令和导出，核对每个窗口只看到自己的会话。记录两路 MRT2 同时运行时的首帧延迟和断音情况。 |

临时联调用的团队登录仅适用于受控成员。语音片段会经 HTTPS 入口发送到 Mac，由所选的听写服务处理。团队成员可以在自己的会话里看到转写、计划和渲染记录。
