"""
SPCutVideo — 用 ffmpeg 截取视频「入点 → 出点」之间的片段，以视频方式输出

行为：
  - 接收输入视频节点（VIDEO）或文件路径字符串（二选一，**视频节点优先**）
  - 接收浮点秒数 `in_point` / `out_point`（出点必须大于入点）
  - 用 ffmpeg 截取 [in_point, out_point) 这段，落盘成新视频
  - 输出 0 号槽 = 输出路径字符串，1 号槽 = VIDEO（与 VideoMergeLossless / SPVideo60FPS 对齐）

两种截取模式（`accurate`）：
  - True（默认，精准）：重编码（libx264 + crf/preset，音频 aac）。**帧级精准**，
    出入点就是你要的那一帧；代价是耗时（与片段长度、分辨率、preset 相关）。
  - False（快速）：`-c copy` 只复制流、不重编码，几乎瞬时；但只能在**关键帧**上切，
    实际入点会回退到入点之前最近的那个关键帧（GOP 越长偏差越大）。适合"先粗剪看效果"。

设计对齐 SPVideo60FPS / SPCaptureVideoFrame：
  - 输入解析共用同一套写法（支持 VIDEO 对象、路径字符串、内存 BytesIO）
  - 相对路径按 ComfyUI 输出目录兜底（上游 GetVideoFileName 常给 `video/xxx.mp4`）
  - 内存视频先落临时文件（ffmpeg 从 pipe 读不了 moov 在末尾的 mp4），用完即删
  - `OUTPUT_NODE = True` + 返回 `ui.images`，画布才能收到产物预览

时间处理：
  - `-ss` 放在 `-i` 之前（输入级快速 seek，重编码时仍帧级精准）
  - 用 `-t (out - in)` 表示片段长度，而不是 `-to` —— 输入级 seek 后 `-to` 的参照系
    在不同 ffmpeg 版本上解释不一致，`-t` 永远相对片段起点，不会踩坑
  - 先 ffprobe 探总时长做软校验：入点超出时长直接报错；出点超出则夹到末尾并告警

依赖：系统 PATH 里要有 ffmpeg 与 ffprobe。
"""

import os
import shutil
import subprocess
import tempfile
import threading

import folder_paths

from .core import logger

try:
    # 与内置 LoadVideo / SPVideo60FPS 同一写法：把磁盘上的视频包成 `VIDEO` 值
    from comfy_api.latest import InputImpl as _InputImpl
except Exception:  # pragma: no cover - 老版本 ComfyUI 没有 comfy_api
    _InputImpl = None


def _as_video_output(path):
    """把落地好的视频包成 `VIDEO` 值。装不上 `comfy_api.latest` 时退化成返回路径字符串。"""
    if _InputImpl is None:
        return path
    try:
        return _InputImpl.VideoFromFile(path)
    except Exception:
        return path


def _resolve_existing(path):
    """
    这个路径真的存在吗？返回可用的绝对路径，不存在返回 `None`。

    ★ 相对路径再按 ComfyUI 的**输出目录**试一次 —— 上游节点（`GetVideoFileName` 那类）
      常给出 `video/xxx.mp4` 这种相对于输出目录的写法，而 ffmpeg 只认它自己的工作目录。
    """
    if os.path.isfile(path):
        return path
    if not os.path.isabs(path):
        candidate = os.path.join(folder_paths.get_output_directory(), path)
        if os.path.isfile(candidate):
            return candidate
    return None


def _get_stream_source(video):
    """
    安全地从 video 对象里拿到流源。返回：文件路径字符串、BytesIO / file-like 内存对象，或 `None`。

    ★ `get_stream_source()` 可能返回 `io.BytesIO`（内存视频，无磁盘路径），这里**原样返回**、
      不做 `str()` —— 否则会变成 `"<_io.BytesIO object at ...>"` 这种废串。
    """
    if video is None:
        return None
    # 0) 直接给路径字符串
    if isinstance(video, str):
        return video
    # 1) 标准 VIDEO 对象：get_stream_source()（返回 str 或 BytesIO）
    getter = getattr(video, "get_stream_source", None)
    if callable(getter):
        try:
            src = getter()
            if src is not None:
                return src
        except Exception:
            pass
    # 2) 兼容部分节点把路径/流直接挂在属性上的情况
    for attr in ("stream_source", "file_path", "path", "filename", "video_path"):
        val = getattr(video, attr, None)
        if val:
            return val
    # 3) 兼容 dict 形式的对象
    if isinstance(video, dict):
        for key in ("stream_source", "file_path", "path", "filename", "video_path", "video"):
            val = video.get(key)
            if val:
                return val
    # 4) 兜底：裸 file-like（io.BytesIO 等内存流），直接当作内存视频
    if hasattr(video, "read"):
        return video
    return None


