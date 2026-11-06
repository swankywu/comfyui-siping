"""
SPCropVideo — 用 ffmpeg 对视频进行「裁剪 + 旋转 + 翻转」

行为：
  - 接收输入视频（VIDEO 对象或文件路径字符串，二选一，**VIDEO 优先**）
  - 按 `x / y / width / height` 裁剪画面
  - 按 `rotation`（none / 90 degrees / 180 degrees）旋转画面
  - 按 `flip`（none / horizontal / vertical / flip_hv）翻转画面
  - 三种变换按固定顺序合成：**先裁剪 → 再旋转 → 最后翻转**
  - 输出 0 号槽 = 输出路径字符串，1 号槽 = VIDEO（与 SPVideo60FPS / SPCutVideo 对齐）

ffmpeg 滤镜链：
  - 裁剪：`crop=w:h:x:y`（x/y 自动夹到画面范围内，避免越界报错）
  - 旋转：`transpose=1`（顺时针 90°）/ `transpose=2`（逆时针 90°，等价顺时针 270°）
          / `transpose=2,transpose=2`（180°）
  - 翻转：`hflip`（水平）/ `vflip`（垂直）/ `hflip,vflip`（同时）

设计对齐 SPVideo60FPS / SPCutVideo：
  - 输入解析共用同一套写法（支持 VIDEO 对象、路径字符串、内存 BytesIO）
  - 相对路径按 ComfyUI 输出目录兜底
  - 内存视频先落临时文件，用完即删
  - `OUTPUT_NODE = True` + 返回 `ui.images`，画布才能收到产物预览
  - 通过 `-progress` + ComfyUI ProgressBar 实时汇报进度

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
    """路径存在则返回绝对路径，否则返回 None。相对路径按 ComfyUI 输出目录兜底。"""
    if os.path.isfile(path):
        return path
    if not os.path.isabs(path):
        candidate = os.path.join(folder_paths.get_output_directory(), path)
        if os.path.isfile(candidate):
            return candidate
    return None


def _get_stream_source(video):
    """安全地从 video 对象里拿到流源（文件路径字符串 / BytesIO / None）。"""
    if video is None:
        return None
    if isinstance(video, str):
        return video
    getter = getattr(video, "get_stream_source", None)
    if callable(getter):
        try:
            src = getter()
            if src is not None:
                return src
        except Exception:
            pass
    for attr in ("stream_source", "file_path", "path", "filename", "video_path"):
        val = getattr(video, attr, None)
        if val:
            return val
    if isinstance(video, dict):
        for key in ("stream_source", "file_path", "path", "filename", "video_path", "video"):
            val = video.get(key)
            if val:
                return val
    if hasattr(video, "read"):
        return video
    return None


def _stream_to_tempfile(stream):
    """把内存视频流落盘成临时文件，返回路径。"""
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
    """用 ffprobe 探测视频总时长（秒）。失败返回 0.0。"""
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


def _build_filter_chain(x, y, width, height, rotation, flip):
    """把 裁剪 → 旋转 → 翻转 三个动作合成为一段 ffmpeg -vf 滤镜链字符串。

    顺序固定：先裁剪（`crop`）→ 再旋转（`transpose`）→ 最后翻转（`hflip`/`vflip`）。
    """
    parts = []

    # 1) 裁剪：crop=w:h:x:y。x/y 用负值会触发 ffmpeg 报错，这里夹到 [0, ...)，
    #    实际越界由 ffmpeg 自动收紧到画面内（crop 滤镜对超界区域会裁剪到有效范围）。
    if width is not None and height is not None and width > 0 and height > 0:
        cx = max(0, int(x or 0))
        cy = max(0, int(y or 0))
        parts.append(f"crop={int(width)}:{int(height)}:{cx}:{cy}")

    # 2) 旋转：transpose 是 90° 步进旋转（会自动交换宽高）。
    #    transpose=1 顺时针 90°；transpose=2 逆时针 90°（等价顺时针 270°）。
    if rotation == "90 degrees":
        parts.append("transpose=1")
    elif rotation == "180 degrees":
        parts.append("transpose=1,transpose=1")
    # 注：rotation 参数里没有 "270 degrees"，故 270 不在枚举内。

    # 3) 翻转：水平 / 垂直 / 两者。
    if flip == "horizontal":
        parts.append("hflip")
    elif flip == "vertical":
        parts.append("vflip")
    elif flip == "flip_hv":
        parts.append("hflip,vflip")

    return ",".join(parts)


def _run_with_progress(cmd, duration):
    """跑 ffmpeg 并把实时进度汇报给 ComfyUI（ProgressBar）。失败时抛异常。"""
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
            if line == "progress=end":
                if duration > 0:
                    pbar.update_absolute(100, 100)
                    last_pct = 100
                continue
            if duration > 0 and line.startswith("out_time_us="):
                try:
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
        raise Exception(f"ffmpeg 处理失败 (返回码 {p.returncode}):\n{error_msg}")


class SPCropVideo:
    """
    对视频进行「裁剪 + 旋转 + 翻转」。

    输入：`video`（VIDEO 节点）与 `video_path`（文件路径字符串）二选一，两者都提供时优先 `video`。
    变换顺序固定：先裁剪 → 再旋转 → 最后翻转。
    依赖：系统 PATH 里要有 ffmpeg 与 ffprobe。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "x": ("INT", {
                    "default": 0, "min": 0, "max": 1000000, "step": 1,
                    "tooltip": "裁剪区域左上角的横坐标（像素）。超出画面右边界时自动收紧。",
                }),
                "y": ("INT", {
                    "default": 0, "min": 0, "max": 1000000, "step": 1,
                    "tooltip": "裁剪区域左上角的纵坐标（像素）。超出画面下边界时自动收紧。",
                }),
                "width": ("INT", {
                    "default": 512, "min": 1, "max": 1000000, "step": 1,
                    "tooltip": "裁剪区域的宽度（像素）。",
                }),
                "height": ("INT", {
                    "default": 512, "min": 1, "max": 1000000, "step": 1,
                    "tooltip": "裁剪区域的高度（像素）。",
                }),
                "rotation": (
                    ["none", "90 degrees", "180 degrees"],
                    {"default": "none", "tooltip": "旋转角度：none（不旋转）/ 90 degrees（顺时针 90°）/ 180 degrees（180°）。"},
                ),
                "flip": (
                    ["none", "horizontal", "vertical", "flip_hv"],
                    {"default": "none", "tooltip": "翻转：none（不翻转）/ horizontal（水平镜像）/ vertical（垂直翻转）/ flip_hv（水平+垂直）。"},
                ),
                "filename_prefix": ("STRING", {"default": "video/crop"}),
            },
            "optional": {
                "video": ("VIDEO",),
                "video_path": ("STRING", {
                    "default": "",
                    "tooltip": "视频文件路径（与 video 二选一，video 优先）。支持相对 ComfyUI 输出目录的路径。",
                }),
                "crf": ("INT", {
                    "default": 18, "min": 0, "max": 51, "step": 1,
                    "tooltip": "恒定质量因子（越小画质越高、文件越大）：0=无损，18≈视觉无损，23=默认，28+明显压缩。",
                }),
                "preset": (
                    ["ultrafast", "superfast", "veryfast", "faster", "fast",
                     "medium", "slow", "slower", "veryslow"],
                    {
                        "default": "medium",
                        "tooltip": "编码速度/压缩效率平衡：越慢文件越小、耗时越长，画质不变（CRF 固定时）。",
                    },
                ),
            },
        }

    RETURN_TYPES = ("STRING", "VIDEO")
    RETURN_NAMES = ("输出视频路径", "video")
    FUNCTION = "process"
    CATEGORY = "🎬 Video Tools"
    OUTPUT_NODE = True
    DESCRIPTION = (
        "Crop, rotate and flip a video via ffmpeg. "
        "Transform order is fixed: crop -> rotate -> flip. "
        "Input: connect either `video` (VIDEO, preferred) or `video_path` (file path string)."
    )

    def process(self, x=0, y=0, width=512, height=512, rotation="none", flip="none",
                filename_prefix="video/crop", video=None, video_path="", crf=18, preset="medium"):
        # 1. 二选一取输入：优先 video（VIDEO），其次 video_path（文件路径字符串）。
        source = _get_stream_source(video)
        if source is None or (isinstance(source, str) and not source.strip()):
            source = video_path if video_path else None

        if source is None:
            raise Exception(
                "未提供输入：请连接 `video`（VIDEO）或填写 `video_path`（文件路径），二者至少提供一个。"
            )

        is_memory = not isinstance(source, str)
        tmp_path = None

        try:
            # 2. 拿到 ffmpeg 能直接读的磁盘路径
            if is_memory:
                src_for_ffmpeg = _stream_to_tempfile(source)
                tmp_path = src_for_ffmpeg
            else:
                source_path = _normalize_path(source)
                resolved = _resolve_existing(source_path)
                if resolved is None:
                    raise Exception(f"视频文件不存在：{source_path}")
                src_for_ffmpeg = resolved

            # 3. 组装滤镜链（裁剪 → 旋转 → 翻转）
            filter_chain = _build_filter_chain(x, y, width, height, rotation, flip)
            if not filter_chain:
                # 什么都没做时，透传原视频即可（理论上至少会有 crop 滤镜）
                return {"result": (src_for_ffmpeg, _as_video_output(src_for_ffmpeg))}

            # 4. 探测总时长用于进度汇报（探测失败就不汇报，交给 ffmpeg）
            duration = _probe_duration(src_for_ffmpeg)

            # 5. 决定输出位置
            full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(
                filename_prefix,
                folder_paths.get_output_directory(),
                1920,
                1080,
            )
            os.makedirs(full_output_folder, exist_ok=True)

            output_filename = f"{filename}_{counter:05}_.mp4"
            output_path = os.path.join(full_output_folder, output_filename)

            # 6. 组装 ffmpeg 命令（重编码输出，保证画布与播放器兼容）
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-i", src_for_ffmpeg,
                "-vf", filter_chain,
                "-c:v", "libx264",
                "-preset", str(preset),
                "-crf", str(crf),
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                "-map", "0:v:0",
                "-map", "0:a:0?",
                "-progress", "pipe:1",
                "-nostats",
                output_path,
            ]
            _run_with_progress(cmd, duration)

            if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
                raise Exception("ffmpeg 执行完成但没有产出有效视频文件，请检查裁剪参数是否落在有效画面内。")

            logger.info(
                f"[SPCropVideo] 已裁剪/旋转/翻转 → {output_path} "
                f"(crop={width}x{height}@{x},{y}, rotation={rotation}, flip={flip})"
            )

            # 7. 把产物报进 `ui.images`，画布才能收到预览
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
