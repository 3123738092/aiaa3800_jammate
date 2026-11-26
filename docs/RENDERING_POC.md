# JamMate 局部渲染 PoC

## 结论与范围

保留一处可选的麦克风实时 3D，不扩大到整页。默认使用同一 GLB 离线渲染的透明 PNG；没有 WebGL 下载或持续重绘。运行日志的“渲染验证”页可切换静态 / 3D，并运行可复现的对比测试。

本轮实测中的持续 3D 重绘存在约 9.7% 的 rAF 吞吐下降。静态基线本身只有约 30 rAF/s，这组内置浏览器数据不代表显示器刷新率或独立 GPU 性能，不足以支持全场景 3D 化。默认轻量是本轮工程选择。

## 视觉与交互检查

- 暗色开放舞台；局部渐变、反光与玻璃用于真实控件和临时弹层。
- 鼓、麦克风、旋律首尾循环。实际验证鼠标拖动从旋律循环到鼓，以及左右方向键循环一周；页面不因聚焦输入框跳动。
- 作用范围“全乐队”独立于物件，唱片机已经移除。麦克风只负责输入。
- “键盘 / MIDI”在桌面和窄屏始终可见。原 25 键与鼓垫试听保留；MIDI 连接由用户点击授权后采集真实 Note-on，用于本地短音试听。尚未向后端新增 MIDI 旋律录入协议，也没有伪装已接入硬件。
- 播放读数统一行高、数字宽度和对齐轴。无数值时不摆放大破折号。
- 设置与日志的视觉选择器转发到原 select；实际枚举、禁用状态和 change 处理器不变。支持键盘选项导航与 Esc 关闭列表。
- 日志空状态不填充假速度、调性或和弦；真正开始会话后显示后台数据。
- DM Sans / Noto Sans SC / Geist Mono 已在页面中验证加载；Geist Mono 只用于代码与 JSON。中文字体子集覆盖 GB2312 常用字及当前 UI 文案，罕见字继续由系统字体 fallback。

## 帧率与音频检查

实测时间：2026-11-26 01:50（Asia/Shanghai）。环境：Codex 内置浏览器，1280 × 720，DPR 1.5；浏览器报告 20 个逻辑核心、16 GB 设备内存。页面已预热，同一段 92 BPM 内置合成器伴奏持续播放。静态图进行 CSS 变换，3D 进行相同频率的受控连续重绘，各 8 秒；普通使用时不会执行该循环。

| 测量项 | 静态图 | 局部 3D |
|---|---:|---:|
| 测量时长 | 8024 ms | 8014 ms |
| rAF 吞吐 | 29.9 /s | 27.0 /s |
| P95 帧间隔 | 104.2 ms | 100.0 ms |
| 超过 50 ms 的帧间隔 | 28 | 29 |
| PerformanceObserver 长任务 | 0 | 0 |
| 实际音频时钟推进 | 8.03 s | 7.99 s |
| 调度重接增量 | 0 | 0 |
| 非 running 音频状态采样 | 0 | 0 |

3D 测试实际提交 216 帧；每帧报告 12 次绘制、78132 个三角形（包括阴影 pass），5 个纹理、7 个几何体。上述结果来自页面实际测量，没有用理想帧率补值。

音频检查覆盖浏览器时钟与项目已有的 gaps / failures 计数，不是听感、系统音频设备、声卡 underrun 或物理麦克风质量检测。本轮没有实体 MIDI 键盘，也没有执行物理麦克风录制。

## 加载检查

该次本机有缓存测量：3D 模块载入与执行约 93.5 ms，GLB 完成 192.4 ms，HDRI 完成 328.6 ms，渲染器就绪 395.8 ms；从动态导入开始到可用共 490 ms。这些阶段部分并行，不能相加。