def _stream_to_tempfile(stream):
    """把内存中的视频流（BytesIO 等）落盘成一个临时文件，返回该文件路径。"""
    try:
        stream.seek(0)
    except Exception:
        pass
    fd, path = tempfile.mkstemp(suffix=".mp4")
    try:
        with os.fdopen(fd, "wb") as f:
            shutil.copyfileobj(stream, f)
    except Exception:
        try:
            os.unlink(path)
        except Exception:
            pass
        raise
    return path


def _normalize_path(video_path):
    """规范化路径字符串：去引号、去空白。"""
    if not video_path:
        return ""
    video_path = str(video_path).strip()
    if len(video_path) >= 2 and video_path[0] == video_path[-1] and video_path[0] in ("'", '"'):
        video_path = video_path[1:-1].strip()
    return video_path


def _probe_duration(path):
    """用 ffprobe 探测视频总时长（秒）。失败返回 0.0（此时不做时长校验与进度汇报）。"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        path,
    ]
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            return 0.0
        return float((result.stdout or "").strip() or "0")
    except Exception:
        return 0.0


def _run_with_progress(cmd, duration):
    """
    跑 ffmpeg 并把实时进度汇报给 ComfyUI（ProgressBar）。

    - `-progress pipe:1` 让 ffmpeg 把 `out_time_us=<微秒>` 等 key=value 进度块写到 stdout；
    - `-nostats` 关掉 stderr 上的人类可读统计，只留错误信息；
    - stderr 用后台线程读，避免管道缓冲区写满把 ffmpeg 卡死（死锁）；
    - 失败时把 stderr 尾部抛出来，方便定位。
    """
    # 延迟导入：ProgressBar 依赖 ComfyUI 执行环境（非 ComfyUI 环境下 import 会失败）
    from comfy.utils import ProgressBar

    pbar = ProgressBar(100)
    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    stderr_lines = []

    def _drain_stderr():
        try:
            for line in p.stderr:
                stderr_lines.append(line)
        except Exception:
            pass

    t = threading.Thread(target=_drain_stderr, daemon=True)
    t.start()

    last_pct = -1
    try:
        for line in p.stdout:
            line = line.strip()
            # 编码结束标记：把进度顶到 100%（最后一帧时间戳会略小于片段长度，永远到不了 100）
            if line == "progress=end":
                if duration > 0:
                    pbar.update_absolute(100, 100)
                    last_pct = 100
                continue
            if duration > 0 and line.startswith("out_time_us="):
                try:
                    # out_time_us 单位是微秒（1e6 微秒 = 1 秒）
                    us = int(line.split("=", 1)[1])
                    pct = min(100, max(0, int(us / 1_000_000.0 / duration * 100)))
                    if pct > last_pct:
                        pbar.update_absolute(pct, 100)
                        last_pct = pct
                except Exception:
                    pass
    finally:
        p.wait()
        t.join(timeout=5)

    if p.returncode != 0:
        error_msg = "".join(stderr_lines).strip()[-4000:] or "未知错误"
        raise Exception(f"ffmpeg 截取失败 (返回码 {p.returncode}):\n{error_msg}")


def _run_simple(cmd):
    """不带进度的 ffmpeg 调用（流复制模式几乎瞬时，不需要进度条）。"""
    result = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        error_msg = (result.stderr or result.stdout or "未知错误").strip()[-4000:]
        raise Exception(f"ffmpeg 截取失败 (返回码 {result.returncode}):\n{error_msg}")


class SPCutVideo:
    """
    截取视频「入点 → 出点」之间的片段，以视频输出。

    输入：`video`（VIDEO 节点）与 `video_path`（文件路径字符串）二选一，两者都提供时优先 `video`。
    依赖：系统 PATH 里要有 ffmpeg 与 ffprobe。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "in_point": ("FLOAT", {
                    "default": 0.0,
                    "min": 0.0,
                    "max": 1000000.0,
                    "step": 0.001,
                    "tooltip": "入点（秒，支持小数）。片段从这一时刻开始。",
                }),
                "out_point": ("FLOAT", {
                    "default": 5.0,
                    "min": 0.0,
                    "max": 1000000.0,
                    "step": 0.001,
                    "tooltip": "出点（秒，支持小数）。必须大于入点；超出视频总时长时自动夹到末尾。",
                }),
                "accurate": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "True=精准（重编码，帧级精确，较慢）；False=快速（流复制，几乎瞬时，但只能在关键帧上切，入点会前移到最近关键帧）。",
                }),
                "filename_prefix": ("STRING", {"default": "cut/video"}),
            },
            "optional": {
                # 二选一输入：优先 `video`（VIDEO），其次 `video_path`（文件路径字符串）。
                # 两者都连时以 video 为准；都不提供则报错。
                "video": ("VIDEO",),
                "video_path": ("STRING", {
                    "default": "",
                    "tooltip": "视频文件路径（与 video 二选一，video 优先）。支持相对 ComfyUI 输出目录的路径。",
                }),
                # 以下两项仅在 accurate=True（重编码）时生效
                "crf": ("INT", {
                    "default": 18, "min": 0, "max": 51, "step": 1,
                    "tooltip": "恒定质量因子（越小画质越高、文件越大）：0=无损，18≈视觉无损，23=默认，28+明显压缩。仅 accurate=True 时生效。",
                }),
                "preset": (
                    ["ultrafast", "superfast", "veryfast", "faster", "fast",
                     "medium", "slow", "slower", "veryslow"],
                    {
                        "default": "medium",
                        "tooltip": "编码速度/压缩效率平衡：越慢文件越小、耗时越长，画质不变（CRF 固定时）。仅 accurate=True 时生效。",
                    },
                ),
            },
        }

    # 与 VideoMergeLossless / SPVideo60FPS 对齐：0 号槽 = 输出路径字符串，1 号槽 = VIDEO
    RETURN_TYPES = ("STRING", "VIDEO")
    RETURN_NAMES = ("输出视频路径", "video")
    FUNCTION = "cut"
    CATEGORY = "🎬 Video Tools"
    OUTPUT_NODE = True
    DESCRIPTION = (
        "Cut a video between in_point and out_point (seconds) via ffmpeg and output the clip as VIDEO. "
        "Input: connect either `video` (VIDEO, preferred) or `video_path` (file path string). "
        "accurate=True re-encodes for frame-accurate cuts; False stream-copies (fast, keyframe-aligned)."
    )

    def cut(self, in_point=0.0, out_point=5.0, accurate=True, filename_prefix="cut/video",
            video=None, video_path="", crf=18, preset="medium"):
        # 1. 二选一取输入：优先 video（VIDEO），其次 video_path（文件路径字符串）。
        source = _get_stream_source(video)
        if source is None or (isinstance(source, str) and not source.strip()):
            source = video_path if video_path else None

        if source is None:
            raise Exception(
                "未提供输入：请连接 `video`（VIDEO）或填写 `video_path`（文件路径），二者至少提供一个。"
            )

        is_memory = not isinstance(source, str)
        resolved = None   # 磁盘输入的绝对路径
        tmp_path = None   # 内存视频不得已落盘时的临时文件路径

        try:
            # 2. 拿到 ffmpeg 能直接读的磁盘路径
            if is_memory:
                tmp_path = _stream_to_tempfile(source)
                src_for_ffmpeg = tmp_path
            else:
                source_path = _normalize_path(source)
                resolved = _resolve_existing(source_path)
                if resolved is None:
                    raise Exception(f"视频文件不存在：{source_path}")
                src_for_ffmpeg = resolved

            # 3. 校验入点 / 出点
            start = float(in_point)
            end = float(out_point)
            if start < 0:
                start = 0.0
            if end < 0:
                end = 0.0
            if end <= start:
                raise Exception(
                    f"出点必须大于入点：当前入点 {start:.3f}s、出点 {end:.3f}s。"
                )

            # 4. 探测总时长做软校验（探测失败就不校验，交给 ffmpeg）
            total = _probe_duration(src_for_ffmpeg)
            if total > 0:
                if start >= total:
                    raise Exception(
                        f"入点 {start:.3f}s 超出视频总时长 {total:.3f}s，该时间点没有内容可截。"
                    )
                if end > total:
                    logger.warning(
                        f"[SPCutVideo] 出点 {end:.3f}s 超出视频总时长 {total:.3f}s，已自动截到末尾。"
                    )
                    end = total

            clip_len = max(end - start, 0.0)
            if clip_len <= 0.0005:
                raise Exception(
                    f"片段长度过短（{clip_len:.6f}s），请把出点调大一些。"
                )

            # 5. 决定输出位置与后缀
            #    - 重编码：统一 .mp4（h264/aac，画布与播放器都最稳）
            #    - 流复制：**跟随源文件后缀** —— 抄一个 .mkv/.webm 却命名成 .mp4，
            #      播放器与画布都会按后缀去认它，那就当场认错（同 VideoMergeLossless 的处理）
            full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(
                filename_prefix,
                folder_paths.get_output_directory(),
                1920,
                1080,
            )
            os.makedirs(full_output_folder, exist_ok=True)

            if accurate:
                ext = ".mp4"
            else:
                ext = os.path.splitext(src_for_ffmpeg)[1].lower() or ".mp4"
            output_filename = f"{filename}_{counter:05}_{ext}"
            output_path = os.path.join(full_output_folder, output_filename)

            # 6. 组装 ffmpeg 命令
            #    `-ss` 在 `-i` 之前 = 输入级快速 seek；重编码时 ffmpeg 会从关键帧解码后丢弃，
            #    因此仍然是帧级精准。片段长度用 `-t`（相对片段起点），不用 `-to`。
            base_cmd = [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-ss", f"{start:.6f}",
                "-i", src_for_ffmpeg,
                "-t", f"{clip_len:.6f}",
                # 只取第一路视频 + 第一路音频（没有音频也照样成功）
                "-map", "0:v:0",
                "-map", "0:a:0?",
                "-sn",  # 不带字幕
                "-dn",  # 不带数据流
            ]

            if accurate:
                cmd = base_cmd + [
                    "-c:v", "libx264",
                    "-preset", str(preset),  # 编码速度/压缩效率：越慢文件越小，画质不变（CRF 固定时）
                    "-crf", str(crf),        # 恒定质量因子：18≈视觉无损
                    "-pix_fmt", "yuv420p",   # 兼容性最好（部分播放器/画布不认 yuv444p / 10bit）
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-movflags", "+faststart",  # moov 前置，网页/画布可边下边播
                    "-progress", "pipe:1",      # 进度块写到 stdout（key=value 形式，含 out_time_us）
                    "-nostats",
                    output_path,
                ]
                _run_with_progress(cmd, clip_len)
            else:
                cmd = base_cmd + [
                    "-c", "copy",                     # 关键：不重新编码，几乎瞬时
                    "-avoid_negative_ts", "make_zero",  # 修正 seek 后开头的时间戳
                    "-movflags", "+faststart",
                    output_path,
                ]
                _run_simple(cmd)
                logger.info(
                    "[SPCutVideo] 流复制模式下实际入点会前移到入点之前最近的关键帧；"
                    "若时序对不上，请改用 accurate=True。"
                )

            if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
                raise Exception("ffmpeg 执行完成但没有产出有效视频文件，请检查入点/出点是否落在有效画面内。")

            mode_txt = "精准重编码" if accurate else "流复制（关键帧对齐）"
            logger.info(
                f"[SPCutVideo] 已截取 {start:.3f}s → {end:.3f}s（时长 {clip_len:.3f}s，{mode_txt}）→ {output_path}"
            )

            # 7. 把产物报进 `ui.images`（形状与内置 SaveVideo / VideoMergeLossless 一致），画布才能收到预览
            return {
                "ui": {
                    "images": [
                        {
                            "filename": output_filename,
                            "subfolder": subfolder,
                            "type": "output",
                        }
                    ],
                },
                "result": (output_path, _as_video_output(output_path)),
            }
        finally:
            # 内存视频落盘的临时文件用完即删
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
