"""
SPCaptureVideoFrame — 用 ffmpeg 截取视频指定秒数处的一帧，以 IMAGE 输出

行为：
  - 接收输入视频（VIDEO 节点）或文件路径字符串（二选一，视频节点优先）
  - 接收浮点秒数 `time_seconds`，用 ffmpeg 精准 seek 到该时间点
  - 截取该时刻的**关键帧**（-frames:v 1），以图片方式输出
  - 输出为 ComfyUI 标准 IMAGE 张量（HWC / RGB / float32 / 0–1），可直接连给下游图像节点
  - `save_output`（bool）：True 时把帧保存到输出目录（filename_prefix，预览 type=output）；
    False 时帧写入 ComfyUI 的 temp 目录（预览 type=temp，不进输出目录，仍能正常预览）

设计对齐 SPVideo60FPS / VideoMergeLossless：
  - 输入解析复用同一套 `_get_stream_source`（支持 VIDEO 对象、路径字符串、内存 BytesIO）
  - 相对路径按 ComfyUI 输出目录兜底（上游 GetVideoFileName 常给 `video/xxx.mp4`）
  - `OUTPUT_NODE = True`：可作终端输出，且返回 `ui.images` 让画布能收到预览
  - 截帧落盘到输出目录（正式命名 .png），再读回成 IMAGE 张量
  - 内存视频先落临时文件（ffmpeg 从 pipe 读不了 moov 在末尾的 mp4）

说明：
  - 秒数超出视频时长时，ffmpeg 会截到最后一帧附近；这里先探测时长做一次软校验并告警
  - 依赖系统 PATH 里的 ffmpeg / ffprobe
"""

import os
import json
import shutil
import subprocess
import tempfile

import numpy as np
import torch
import folder_paths

from PIL import Image

from .core import logger

try:
    # 与内置 LoadVideo / SPVideo60FPS 同一写法：从 comfy_api 取 VideoFromFile（仅用于兜底识别，非必需）
    from comfy_api.latest import InputImpl as _InputImpl
except Exception:  # pragma: no cover - 老版本 ComfyUI 没有 comfy_api
    _InputImpl = None


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


def _load_image_tensor(path):
    """把磁盘上的图片读成 ComfyUI 标准 IMAGE 张量（1,H,W,3 float32 0–1）。"""
    with Image.open(path) as img:
        img = img.convert("RGB")
        arr = np.array(img).astype(np.float32) / 255.0
    return torch.from_numpy(arr)[None, ...]


class SPCaptureVideoFrame:
    """
    截取视频指定秒数处的一帧，以 IMAGE 输出。

    输入：`video`（VIDEO 节点）与 `video_path`（文件路径字符串）二选一，两者都提供时优先 `video`。
    依赖：系统 PATH 里要有 ffmpeg 与 ffprobe。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "time_seconds": ("FLOAT", {
                    "default": 0.0,
                    "min": 0.0,
                    "max": 1000000.0,
                    "step": 0.001,
                    "tooltip": "要截取帧所在的时间点（秒）。会 seek 到该时刻并取关键帧。",
                }),
                "save_output": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "True 时把帧保存到输出目录（filename_prefix）；False 时仅预览（帧写入 temp 目录），不落盘到输出目录。",
                }),
                "filename_prefix": ("STRING", {"default": "capture/frame"}),
            },
            "optional": {
                # 二选一输入：优先 `video`（VIDEO），其次 `video_path`（文件路径字符串）。
                # 两者都连时以 video 为准；都不提供则报错。
                "video": ("VIDEO",),
                "video_path": ("STRING", {
                    "default": "",
                    "tooltip": "视频文件路径（与 video 二选一，video 优先）。支持相对 ComfyUI 输出目录的路径。",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "capture"
    CATEGORY = "🎬 Video Tools"
    OUTPUT_NODE = True
    DESCRIPTION = (
        "Extract a single frame at a given time (seconds) from a video via ffmpeg, "
        "output as an IMAGE tensor. Input: connect either `video` (VIDEO, preferred) "
        "or `video_path` (file path string)."
    )

    def capture(self, time_seconds=0.0, filename_prefix="capture/frame", save_output=True, video=None, video_path=""):
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

            # 3. 探测时长，做软校验（超出时告警，但不阻断，ffmpeg 会截到末帧附近）
            duration = _probe_duration(src_for_ffmpeg)
            if duration > 0 and float(time_seconds) > duration:
                logger.warning(
                    f"[SPCaptureVideoFrame] 目标秒数 {time_seconds:.3f}s 超出视频时长 {duration:.3f}s，"
                    "ffmpeg 将截取最后一帧附近。"
                )

            # 4. 决定输出位置：
            #    - save_output=True  → 正式输出目录（正式命名 .png），预览 type=output
            #    - save_output=False → 写进 ComfyUI 的 temp 目录，预览 type=temp（不进输出目录）
            #      预览必须走 ui.images 的文件引用；否则 OUTPUT_NODE 的画布拿不到可展示的图片。
            if save_output:
                full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(
                    filename_prefix,
                    folder_paths.get_output_directory(),
                    1920,
                    1080,
                )
                os.makedirs(full_output_folder, exist_ok=True)
                output_filename = f"{filename}_{counter:05}_.png"
                output_path = os.path.join(full_output_folder, output_filename)
                ui_type = "output"
                cleanup_output = False
            else:
                # 复用 get_save_image_path 的计数器，但把落地目录换成 temp 目录
                temp_dir = folder_paths.get_temp_directory()
                _, filename, counter, _, _ = folder_paths.get_save_image_path(
                    filename_prefix,
                    temp_dir,
                    1920,
                    1080,
                )
                output_filename = f"{filename}_{counter:05}_.png"
                output_path = os.path.join(temp_dir, output_filename)
                ui_type = "temp"
                subfolder = ""
                cleanup_output = False

            # 5. ffmpeg 精准 seek + 截取一帧
            #    `-ss` 放在 `-i` 之前是快速输入级 seek；`-frames:v 1` 只取一帧；
            #    `-vframes 1` 等价，这里用 `-frames:v 1` 明确只取视频流一帧。
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-ss", f"{float(time_seconds):.6f}",
                "-i", src_for_ffmpeg,
                "-frames:v", "1",
                "-f", "image2",
                output_path,
            ]
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
                raise Exception(f"ffmpeg 截帧失败 (返回码 {result.returncode}):\n{error_msg}")

            if not os.path.isfile(output_path):
                raise Exception("ffmpeg 执行完成但未生成图片文件，可能是目标时间点无有效帧。")

            # 6. 读回为 IMAGE 张量
            image = _load_image_tensor(output_path)

            logger.info(
                f"[SPCaptureVideoFrame] 已在 {float(time_seconds):.3f}s 处截取一帧"
                + (f" → {output_path}" if save_output else "（仅预览，未保存到输出目录）")
            )

            # 7. 把产物报进 `ui.images` 让画布能预览：
            #    - save_output=True  → type=output（保存在输出目录）
            #    - save_output=False → type=temp（仅预览，文件在 temp 目录，不进输出目录）
            return {
                "ui": {
                    "images": [
                        {
                            "filename": output_filename,
                            "subfolder": subfolder,
                            "type": ui_type,
                        }
                    ]
                },
                "result": (image,),
            }
        finally:
            # 内存视频落盘的临时文件用完即删
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
