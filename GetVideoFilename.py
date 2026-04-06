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

    # 输出接口：纯文件名、完整路径、文件后缀
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("纯文件名", "完整文件路径", "文件后缀")
    
    FUNCTION = "get_filename"  # 执行函数名
    CATEGORY = "视频工具"      # 节点分类

    def get_filename(self, video):
        """
        核心逻辑：解析视频路径，提取文件名信息
        video: VideoFromFile 对象，包含视频文件路径信息
        """
        # 1. 获取视频完整绝对路径
        # VideoFromFile 对象的 get_stream_source() 方法返回文件路径
        video_path = video.get_stream_source()

        # 2. 提取纯文件名（带后缀）
        filename = os.path.basename(video_path)

        # 3. 提取文件后缀（不带.）
        file_ext = Path(filename).suffix.lstrip(".")

        # 返回三个输出值
        return (filename, video_path, file_ext)

