#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
英语单词速记视频生成器 (English Word Memory Video Generator)
=============================================================
把 Coze 工作流「输入英文单词 -> 自动生成英语单词速记视频」转换为可本地运行的技能。

原 Coze 工作流(54 节点)依赖剪映草稿插件 / 火山引擎 TTS / 即梦图像，无法在 WorkBuddy 直接复刻。
本技能在保留「核心智能」(LLM 拆词记忆生成 + 火山式卡通音色 + 矢量插画风格) 的前提下，
改用可本地运行的工具链产出**成品 MP4**：

  1. LLM(OpenAI 兼容, 默认 DashScope/qwen)  -> 生成 word_segments JSON(词 / 词性 / 音义拆块 / 创意笔记)
  2. edge_tts                                -> 单词发音 + 中文释义发音 + 笔记发音(顺序: 词, 释义, 笔记, 词, 释义)
  3. PIL 逐帧渲染(1080x1920 竖屏)           -> 开场拆解段 / 笔记段 / 结尾强化段, 带淡入淡出与错落下场动画
  4. ffmpeg                                  -> 帧编码 + 人声混音 + 可选背景音乐 -> 成品 mp4

用法见 SKILL.md。
"""
import argparse
import asyncio
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap

# ---------- 入口强制 UTF-8(Windows GBK 防护, 见用户级记忆铁律) ----------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ======================================================================
# 1. 配置
# ======================================================================
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))


def default_config():
    return {
        "llm": {
            "provider": "dashscope",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "YOUR_KEY",
            "model": "qwen-plus",
            "temperature": 0.6,
        },
        "tts": {
            "voice": "zh-CN-YunxiNeural",
            "rate": "+0%",
            "volume": "+0%",
        },
        "video": {
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "bgm_volume": 0.22,
        },
        "paths": {
            "ffmpeg": r"C:\Users\lenovo\bin\ffmpeg\ffmpeg-8.1.2-essentials_build\bin\ffmpeg.exe",
            "ffprobe": r"C:\Users\lenovo\bin\ffmpeg\ffmpeg-8.1.2-essentials_build\bin\ffprobe.exe",
            "font_head": r"C:\Windows\Fonts\simhei.ttf",
            "font_body": r"C:\Windows\Fonts\msyh.ttf",
        },
    }


def load_config():
    cfg_path = os.path.join(SKILL_DIR, "config.json")
    cfg = default_config()
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8-sig") as f:
                user = json.load(f)
            _deep_update(cfg, user)
        except Exception as e:
            print(f"[warn] 读取 config.json 失败, 使用默认配置: {e}")
    else:
        # 首次运行自动生成一份可编辑的 config.json
        try:
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            print(f"[info] 已生成默认配置: {cfg_path} (按需修改后重跑)")
        except Exception:
            pass
    # 环境变量覆盖 api_key
    env_key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if env_key:
        cfg["llm"]["api_key"] = env_key
        if os.environ.get("OPENAI_API_KEY") and cfg["llm"]["provider"] == "dashscope":
            # 若仅提供 OPENAI 变量, 切到 openai 兼容
            cfg["llm"]["provider"] = "openai"
            cfg["llm"]["base_url"] = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    return cfg


def norm_path(p):
    """兼容 Git-Bash 风格路径(/c/Users/...) 与 Windows 路径, 统一为绝对路径。"""
    if not p:
        return p
    p = p.strip().strip('"').strip("'")
    m = re.match(r"^/([a-zA-Z])/(.*)$", p)
    if m:
        p = f"{m.group(1)}:/{m.group(2)}"
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.abspath(p)
    return p


def _deep_update(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
    return base


# ======================================================================
# 2. LLM: 拆词记忆生成(对应 Coze 节点 4号)
# ======================================================================
SYSTEM_PROMPT = """你是一个**精通中英文语言结构、深谙中国互联网文化及传播心理学、擅长创意联想与幽默表达的"超级梗词卡"创作大师**。你致力于将枯燥的英文单词转化为能在中国年轻人中引发病毒式传播的、集趣味性与记忆点于一体的"音义混合学习卡片"。

-----

## 核心任务 (Core Task)

根据提供的英文单词，生成一组**适用于短视频图文卡片的 JSON 数据结构**。该结构旨在通过巧妙的"音义拆解"和"情境联想"，帮助中文母语者轻松、高效、且愉悦地记忆英文单词。

