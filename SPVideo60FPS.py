"""
SPVideo60FPS — 用 ffmpeg 把高帧率视频统一转成 60fps

行为：
  - 先探测输入视频的帧率（ffprobe，优先 avg_frame_rate，回退 r_frame_rate）
  - 帧率 > 60：用 ffmpeg 重编码到 60fps（`fps=60` 滤镜 + libx264，音频原样复制）
  - 帧率 ≤ 60：不转换、不重编码，直接透传原视频，并给出 warning

设计对齐 VideoMergeLossless：
  - `OUTPUT_NODE = True`：可作终端输出（少了它，无输出节点时会被 `Prompt has no outputs` 拒收）
  - 返回 0 号槽 = 输出路径字符串，末号槽 = VIDEO（用 `comfy_api.latest` 包成 VIDEO 值）
  - 转换产物通过 `ui.images` 报进 `history.outputs`，画布才能收到文件
  - 透传时通过 `ui.text` + logger.warning 给出提示（不产生新文件）

输入约定（二选一，video 优先）：
  - `video` 接受 ComfyUI 的 VIDEO 值（来自内置 LoadVideo / 上游 VideoFromFile）。
  - `video_path` 接受文件路径字符串（支持相对 ComfyUI 输出目录的路径）。
  - 两者都提供时以 `video` 为准；都不提供则报错。
  - 透传时直接返回原视频对象，转换时把落盘文件重新包成 VIDEO。
"""

import os
import shutil
import subprocess
import folder_paths

from .core import logger

try:
    # ★ 与内置 `LoadVideo` / `VideoMergeLossless` 同一写法：把磁盘上的视频包成 `VIDEO` 值。
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
    """安全地从 video 对象（或路径字符串）里拿到流源路径；不规范输入统一兜底为 ''。"""
    if video is None:
        return ""
    # 0) 直接给字符串路径的情况
    if isinstance(video, str):
        return video
    # 1) 标准 VIDEO 对象：get_stream_source()
    getter = getattr(video, "get_stream_source", None)
    if callable(getter):
        try:
            src = getter()
            return "" if src is None else str(src)
        except Exception:
            pass
    # 2) 兼容部分节点把路径直接挂在属性上的情况
    for attr in ("stream_source", "file_path", "path", "filename", "video_path"):
        val = getattr(video, attr, None)
        if val:
            return str(val)
    # 3) 兼容 dict 形式的对象
    if isinstance(video, dict):
        for key in ("stream_source", "file_path", "path", "filename", "video_path", "video"):
            val = video.get(key)
            if val:
                return str(val)
    return ""


def _normalize_path(video_path):
    """规范化路径字符串：去引号、去空白。"""
    if not video_path:
        return ""
    video_path = str(video_path).strip()
    if len(video_path) >= 2 and video_path[0] == video_path[-1] and video_path[0] in ("'", '"'):
        video_path = video_path[1:-1].strip()
    return video_path


def _parse_fraction(s):
    """把 ffprobe 的 `30000/1001` 或 `60/1` 这类帧率字符串解析成 float。"""
    s = (s or "").strip()
    try:
        if "/" in s:
            num, den = s.split("/", 1)
            den = float(den)
            if den == 0:
                return 0.0
            return float(num) / den
        return float(s)
    except Exception:
        return 0.0