最终资源：GLB 1,029,672 字节，Three.js + loaders bundle 约 631 KB，HDRI 1,615,248 字节，透明 PNG 约 121 KB。默认静态方案不请求前三个资源；资源都由 localhost / 同源服务器提供，运行时不请求第三方 CDN。

上述加载数字是 localhost、有缓存、当前浏览器下的组件测量，不是冷缓存移动网络的整页首屏承诺。后续扩大前应补充独立浏览器与真实移动设备的数据。

## 渲染层与业务解耦

- `web/rendering/microphone-scene.js`：仅管理场景、GLB、PBR 材质、HDRI、灯光、相机与 GPU 生命周期，不访问录音、MIDI、后端 API，也不创建 AudioContext。
- `web/jam-spatial.js`：空间焦点、视觉选择器和性能面板，继续驱动原 DOM 控件。
- `jam.js` 新增只读 `jammate:playback-metrics` 事件，把原有 gaps、failures、会话与音频状态提供给测量器，不改变调度。
- 渲染按需执行；离屏、隐藏页面时停止，录音 LED 只响应真实录音状态变化。限制 DPR，处理 WebGL context loss，异常时显示静态图。
- `?render=light` 强制轻量；`?render=3d` 在非受限设备上预览实时 3D。节流、节省流量或低性能设备默认静态；Inspector 手动切换用于实验。

InputEvent、JamState、BandPlan、Renderer、原麦克风采集、字幕与真实日志仍使用已有实现。实体 MIDI 新入口只是本地试听，不把浏览器音符伪装成后端输入。

## 资源与可复现构建

原创建模源码在 `scripts/render-poc/model.mjs`；`npm ci --prefix scripts/render-poc` 后运行 `npm run build --prefix scripts/render-poc`，会生成 GLB 和局部渲染 bundle。Three.js 版本由 lockfile 固定为 0.187.0，许可证在 `web/vendor/THREE-LICENSE.txt`。

透明预渲染由 `scripts/render-poc/prerender.py` 使用 Mitsuba 3.9.1 离线完成：同一 GLB、同一 HDRI、同一相机，128 spp beauty pass 加独立的几何 coverage pass。只在开发时需要 Python 渲染依赖，应用运行不依赖它们。

HDRI：[Poly Haven studio_small_09](https://polyhaven.com/a/studio_small_09)，采用 [CC0 资源许可](https://polyhaven.com/license)。字体来自 Google Fonts 的 DM Sans、Noto Sans SC 和 Geist Mono，OFL 许可证随字体存放。Three.js 实现参考其 [官方文档](https://threejs.org/docs/)。


## 最后一次界面审查

最新批注已落实为紧凑悬浮播放控件、独立录音圆钮与轻量输入字段；页头保持原样。录音采用标准线性 SVG，真实录制状态才点亮麦克风模型。

选择列表按文字与触发器宽度计算尺寸，并限制在设置 dialog 的可见边界内。实测 640px 设置面板中的语音模式列表为 180 × 136px，左右及上下均位于面板内；Esc 关闭列表后，设置面板仍保持打开。

Inspector 的处理过程改为左侧六阶段导航、右侧单阶段摘要 / JSON / 状态比较。阶段旁的 N 项数据只表示实际可见字段数量，不表示阶段已经成功执行。原始数据改为来源导航加单个内容视口，保留原 pre 的 ID、内容及后端更新方式。

已用真实“鼓少一点”指令验证：InputEvent 原文为该指令，起拍后的状态快照中 arrangement.layers[0].density 从 1 变为 0.75。比较的是所选输入的 at_decision 与 after_playback；界面也保留正常 transport 推进产生的差异，不把全部差异都归因于该指令。

后续一次复测出现静态 / 3D 均约 1 rAF/s 的低基线。该组观测不用于渲染成本判断，虽然其音频重接增量仍为 0。验证器现会在页面隐藏时中止；基线低于 10 rAF/s 或没有实际 GPU 绘制时标记 comparisonValid=false，保留原始观测而不输出理想数值。