-----

## ✅ 输出格式 (Output Format JSON)

请严格按照以下 JSON 结构输出：

```json
{
  "word": "英文原词",
  "pos": "英文词性缩写. 一个核心中文释义",
  "segments": [
    { "en": "英文块1", "ch": "中文对应1" },
    { "en": "英文块2", "ch": "中文对应2" }
  ],
  "note": "融合了segments的、具有中国文化共鸣的创意中文笔记引导文案 (一定用\\n 换行，并且精炼)"
}
```

-----

## ✅ 生成规则说明

### 一、英文单词拆分规则 (segments)
1. 语义优先，兼顾发音：首选按音节/词根词缀拆分；次选按发音谐音拆分。
2. 中文对应(ch)创意本地化：谐音梗 / 直译活用 / 拼音造词 / 文化符号(品牌、人物、影视、游戏)。
3. 数量控制在 2-4 段。
4. 避免生硬拼凑。

### 二、词性与核心释义 (pos)
格式："英文词性缩写. 一个核心中文释义"(如 n. 后果 / v. 恐吓 / adj. 经济的)。只取最核心常用释义。

### 三、笔记引导文案 (note) —— 灵魂所在
- 深度本土化：融入中国文化共鸣、社会热点、网络梗、生活经验，引发情绪(喜悦/吐槽/自嘲/怀旧)。
- 将 en(括号括起) 与 ch 像彩蛋一样散落句中，不拘顺序，重创意关联。
- 用 \\n 换行，1-3 行短句，口语化、有传播性。

## 🚫 限制
- 禁止方言；ch 须为普通话可发音汉字或广为人知的拼音/网络梗。
- 禁止政治敏感、歧视、暴力血腥内容。
- 自然流畅、有"人话感"与创意性，拒绝生硬拼凑。