def _probe_fps(path):
    """用 ffprobe 读取视频平均帧率。返回 float；探测失败返回 0.0。"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0

    # avg_frame_rate 对可变帧率更准确，r_frame_rate 作为回退
    cmd = [
        ffprobe, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate,r_frame_rate",
        "-of", "csv=p=0",
        path,
    ]
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            return 0.0
        parts = (result.stdout or "").strip().split(",")
        if not parts or not parts[0].strip():
            return 0.0
        avg = _parse_fraction(parts[0])
        if avg > 0:
            return avg
        if len(parts) > 1:
            return _parse_fraction(parts[1])
        return 0.0
    except Exception:
        return 0.0


class SPVideo60FPS:
    """
    把 >60fps 的视频转成 60fps；≤60fps 的视频不转换、直接透传并给出 warning。

    依赖：系统 PATH 里要有 ffmpeg 与 ffprobe。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "filename_prefix": ("STRING", {"default": "video/60fps"}),
            },
            "optional": {
                # 二选一输入：优先 `video`（VIDEO），其次 `video_path`（文件路径字符串）。
                # 两者都连时以 video 为准；都不提供则报错。
                "video": ("VIDEO",),
                "video_path": ("STRING", {
                    "default": "",
                    "tooltip": "视频文件路径（与 video 二选一，video 优先）。支持相对 ComfyUI 输出目录的路径。",
                }),
                # CRF（恒定质量因子，0–51）：越小画质越高、文件越大。仅 >60fps 重编码时生效。
                #   0=无损，18≈视觉无损，23=x264 默认，28+ 明显压缩。
                "crf": ("INT", {
                    "default": 18, "min": 0, "max": 51, "step": 1,
                    "tooltip": "恒定质量因子（越小画质越高、文件越大）：0=无损，18≈视觉无损，23=默认，28+明显压缩。仅 >60fps 重编码时生效。",
                }),
                # preset（x264 编码速度预设）：越慢越省体积，画质不变（CRF 固定时）。
                #   仅 >60fps 重编码时生效。
                "preset": (
                    ["ultrafast", "superfast", "veryfast", "faster", "fast",
                     "medium", "slow", "slower", "veryslow"],
                    {
                        "default": "medium",
                        "tooltip": "编码速度/压缩效率平衡：越慢文件越小、耗时越长，画质不变（CRF 固定时）。仅 >60fps 重编码时生效。",
                    },
                ),
            },
        }

    # 与 VideoMergeLossless 对齐：0 号槽 = 输出路径字符串，1 号槽 = VIDEO
    RETURN_TYPES = ("STRING", "VIDEO")
    RETURN_NAMES = ("输出视频路径", "video")
    FUNCTION = "convert"
    CATEGORY = "🎬 Video Tools"
    OUTPUT_NODE = True
    DESCRIPTION = (
        "Convert video above 60fps down to 60fps via ffmpeg (fps=60 filter + libx264, "
        "audio copied). Videos at or below 60fps are passed through unchanged with a warning. "
        "Input: connect either `video` (VIDEO, preferred) or `video_path` (file path string)."
    )

    def convert(self, video=None, video_path="", filename_prefix="video/60fps", crf=18, preset="medium"):
        # 1. 二选一取输入：优先 video（VIDEO），其次 video_path（文件路径字符串）。
        #    `_get_stream_source` 对 None 返回 ""，于是自然回退到 video_path。
        raw = _get_stream_source(video) or (video_path or "")
        source_path = _normalize_path(raw)
        if not source_path:
            raise Exception(
                "未提供输入：请连接 `video`（VIDEO）或填写 `video_path`（文件路径），二者至少提供一个。"
            )

        resolved = _resolve_existing(source_path)
        if resolved is None:
            raise Exception(f"视频文件不存在：{source_path}")

        # 2. 探测帧率
        fps = _probe_fps(resolved)
        if fps <= 0:
            raise Exception(
                "无法读取视频帧率（ffprobe 未找到或探测失败）。"
                "请确认已安装 ffmpeg/ffprobe 并加入系统 PATH。"
            )

        # 3. ≤ 60fps：不转换，直接透传 + warning
        if fps <= 60:
            msg = f"⚠️ 视频帧率 {fps:.2f} fps ≤ 60，无需转换，已直接输出原视频。"
            logger.warning(f"[SPVideo60FPS] {msg}")
            return {
                "ui": {"text": [msg]},
                # 透传：不产生新文件，直接返回原路径与原 VIDEO
                "result": (resolved, _as_video_output(resolved)),
            }

        # 4. > 60fps：ffmpeg 重编码到 60fps
        full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(
            filename_prefix,
            folder_paths.get_output_directory(),
            1920,
            1080,
        )
        os.makedirs(full_output_folder, exist_ok=True)

        output_filename = f"{filename}_{counter:05}_.mp4"
        output_path = os.path.join(full_output_folder, output_filename)

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i", resolved,
            "-vf", "fps=60",
            "-c:v", "libx264",
            "-preset", str(preset),  # 编码速度/压缩效率预设：越慢文件越小、画质不变（CRF 固定时）
            "-crf", str(crf),        # 恒定质量因子：越小画质越高、文件越大（18≈视觉无损）
            "-c:a", "copy",  # 音频不重编码，保持原样
            output_path,
        ]

        result = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "未知错误"
            raise Exception(f"ffmpeg 转换失败 (返回码 {result.returncode}):\n{error_msg}")

        msg = f"✅ 已将视频从 {fps:.2f} fps 转换为 60 fps。"
        logger.info(f"[SPVideo60FPS] {msg}")

        # ★ 把产物报进 `ui`（形状与内置 SaveVideo / VideoMergeLossless 一致），
        #   同时带上 `text` 供画布直接显示结果提示。
        return {
            "ui": {
                "images": [
                    {
                        "filename": output_filename,
                        "subfolder": subfolder,
                        "type": "output",
                    }
                ],
                "text": [msg],
            },
            "result": (output_path, _as_video_output(output_path)),
        }
