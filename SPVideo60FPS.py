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
  - 透传时通过 logger.warning 给出提示（不产生新文件）
  - 转码时通过 ffmpeg `-progress` + ComfyUI ProgressBar 实时汇报进度

输入约定（二选一，video 优先）：
  - `video` 接受 ComfyUI 的 VIDEO 值（来自内置 LoadVideo / 上游 VideoFromFile）。
  - `video_path` 接受文件路径字符串（支持相对 ComfyUI 输出目录的路径）。
  - 两者都提供时以 `video` 为准；都不提供则报错。
  - 透传时直接返回原视频对象，转换时把落盘文件重新包成 VIDEO。
"""

import os
import shutil
import subprocess
import tempfile
import threading
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
    """
    安全地从 video 对象里拿到流源。返回：文件路径字符串、BytesIO / file-like 内存对象，或 `None`。

    ★ 注意：`VideoFromFile.get_stream_source()` 可能返回 `io.BytesIO`（内存视频，无磁盘路径），
      这里**原样返回**、不做 `str()` —— 否则会变成 `"<_io.BytesIO object at ...>"` 这种废串。
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


def _copy_to_output(src_path, filename_prefix):
    """把 `src_path` 原样复制到输出目录（正式命名），返回新路径。用于内存视频的"透传"落盘。"""
    full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(
        filename_prefix,
        folder_paths.get_output_directory(),
        1920,
        1080,
    )
    os.makedirs(full_output_folder, exist_ok=True)
    ext = os.path.splitext(src_path)[1].lower() or ".mp4"
    output_filename = f"{filename}_{counter:05}_{ext}"
    output_path = os.path.join(full_output_folder, output_filename)
    if os.path.abspath(src_path) != os.path.abspath(output_path):
        shutil.copy2(src_path, output_path)
    return output_path


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


def _probe_duration(path):
    """用 ffprobe 探测视频总时长（秒）。失败返回 0.0（此时转码不再汇报进度）。"""
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


def _transcode_with_progress(src_path, output_path, preset, crf):
    """
    用 ffmpeg 把 `src_path` 转成 60fps，并把实时进度汇报给 ComfyUI（ProgressBar）。

    - `-progress pipe:1` 让 ffmpeg 把 `out_time_us=<微秒>` 等 key=value 进度块写到 stdout；
    - `-nostats` 关掉 stderr 上的人类可读统计，只留错误信息；
    - stderr 用后台线程读，避免管道缓冲区写满把 ffmpeg 卡死（死锁）；
    - 失败时把 stderr 尾部抛出来，方便定位。
    """
    # 延迟导入：ProgressBar 依赖 ComfyUI 执行环境（非 ComfyUI 环境下 import 会失败）
    from comfy.utils import ProgressBar

    duration = _probe_duration(src_path)

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i", src_path,
        "-vf", "fps=60",
        "-c:v", "libx264",
        "-preset", str(preset),  # 编码速度/压缩效率预设：越慢文件越小、画质不变（CRF 固定时）
        "-crf", str(crf),        # 恒定质量因子：越小画质越高、文件越大（18≈视觉无损）
        "-c:a", "copy",  # 音频不重编码，保持原样
        "-progress", "pipe:1",  # 进度块写到 stdout（key=value 形式，含 out_time_us）
        "-nostats",             # 关掉 stderr 上的默认统计，只保留错误
        output_path,
    ]

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
            # 编码结束标记：把进度顶到 100%（ffmpeg 最后一帧时间戳会略小于总时长，永远到不了 100）
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
        raise Exception(f"ffmpeg 转换失败 (返回码 {p.returncode}):\n{error_msg}")


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
        source = _get_stream_source(video)
        if source is None or (isinstance(source, str) and not source.strip()):
            source = video_path if video_path else None

        if source is None:
            raise Exception(
                "未提供输入：请连接 `video`（VIDEO）或填写 `video_path`（文件路径），二者至少提供一个。"
            )

        # 2. 物化为磁盘文件：
        #    - 字符串路径 → 解析出绝对路径（相对路径回退到 ComfyUI 输出目录）
        #    - BytesIO / file-like（内存视频，无磁盘路径）→ 先落盘到临时文件
        is_memory = False
        if isinstance(source, str):
            source_path = _normalize_path(source)
            resolved = _resolve_existing(source_path)
            if resolved is None:
                raise Exception(f"视频文件不存在：{source_path}")
        else:
            resolved = _stream_to_tempfile(source)
            is_memory = True

        try:
            # 3. 探测帧率
            fps = _probe_fps(resolved)
            if fps <= 0:
                raise Exception(
                    "无法读取视频帧率（ffprobe 未找到或探测失败）。"
                    "请确认已安装 ffmpeg/ffprobe 并加入系统 PATH。"
                )

            # 4. ≤ 60fps：不转换，直接透传 + warning
            if fps <= 60:
                logger.warning(
                    f"[SPVideo60FPS] 视频帧率 {fps:.2f} fps ≤ 60，无需转换，已直接输出原视频。"
                )
                if is_memory:
                    # 内存视频没有持久化文件：把落盘好的临时文件复制到输出目录（内容原样，等价"另存"）
                    out_path = _copy_to_output(resolved, filename_prefix)
                else:
                    out_path = resolved
                return {
                    "result": (out_path, _as_video_output(out_path)),
                }

            # 5. > 60fps：ffmpeg 重编码到 60fps
            full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(
                filename_prefix,
                folder_paths.get_output_directory(),
                1920,
                1080,
            )
            os.makedirs(full_output_folder, exist_ok=True)

            output_filename = f"{filename}_{counter:05}_.mp4"
            output_path = os.path.join(full_output_folder, output_filename)

            # 转码（实时汇报进度到 ComfyUI 前端；失败时内部抛异常）
            _transcode_with_progress(resolved, output_path, preset, crf)

            logger.info(f"[SPVideo60FPS] 已将视频从 {fps:.2f} fps 转换为 60 fps。")

            # ★ 把产物报进 `ui.images`（形状与内置 SaveVideo / VideoMergeLossless 一致），画布才能收到预览
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
            # 内存视频落盘的临时文件用完即删（透传时已复制到输出目录、转码时已产出新文件）
            if is_memory:
                try:
                    os.unlink(resolved)
                except Exception:
                    pass