## ✅ 示例
输入: aftermath
输出: {"word":"aftermath","pos":"n. 创伤","segments":[{"en":"after","ch":"之后"},{"en":"math","ch":"数学"}],"note":"学了数学(math)之后(after)，\\n心灵受到了创伤"}
"""


def parse_word_segments(text):
    """从 LLM 回复里抠出 JSON 对象。容忍 ```json 代码块 / word_segments: 前缀。"""
    # 去掉 ```json ... ``` 围栏
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        block = m.group(1)
    else:
        # 直接找第一个 { 到最后一个 }
        s = text.find("{")
        e = text.rfind("}")
        block = text[s:e + 1] if (s != -1 and e != -1) else text
    # 兼容 "word_segments: {...}" 形式
    m2 = re.search(r"word_segments\s*:\s*(\{.*\})", block, re.S)
    if m2:
        block = m2.group(1)
    data = json.loads(block)
    # 校验必需字段
    for k in ("word", "pos", "segments", "note"):
        if k not in data:
            raise ValueError(f"LLM 返回缺少字段: {k}")
    if not isinstance(data["segments"], list) or not data["segments"]:
        raise ValueError("segments 为空或格式错误")
    return data


def generate_word_segments(word, cfg):
    import requests

    llm = cfg["llm"]
    api_key = llm.get("api_key", "")
    if not api_key or api_key == "YOUR_KEY":
        raise RuntimeError(
            "未配置 LLM API Key。请在 config.json 设置 llm.api_key，或设置环境变量 "
            "DASHSCOPE_API_KEY / OPENAI_API_KEY；也可加 --demo 用内置示例单词体验流程。"
        )
    url = llm["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": llm.get("model", "qwen-plus"),
        "temperature": llm.get("temperature", 0.6),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"单词: {word}"},
        ],
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=120)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}: {r.text[:300]}"
                continue
            j = r.json()
            content = j["choices"][0]["message"]["content"]
            return parse_word_segments(content)
        except Exception as e:
            last_err = str(e)
    raise RuntimeError(f"LLM 调用失败: {last_err}")


DEMO_BANK = {
    "aftermath": {"word": "aftermath", "pos": "n. 创伤",
                  "segments": [{"en": "after", "ch": "之后"}, {"en": "math", "ch": "数学"}],
                  "note": "学了数学(math)之后(after)，\n心灵受到了创伤"},
    "economy": {"word": "economy", "pos": "n. 经济",
                "segments": [{"en": "e", "ch": "依"}, {"en": "co", "ch": "靠"},
                             {"en": "no", "ch": "农"}, {"en": "my", "ch": "民"}],
                "note": "国民经济\n依(e)靠(co)农(no)民(my)"},
    "intimidate": {"word": "intimidate", "pos": "v. 恐吓/威胁",
                   "segments": [{"en": "in", "ch": "在"}, {"en": "timi", "ch": "Timi"},
                                {"en": "date", "ch": "日子"}],
                   "note": "在(in)王者荣耀(Timi)上分的日子(date)里\n队友总是恐吓威胁我"},
}


# ======================================================================
# 3. TTS: 语音合成(对应 Coze 节点 7号/9号, 火山卡通音色 -> edge_tts)
# ======================================================================
async def _tts_one(text, voice, rate, volume, out_path):
    import edge_tts
    comm = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
    await comm.save(out_path)


def tts_list(texts, cfg, out_dir):
    import edge_tts
    tts = cfg["tts"]
    paths = []
    for i, t in enumerate(texts):
        p = os.path.join(out_dir, f"tts_{i}.mp3")
        asyncio.run(_tts_one(t, tts["voice"], tts.get("rate", "+0%"), tts.get("volume", "+0%"), p))
        paths.append(p)
    return paths


def audio_duration(path, cfg):
    ffprobe = cfg["paths"].get("ffprobe") or "ffprobe"
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stderr=subprocess.DEVNULL,
        )
        return float(out.strip())
    except Exception:
        # 兜底估算: 中文约 0.22s/字
        return max(1.0, len(open(path, "rb").read()) / 4000.0)


# ======================================================================
# 4. 渲染: PIL 逐帧(对应 Coze 剪映草稿节点 27-39 的视觉逻辑)
# ======================================================================
from PIL import Image, ImageDraw, ImageFont


def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def make_bg(cfg, bg_image):
    W, H = cfg["video"]["width"], cfg["video"]["height"]
    if bg_image and os.path.exists(bg_image):
        img = Image.open(bg_image).convert("RGB")
        # cover 缩放裁剪到 1080x1920
        iw, ih = img.size
        scale = max(W / iw, H / ih)
        nw, nh = int(iw * scale), int(ih * scale)
        img = img.resize((nw, nh), Image.LANCZOS)
        left = (nw - W) // 2
        top = (nh - H) // 2
        img = img.crop((left, top, left + W, top + H))
        base = img.convert("RGBA")
        # 加半透明暗层, 提升文字可读性
        dark = Image.new("RGBA", (W, H), (0, 0, 0, 70))
        base = Image.alpha_composite(base, dark)
    else:
        # 深色渐变背景(青春竖屏短视频风)
        base = Image.new("RGBA", (W, H), (15, 22, 41, 255))
        grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(grad)
        top_c = (31, 42, 74, 255)
        bot_c = (10, 14, 28, 255)
        for y in range(H):
            a = y / H
            r = int(top_c[0] * (1 - a) + bot_c[0] * a)
            g = int(top_c[1] * (1 - a) + bot_c[1] * a)
            b = int(top_c[2] * (1 - a) + bot_c[2] * a)
            gd.line([(0, y), (W, y)], fill=(r, g, b, 255))
        base = Image.alpha_composite(base, grad)
    return base


def wrap_cjk(s, max_chars):
    lines = []
    cur = ""
    for ch in s:
        if ch == "\n":
            lines.append(cur)
            cur = ""
        else:
            cur += ch
            if len(cur) >= max_chars:
                lines.append(cur)
                cur = ""
    if cur:
        lines.append(cur)
    return lines


def draw_centered_lines(draw, lines, font, cx, start_y, line_h, color, anchor="mm"):
    y = start_y
    for ln in lines:
        draw.text((cx, y), ln, font=font, fill=color, anchor=anchor)
        y += line_h
    return y


def composite_layer(base, layer, fade=1.0):
    if fade >= 1.0:
        return Image.alpha_composite(base, layer)
    r, g, b, a = layer.split()
    a = a.point(lambda x: int(x * fade))
    layer2 = Image.merge("RGBA", (r, g, b, a))
    return Image.alpha_composite(base, layer2)


def render_video(ws, cfg, tts_paths, out_mp4, bg_image=None, bgm=None):
    import subprocess
    W, H = cfg["video"]["width"], cfg["video"]["height"]
    fps = cfg["video"]["fps"]
    font_head = load_font(cfg["paths"]["font_head"], 100)
    font_pos = load_font(cfg["paths"]["font_body"], 46)
    font_seg = load_font(cfg["paths"]["font_body"], 58)
    font_note = load_font(cfg["paths"]["font_body"], 64)
    font_small = load_font(cfg["paths"]["font_body"], 34)

    # 时长
    durs = [audio_duration(p, cfg) for p in tts_paths]
    # 顺序: word, word_ch, note, word, word_ch  (Coze 节点 11 合并结果)
    word_dur, pos_dur, note_dur = durs[0], durs[1], durs[2]
    open_dur = word_dur + pos_dur
    end_dur = word_dur + pos_dur
    total = open_dur + note_dur + end_dur

    colors = {
        "word": (255, 213, 74, 255),
        "pos": (200, 210, 230, 255),
        "en": (255, 255, 255, 255),
        "ch": (255, 209, 102, 255),
        "note": (255, 255, 255, 255),
        "label": (255, 255, 255, 200),
    }

    base_bg = make_bg(cfg, bg_image)
    tmp = tempfile.mkdtemp(prefix="wmv_")
    frame_idx = 0
    n_frames = int(total * fps) + fps

    cx = W // 2
    segments = ws["segments"]

    def render_frame(t):
        nonlocal frame_idx
        img = base_bg.copy()
        d = ImageDraw.Draw(img)
        # 顶部小标签
        d.text((cx, 70), "英语单词速记", font=font_small, fill=colors["label"], anchor="mm")

        if t < open_dur:
            # ---- 场景 A: 开场拆解段 ----
            ta = t
            fade = min(ta / 0.5, 1.0)
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            # 大单词
            ld.text((cx, H * 0.30), ws["word"], font=font_head, fill=colors["word"], anchor="mm")
            # 词性
            ld.text((cx, H * 0.30 + 110), ws["pos"], font=font_pos, fill=colors["pos"], anchor="mm")
            # 拆块逐条错落下场
            seg_start = H * 0.46
            for i, seg in enumerate(segments):
                appear = 0.4 * (i + 1)
                sa = max(0.0, min(1.0, (ta - appear) / 0.4))
                if sa <= 0:
                    continue
                line = f"{seg['en']}  →  {seg['ch']}"
                ld.text((cx, seg_start + i * 90), line, font=font_seg, fill=colors["en"], anchor="mm")
            img = composite_layer(img, layer, fade)
        elif t < open_dur + note_dur:
            # ---- 场景 B: 笔记段 ----
            tb = t - open_dur
            fade = min(tb / 0.5, 1.0)
            drift = max(0.0, 1.0 - tb / 0.6) * 30  # 轻微上飘
            lines = wrap_cjk(ws["note"], 13)
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            draw_centered_lines(ld, lines, font_note, cx, H * 0.46 - drift, 86, colors["note"])
            img = composite_layer(img, layer, fade)
        else:
            # ---- 场景 C: 结尾强化记忆 ----
            tc = t - open_dur - note_dur
            if tc < 0.5:
                fade = tc / 0.5
            elif tc > end_dur - 0.6:
                fade = max(0.0, (end_dur - tc) / 0.6)
            else:
                fade = 1.0
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            ld.text((cx, H * 0.42), ws["word"], font=font_head, fill=colors["word"], anchor="mm")
            ld.text((cx, H * 0.42 + 110), ws["pos"], font=font_pos, fill=colors["pos"], anchor="mm")
            ld.text((cx, H * 0.62), "—— 记住它！", font=font_small, fill=colors["label"], anchor="mm")
            img = composite_layer(img, layer, fade)

        # 底部水印
        d2 = ImageDraw.Draw(img)
        d2.text((cx, H - 60), "音义拆解 · 趣味记忆", font=font_small, fill=colors["label"], anchor="mm")

        fp = os.path.join(tmp, f"frame_{frame_idx:05d}.png")
        img.convert("RGB").save(fp, "PNG")
        frame_idx += 1

    for f in range(n_frames):
        t = f / fps
        if t > total:
            t = total
        render_frame(t)

    # ---- ffmpeg 编码 + 混音 ----
    ffmpeg = cfg["paths"].get("ffmpeg") or "ffmpeg"
    # 先合并人声 5 段
    voice_concat = os.path.join(tmp, "voice.txt")
    with open(voice_concat, "w", encoding="utf-8") as f:
        for p in tts_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    voice_mp3 = os.path.join(tmp, "voice.mp3")
    subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", voice_concat,
                    "-c", "copy", voice_mp3], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    cmd = [ffmpeg, "-y", "-framerate", str(fps), "-i", os.path.join(tmp, "frame_%05d.png"),
           "-i", voice_mp3]
    if bgm and os.path.exists(bgm):
        cmd += ["-i", bgm]
        cmd += ["-filter_complex",
                f"[1:a]volume=1.0[va];[2:a]volume={cfg['video']['bgm_volume']},"
                f"aloop=loop=-1:size=2e9[ba];[va][ba]amix=inputs=2:duration=first:dropout_transition=0[outa]"]
        cmd += ["-map", "0:v", "-map", "[outa]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-shortest", "-movflags", "+faststart", out_mp4]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 清理临时帧
    shutil.rmtree(tmp, ignore_errors=True)
    return out_mp4, total


# ======================================================================
# 主流程
# ======================================================================
def main():
    ap = argparse.ArgumentParser(description="英语单词速记视频生成器")
    ap.add_argument("--word", help="要记忆的英文单词(不填则 --demo 用内置示例)")
    ap.add_argument("--bg-image", help="背景插画图片路径(默认深色渐变背景)")
    ap.add_argument("--bgm", help="背景音乐 mp3 路径(可选)")
    ap.add_argument("--voice", help="覆盖 TTS 音色, 如 zh-CN-YunxiNeural")
    ap.add_argument("--out", help="输出 mp4 路径")
    ap.add_argument("--demo", action="store_true", help="用内置示例单词跑通全流程(无需 LLM Key)")
    ap.add_argument("--random", action="store_true", help="随机选一个内置示例单词")
    args = ap.parse_args()

    cfg = load_config()
    if args.voice:
        cfg["tts"]["voice"] = args.voice

    # 选词
    if args.demo or args.random or not args.word:
        bank = list(DEMO_BANK.keys())
        import random
        word = random.choice(bank) if (args.random and not args.word) else (args.word or "aftermath")
        if word not in DEMO_BANK and not args.word:
            word = "aftermath"
        if args.word and args.word in DEMO_BANK:
            ws = DEMO_BANK[args.word]
        elif word in DEMO_BANK:
            ws = DEMO_BANK[word]
        else:
            ws = None
        if ws is None:
            # 有真实单词但不在示例库 -> 必须走 LLM
            ws = generate_word_segments(args.word, cfg)
        else:
            print(f"[demo] 使用内置示例单词: {ws['word']} (加 --word 真实生成需配置 LLM Key)")
    else:
        ws = generate_word_segments(args.word, cfg)

    print(f"[1/4] 拆词记忆结果: word={ws['word']} pos={ws['pos']} segments={ws['segments']}")
    print(f"      note={ws['note']!r}")

    out_dir = tempfile.mkdtemp(prefix="wmv_tts_")
    # 人声文本顺序(对应 Coze 节点 6/11): word, word_ch, note, word, word_ch
    word_ch = re.sub(r"^[a-z]+\.\s*", "", ws["pos"]).strip()  # 抽取中文释义
    pure_note = re.sub(r"\([^)]*\)", "", ws["note"]).strip()  # 去掉括号内容
    texts = [ws["word"], word_ch, pure_note, ws["word"], word_ch]
    print("[2/4] 语音合成中(edge_tts)...")
    tts_paths = tts_list(texts, cfg, out_dir)
    print(f"      生成 {len(tts_paths)} 段音频")

    out = norm_path(args.out) or os.path.join(os.getcwd(), f"{ws['word']}_速记视频.mp4")
    if not os.path.isabs(out):
        out = os.path.abspath(out)
    bg_image = norm_path(args.bg_image)
    bgm = norm_path(args.bgm)
    print(f"[3/4] 渲染竖屏视频(1080x1920)并混音...")
    out_mp4, total = render_video(ws, cfg, tts_paths, out, bg_image=args.bg_image, bgm=args.bgm)
    print(f"[4/4] 完成! 时长约 {total:.1f}s")
    print(f"      输出: {out_mp4}")


if __name__ == "__main__":
    main()
