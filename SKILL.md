---
name: english-word-memory-video
display_name: 英语单词速记视频生成器
version: "1.0.0"
description: >
  输入一个英文单词，自动生成「音义拆解 + 趣味笔记 + 发音」的竖屏短视频(mp4)。
  由 Coze 工作流「英语单词，就能自动生成相对应的英语单词速记视频」转换而来：
  保留其 LLM 拆词记忆生成 + 卡通音色 + 矢量插画风格的核心智能，
  改用可本地运行的工具链(OpenAI 兼容 LLM + edge_tts + PIL + ffmpeg)产出成品 MP4，
  不再依赖剪映草稿 / 火山引擎 TTS / 即梦图像等平台独占节点。
type: script
entry: gen_word_video.py
tags: [english, video, education, tts, flashcard, 短视频, 单词速记]
runtime: python
---

# 英语单词速记视频生成器

把任意一个英文单词，变成一支可在抖音 / 视频号 / B站发布的 **1080×1920 竖屏速记短视频**。
视频三段式结构（与原 Coze 工作流一致）：

1. **开场拆解段**：大号单词 + 词性释义 + 逐条错落展示的音义拆块（`en → ch`），带淡入动画。
2. **笔记段**：LLM 生成的「梗式」中文创意笔记（本土化、有共鸣、可传播），淡入上飘。
3. **结尾强化段**：单词再次淡入淡出，配「记住它！」，强化记忆。

人声配音顺序为 `单词 → 中文释义 → 笔记 → 单词 → 中文释义`（对应原工作流节点 11 的合并逻辑），
可选叠加背景音乐。

## 与原 Coze 工作流的对应映射

| Coze 节点 | 作用 | 本技能实现 |
|---|---|---|
| 开始 / 2号随机生成 | 输入单词或随机选词 | `--word` 参数；`--demo/--random` 用内置示例 |
| 4号 拆词记忆生成(LLM) | 生成 `word_segments` JSON | `generate_word_segments()` + `SYSTEM_PROMPT`(完整移植) |
| 5号 抽取释义/笔记 | 清洗 pos / note | `re` 抽取 `word_ch`、去括号 `pure_note` |
| 6号 释义合并/音色 | 组人声文本+选音色 | `texts` 列表 + edge_tts 音色(可配) |
| 7/9号 语音合成 | 火山引擎 TTS | `edge_tts`(免费、本地可用) |
| 11号 合并音频 | 拼接顺序 | `tts_paths` 顺序 word,ch,note,word,ch |
| 18号 图片提示词 / 19号 图像生成 / 20号 抠图 | 矢量插画背景 | 可选 `--bg-image`(自己生成或用任意图)；默认深色渐变背景，离线零依赖 |
| 27-39号 剪映草稿 | 轨道/字幕/关键帧 | `render_video()` 用 PIL 逐帧还原视觉逻辑 |
| 结束 | 导出 | 输出成品 `mp4` |

> 设计取舍：原工作流产出的是「剪映草稿」需手动导出，且强绑定字节系付费节点。
> 本技能直接产出**可发布的 mp4**，且完全离线可跑（仅 LLM 与可选插画需联网）。

## 安装 / 依赖

- Python 3.11+（脚本用托管 Python 3.13 运行）
- 依赖（已包含在 `~/.workbuddy/binaries/python/envs/default`）：`edge_tts`、`pillow`、`numpy`、`requests`
- 系统需有 `ffmpeg` / `ffprobe`（本机位于 `C:\Users\lenovo\bin\ffmpeg\ffmpeg-8.1.2-essentials_build\bin\`）
- 中文字体：`C:\Windows\Fonts\simhei.ttf`(标题) 与 `msyh.ttf`(正文)

首次运行会自动生成 `config.json`，按需修改。

## 配置（config.json 或环境变量）

```json
{
  "llm": {
    "provider": "dashscope",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key": "YOUR_KEY",          // 也可设环境变量 DASHSCOPE_API_KEY / OPENAI_API_KEY
    "model": "qwen-plus",
    "temperature": 0.6
  },
  "tts": { "voice": "zh-CN-YunxiNeural", "rate": "+0%", "volume": "+0%" },
  "video": { "width": 1080, "height": 1920, "fps": 30, "bgm_volume": 0.22 },
  "paths": { "ffmpeg": "...", "ffprobe": "...", "font_head": "...", "font_body": "..." }
}
```

可用 `tts.voice` 换成任意中文音色，例如：
`zh-CN-XiaomengNeural`(甜美)、`zh-CN-YunyangNeural`(新闻男)、`zh-CN-XiaoxiaoNeural`(温柔女)。

## 使用

```bash
# 用内置示例单词跑通全流程(无需任何 Key，验证管线)
python gen_word_video.py --demo

# 真实生成(需配置 LLM Key)
python gen_word_video.py --word aftermath
python gen_word_video.py --word economy --bg-image 我的插画.png --bgm 背景音乐.mp3
python gen_word_video.py --word intimidate --voice zh-CN-XiaomengNeural --out 输出.mp4
```

运行命令示例（本机托管 Python）：
```
C:\Users\lenovo\.workbuddy\binaries\python\envs\default\Scripts\python.exe \
  C:\Users\lenovo\.workbuddy\skills\english-word-memory-video\gen_word_video.py --word aftermath
```

## 输出

- 当前目录下 `<word>_速记视频.mp4`（可用 `--out` 指定）
- 同时打印拆词结果（word / pos / segments / note），便于二次编辑文案

## 局限与可扩展

- 默认背景为深色渐变（离线、零依赖）。若想要原版「矢量实物插画」效果，可用 ImageGen / 即梦
  生成白底扁平插画后传 `--bg-image`。
- LLM 需联网；如希望完全离线，可扩展 `DEMO_BANK` 预置更多单词的 `word_segments`。
- 配音为机器 TTS；如需更接近原版「佩奇猪/猴哥」等卡通音色，可接入火山引擎 TTS 替换 `tts_list()`。
