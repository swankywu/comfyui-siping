import os
import subprocess
import tempfile
import folder_paths


class VideoMergeLossless:
    """
    极速合并多个视频（不重新编码，仅复制流）
    要求：所有视频 编码、分辨率、帧率必须完全一致
    """
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video_paths": ("*", {
                    "multiline": True,
                    "default": "# 每行一个视频完整路径\nD:\\video1.mp4\nD:\\video2.mp4"
                }),
                "filename_prefix": ("STRING", {
                    "default": "merged/video"
                }),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("合并后视频路径",)
    FUNCTION = "merge_videos"
    CATEGORY = "🎬 Video Tools"
    OUTPUT_NODE = True

    def merge_videos(self, video_paths, filename_prefix):
        try:
            # 处理 video_paths 输入（可能是列表或字符串）
            if isinstance(video_paths, list):
                # 列表形式：每个元素一行，或单个多行字符串
                if len(video_paths) == 1 and isinstance(video_paths[0], str) and "\n" in video_paths[0]:
                    text = video_paths[0]
                else:
                    # 直接是路径列表，合并后处理
                    text = "\n".join(str(p) for p in video_paths)
            else:
                text = video_paths

            # 统一处理：按换行符拆分，过滤空行和注释行
            lines = []
            for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    lines.append(line)

            if len(lines) < 2:
                raise Exception(f"至少需要输入 2 个视频才能合并，当前解析到 {len(lines)} 个路径: {lines}")

            # 获取输出目录和文件名（ComfyUI 标准方式）
            # 使用默认宽高，因为这个节点不处理图像
            full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(
                filename_prefix,
                folder_paths.get_output_directory(),
                1920,
                1080
            )

            # 确保输出目录存在
            os.makedirs(full_output_folder, exist_ok=True)

            # 生成最终输出文件名
            output_filename = f"{filename}_{counter:05}_.mp4"
            output_path = os.path.join(full_output_folder, output_filename)

            # 创建临时列表文件（FFmpeg 合并专用）
            with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
                for path in lines:
                    f.write(f"file '{path}'\n")
                list_path = f.name

            # FFmpeg 命令：不重新编码，极速合并
            cmd = [
                "ffmpeg",
                "-f", "concat",
                "-safe", "0",
                "-i", list_path,
                "-c", "copy",  # 关键：不重新编码
                "-y",
                output_path
            ]

            # 执行合并
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8"
            )

            # 删除临时文件
            os.unlink(list_path)
            return (output_path,)

        except Exception as e:
            raise Exception(f"合并失败：{str(e)}")
