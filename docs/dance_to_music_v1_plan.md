# dance_to_music V1 实现方案

## 背景

LampGo 运行时现在主要起在 PC 端，因此可以在 macOS 上感知电脑正在播放的音乐，
再把音乐节奏映射成台灯动作。V1 的目标不是做完整编舞系统，而是先做一个可靠、
有产品感、不会伤硬件的出厂技能：

```bash
uv run lampgo invoke dance_to_music duration=60
```

用户启动后，台灯随着当前电脑音乐做基础摇摆、重拍点头和轻微装饰动作，看起来像
在听音乐，而不是像电机被音频波形直接拉扯。

## 产品目标

- 出厂技能名：`dance_to_music`。
- 在 macOS 上优先读取系统播放音频，而不是麦克风环境声。
- 实时识别音乐强弱、低频、鼓点和高频变化。
- 将音乐分成几个稳定的动作 lane：低频驱动身体律动，鼓点驱动 accent，高频驱动小装饰。
- 所有动作仍走 `SkillExecutor -> MotionRuntime -> SafetyKernel -> HAL`。
- 用户发起新技能、急停、录制或手动控制时，`dance_to_music` 必须立刻让位。

## 非目标

- V1 不做复杂情绪识别、乐段识别或 AI 编舞。
- V1 不保证所有播放器/DRM 内容都能被系统音频采集。
- V1 不做跨平台完整实现；先把 macOS 跑通。
- V1 不直接把每个音频 buffer 映射成关节目标，避免抖动、噪音和磨损。

## macOS 音频采集方案

### 首选：ScreenCaptureKit helper

Apple 的 ScreenCaptureKit 支持采集屏幕内容以及对应音频，音频样本通过
`SCStreamOutput` 以 `CMSampleBuffer` 形式输出。官方 WWDC 说明里也明确提到：
可以选择显示器、应用和窗口，可以设置音频采样率/声道数，并且捕获前需要用户授权。

建议新建一个很小的 Swift helper：

```text
lampgo/macos/audio_capture/
  Package.swift
  Sources/LampgoAudioTap/main.swift
```

helper 职责：

- 使用 `ScreenCaptureKit` 选择当前主显示器或全局 display filter。
- 排除 LampGo 自己的 Web/终端进程，避免采集自己的提示音或 TTS 回声。
- 设置 `capturesAudio = true`、`sampleRate = 48000`、`channelCount = 2`。
- 只输出音频，不保存文件。
- 将 PCM frame 通过 stdout、Unix domain socket 或 WebSocket 发给 Python 进程。

Python 侧不直接调 ScreenCaptureKit。这样可以避免 PyObjC 对新 API 支持不稳定，也能让权限、
采集和 sample buffer 解包逻辑集中在 macOS 原生代码里。

### 备用路径

- `source=mic`：直接用 `sounddevice` 采集默认麦克风，适合快速开发和没有授权时兜底。
- `source=blackhole`：如果用户已经装了 BlackHole/Loopback 等虚拟声卡，可以通过现有音频输入路径读取。

V1 默认参数建议：

```text
source = "system"       # system | mic | blackhole
duration = 60           # 秒；0 表示持续到被取消
style = "jazz"          # jazz | electronic | ambient
sensitivity = 1.0
led = true
```

## 技术架构

```text
ScreenCaptureKit helper
  -> PCM frames, 48kHz stereo
  -> Python AudioFeatureExtractor
  -> Beat/Groove Planner
  -> Motion Phrase Renderer
  -> ctx.motion.stream_frames(...)
  -> SafetyKernel
  -> HAL
```

推荐新增模块：

```text
lampgo/perception/music.py
lampgo/skills/builtin/music_skills.py
```

`music.py` 放音频特征提取和节拍状态机，`music_skills.py` 放出厂技能
`DanceToMusicSkill`。这样不会把音乐逻辑塞进现有 `parametric_skills.py`，后续也便于扩展。

## 音频特征 V1

V1 只需要足够稳定的实时特征，不需要完整音乐理解。

每 20ms 接收一帧音频，每 100ms 聚合一次特征：

- `rms`：总能量，控制动作幅度。
- `bass_energy`：约 40-160Hz，控制 base_yaw/body groove。
- `mid_energy`：约 160-2000Hz，控制 base_pitch 慢变化。
- `treble_energy`：约 2000-8000Hz，控制 wrist_roll 或 LED 装饰。
- `onset`：频谱通量突增，用来检测鼓点/重拍。
- `bpm_estimate`：短窗口估计，V1 可先粗略估计 70-160 BPM。

