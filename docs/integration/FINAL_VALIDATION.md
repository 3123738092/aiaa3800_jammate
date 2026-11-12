# 最终交付与验收

完成范围：同一项目中的哼唱与节拍输入，统一 InputEvent，保留整合基线与最终优化版，完成核心改善后的四轮审查。没有生成合成歌曲，也没有把 MIDI 当作共享状态。

## 验证结果

- Python 完整回归：**241 passed**，包含原有节拍、CLI、MIDI、运行时和新 InputEvent 测试。
- JavaScript：**29 passed**（基线 10、优化版 19）。
- 实际浏览器：基线与优化版均能导出对应的 JSON、InputEvent、MIDI；优化五音示例完整，重复转换音符对象一致。
- 节拍示例：[0, 1, 1.5, 3]，约 100.45 BPM；两段独立录音的 session_id 不同。
- 五组合成音频：持续音、重复音、换音、休止、短音的优化音序符合预期。数据见 humming-benchmark.json；这不是人类录音准确率。
- 录音生命周期：模拟权限等待/拒绝、停止后释放音轨、损坏音频清除、重新加载、参数变动使旧结果失效，均通过。
- 附录切换、五行对比表、390 px 页面宽度检查通过；检查了实际页面截图，无浏览器脚本错误。
- 已在 Windows 上实际测试启动脚本，成功启动本机服务。关闭后的使用入口是项目根目录 start-input-lab.cmd。
- `git diff integration-baseline -- web/baseline` 为空：优化没有改写冻结的哼唱基线。

系统默认 pytest 临时目录因 Windows 权限问题导致测试准备失败，改用项目内新的 `tmp/pytest-final-20261028a` 后完整通过；没有修改系统目录权限。详细浏览器证据：final-browser.json、round-1-browser.json、round-3-browser.json、round-4-browser.json。

## 保留的限制

没有真人哼唱数据，也没有进行物理麦克风端到端测试。连续性策略适用于单声部；大幅滑音、复音、噪声、弱短换音仍可能失败。CPU 参考后端比某些硬件后端慢。基础节拍仍要求 60–180 BPM、完整一小节 4/4；不会自由推断任意节拍。

独立网页录音从各自零点开始。接入现有 JamState transport 时必须显式传入会话及时间偏移。语音识别/NLP、自动模态切换、完整 BandPlan 迁移、线上服务化和真人质量基准留待后续。

## GitHub 交付

调用连接：**Farewellxxxx**（GitHub 用户 ID 181940605，Wenhan XU）。目标：Farewellxxxx/3800-HAI-Project。仅写入该目标，没有修改同学的源仓库。

完整基线保留在 `integration-baseline` 分支；最终版放在 `main`，并保留 `integration/input-events` 分支。通过 GitHub API 分轮创建提交，每一轮都核对完整 tree SHA 与本地对应版本一致；API 提交与本地源提交的 commit SHA 因提交元数据和父历史不同而不同，文件内容一致。阶段映射见 GITHUB_COMMITS.json。

本地保留上游原始 Git 历史与源提交；交付目录另有 Git bundle 及包含已构建网页/模型的备份 ZIP。私人真人录音不自动保存、不提交；本次提交的 WAV 全部为已有示例或新生成的合成测试数据。
