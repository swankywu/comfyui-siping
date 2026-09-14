# 导入依赖
import os
from pathlib import Path

# 节点类定义
class GetVideoFileName:
    """
    上传视频后输出文件名、完整路径、后缀名
    """
    
    # 节点在 ComfyUI 中的显示信息
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                # 上传视频的输入接口
                "video": ("VIDEO",),
            }
        }

    # 输出接口：纯文件名、完整路径、文件后缀、不带扩展名的文件名
    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("纯文件名", "完整文件路径", "文件后缀", "不带扩展名的文件名")
    
    FUNCTION = "get_filename"  # 执行函数名
    CATEGORY = "视频工具"      # 节点分类

    def _safe_get_stream_source(self, video):
        """安全地从 video 对象中拿到流源路径；不规范输入统一兜底为 ''"""
        if video is None:
            return ""
        # 1) 优先尝试标准的 get_stream_source() 方法
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

    def _normalize_path(self, video_path):
        """规范化路径字符串：去引号、去空白"""
        if not video_path:
            return ""
        video_path = str(video_path).strip()
        # 去掉首尾成对出现的单/双引号（部分上传组件会包一层）
        if len(video_path) >= 2 and video_path[0] == video_path[-1] and video_path[0] in ("'", '"'):
            video_path = video_path[1:-1].strip()
        return video_path

    def get_filename(self, video):
        """
        核心逻辑：解析视频路径，提取文件名信息
        video: VideoFromFile 对象，包含视频文件路径信息
        """
        # 1. 安全获取视频完整绝对路径（容忍不规范输入）
        video_path = self._normalize_path(self._safe_get_stream_source(video))

        # 2. 路径为空 / 仅含分隔符等极端情况：返回四个空串，避免下游崩溃
        if not video_path or video_path in (os.sep, "/", "\\"):
            return ("", "", "", "")

        # 3. 跨平台路径标准化（Windows / Linux / macOS）
        try:
            normalized_path = os.path.normpath(video_path)
        except (TypeError, ValueError):
            return ("", "", "", "")

        # 4. 提取纯文件名（带后缀）
        filename = os.path.basename(normalized_path)

        # 5. 提取文件后缀（不带 .），无后缀时为空串而非异常
        try:
            file_ext = Path(filename).suffix.lstrip(".")
        except (TypeError, ValueError):
            file_ext = ""

        # 6. 提取不带扩展名的文件名
        try:
            filename_without_ext = Path(filename).stem
        except (TypeError, ValueError):
            filename_without_ext = ""

        # 返回四个输出值
        return (filename, normalized_path, file_ext, filename_without_ext)