实现上可以先用 `numpy` FFT + 简单 IIR 平滑，不急着引入 `librosa`。`librosa` 更适合离线分析，
实时场景里会偏重。V1 可考虑新增轻量依赖 `numpy`；如果希望更少依赖，也可以先用标准库
加 `array`/`math` 做简化版，但维护成本会更高。

## 动作映射

V1 的关键是动作要像“听懂节奏”，不是“实时抖动”。
快节奏音乐尤其不能追求“每拍都跟”。人的舞蹈也常常是选择重要拍点：
卡重拍、卡段落、卡鼓点，而不是把每一个细碎节拍都转成小动作。V1 应该把
“卡点准确和动作明确”放在“拍子覆盖率”前面。

### Lane 设计

| 音乐 lane | 检测信号 | 动作输出 |
| --- | --- | --- |
| bass lane | 低频能量 | `base_yaw` 左右慢摆，幅度随低频变大 |
| beat lane | onset / beat | 只在被选中的重要拍点做明确下压/回弹 |
| treble lane | 高频能量 | `wrist_roll` 或 LED 做轻微装饰 |
| energy lane | 总 RMS | 控制全局动作幅度和动作密度 |

### 拍点选择：宁可少动，不要小抖

V1 应该引入一个 `BeatGate`，把检测到的 beat/onset 先筛一遍，再交给动作层。

规则建议：

- `beat_stride`：每 N 拍最多响应一次。快歌可以自动升到 2、4、8。
- `min_accent_interval_s`：两次实体 accent 至少间隔一段时间，例如 0.45-0.8 秒。
- `accent_threshold`：只有 onset 足够强，或落在小节重拍附近，才触发动作。
- `min_motion_amplitude_deg`：如果算出来的动作幅度低于可感知阈值，直接不动，不用小幅补偿。
- `phrase_anchor`：优先选择小节开头、低频强拍、段落切换点作为动作锚点。

这样即使音乐是 160 BPM，台灯也可以只跟 80 BPM、40 BPM，甚至只在每小节第一拍
做一次明确动作。用户会感受到“它在卡点”，而不是“它试图追每个拍子但追不上”。

### 动作范围建议

先保守，避免噪音和机械负担：

```text
base_yaw     +/- 8-18 deg
base_pitch   +/- 3-8 deg
wrist_roll   +/- 4-12 deg
wrist_pitch  +/- 2-5 deg
```

V1 不建议动 `elbow_pitch` 太多。它视觉上很明显，但机械负担和空间风险更高，可以留给 V2。
动作幅度低于约 2-3 度时，V1 默认应丢弃而不是播放。小于可见阈值的动作更像抖动，
不如保持静止等待下一个明确拍点。

### Phrase 而不是逐帧直驱

技能内部不要每 20ms 调一次 `move_to`。推荐每 0.8-2.0 秒生成一个小 phrase：

```text
读取最近 1-2 秒音频特征
  -> 估计当前 groove 状态
  -> 生成未来 1 秒动作 frames
  -> stream_frames(frames, fps=50, playback_mode="expressive")
  -> 同时继续分析下一段音频
```

为了降低延迟，V1 可以采用短 phrase：

- 分析窗口：0.5-1.0 秒
- 生成窗口：0.8 秒
- overlap：0.2 秒

这样会比直接等一整段音乐分析完再动自然，也不会像逐帧控制那样抖。

Phrase 内部也要遵守 BeatGate：如果这一段没有值得卡的拍点，就只保持基础 groove
或静止，不生成一串低幅度“补拍”。

## `dance_to_music` 技能接口

建议出厂技能参数：

```python
skill_id = "dance_to_music"
parameters = {
    "duration": float,      # default 60, 0 = until cancelled
    "source": str,          # system | mic | blackhole
    "style": str,           # jazz | electronic | ambient
    "sensitivity": float,   # default 1.0
    "amplitude": float,     # optional global cap, default 1.0
    "beat_stride": int,     # optional, 0 = auto, 1/2/4/8 = every N beats
    "led": bool,            # default true
}
```

执行规则：

- 如果 macOS helper 不可用或无权限，返回可读错误，并提示用户改用 `source=mic` 或完成权限授权。
- 如果当前正在录制动作，拒绝启动。
- 启动后持续运行到 `duration` 到期或被取消。
- `cancel()` 中停止 helper、停止内部任务，并调用 `ctx.motion.stop_smooth()`。
- 每次 phrase 播放前检查 `ctx.motion.is_running` 和 executor cancel 状态。

## 样式预设

V1 至少做三个风格，不需要复杂 UI：

### jazz

- 默认风格。
- base_yaw 慢摆为主。
- beat accent 少而明确，更多是“身体律动”。
- 适合爵士、流行、lofi。

### electronic

- 低频驱动更明显。
- onset 时 base_pitch 下压更干脆。
- 快歌默认每 2 或 4 拍响应一次，避免每拍追随。
- LED 跟随重拍闪烁。

### ambient

- 不强追鼓点。
- 主要跟随总能量和中频缓慢漂移。
- 高频只做很轻的腕部装饰。
- 很多拍点可以直接跳过，以段落变化和能量起伏为主。

## LED 映射

V1 可以把 LED 当作节奏反馈，但不要太抢：

- onset 强时短闪 `music` 或 `star`。
- 高频连续强时使用更亮的动态表情。
- ambient 风格下不频繁闪，避免廉价 disco 感。

如果 ESP32 LED 不在线，技能不应该失败，只跳过 LED。

## 权限与用户体验

macOS 采集系统音频通常会触发屏幕录制/内容捕获相关权限。V1 需要在第一次启动技能时给出清晰提示：

```text
需要允许 LampGo 捕获屏幕/系统音频：
系统设置 -> 隐私与安全性 -> 屏幕录制 -> 允许 LampGoAudioTap
授权后请重启 lampgo。
```

如果 helper 是独立 Swift 可执行文件，授权对象可能是 helper 本身，而不是 Python 进程。
这点要在文档和错误提示里说清楚。

## 配置项建议

后续可以接入 Web 设置页，但 V1 先放技能参数即可。若要持久化，可新增：

```toml
[music]
source = "system"
style = "jazz"
sensitivity = 1.0
max_amplitude = 1.0
led_enabled = true
```

## 验收标准

### 功能验收

- macOS 播放音乐时，执行 `uv run lampgo invoke dance_to_music duration=30` 后台灯开始律动。
- 音乐暂停后，动作幅度在 1-2 秒内明显下降。
- 鼓点明显的音乐中，台灯能在重拍附近做下压/点头 accent。
- 快节奏音乐中，台灯可以间隔 N 拍跟随，但每次动作必须卡在明确拍点上。
- 高频明显时，腕部或 LED 有可见但不刺眼的装饰反馈。
- 用户执行任意新技能时，`dance_to_music` 能被取消并让位。
- 急停始终有效。

### 体验验收

- 看起来像“跟着节奏摇摆”，而不是“系统不稳定抖动”。
- 追不上快节奏时宁可少动，也不能用超小幅度动作填满每个拍子。
- 连续运行 3 分钟内，动作不应越来越偏离初始姿态。
- 默认音量/普通流行音乐下，电机噪音不可明显高于普通 `idle_sway`/`dance`。
- 无系统音频权限时，错误信息能告诉用户下一步做什么。

### 技术验收

- 所有运动帧仍经 `SafetyKernel`。
- 不绕过 `MotionRuntime` 写硬件。
- no-hw 模式可以用模拟音频或麦克风输入跑通状态机。
- 单元测试覆盖 feature extractor、lane quantizer、skill cancel。

## V1 开发步骤

1. 写 macOS Swift helper，只输出 PCM 和简单 level meter 日志。
2. 在 Python 中实现 `AudioFrameSource` 抽象，先接 helper stdout，再接 `sounddevice` 麦克风兜底。
3. 实现 `MusicFeatureExtractor`：RMS、三段频带、onset。
4. 实现 `GroovePlanner`：把特征转成 `bass/mid/treble/beat` lanes。
5. 实现 `DancePhraseRenderer`：每 0.8 秒生成 50Hz frames。
6. 新增出厂技能 `DanceToMusicSkill` 并注册到 `LampgoServer._register_builtin_skills()`。
7. 给 CLI/IPC 调用加 smoke test；给 no-hw 加模拟音频测试。
8. 再考虑 Web UI 里的“跟着音乐跳舞”按钮。

## 风险与取舍

- ScreenCaptureKit 权限会带来第一次使用摩擦，但这是 macOS 上更干净的系统音频路径。
- 实时 beat tracking 很容易过拟合，V1 应以“稳定律动 + 明显重拍”优先。
- 快歌不要追求每拍响应；BeatGate 的分频跟随比超小幅度补拍更重要。
- 太小的动作会再次被误解成抖动；太大的动作会吵和累。默认幅度要偏保守，但比 idle 微动更明确。
- 不建议 V1 做全自动长时间后台舞蹈。它应该是用户主动触发的技能。

## 参考

- Apple Developer: ScreenCaptureKit
  https://developer.apple.com/documentation/screencapturekit
- Apple Developer: Capturing screen content in macOS
  https://developer.apple.com/documentation/screencapturekit/capturing-screen-content-in-macos
- WWDC22: Meet ScreenCaptureKit
  https://developer.apple.com/videos/play/wwdc2022/10156/
