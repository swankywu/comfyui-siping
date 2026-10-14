"""
极速合并多个视频（不重新编码，仅复制流）

## 与 Siping Canvas 对齐的三件事（2026-10-09）

别人问"我这个自定义节点要怎样才能被当成**输出**"，答案分三层 —— 这个文件三层都做了：

| 层 | 要满足什么 | 这个文件怎么做的 |
|---|---|---|
| ① ComfyUI 认它是输出节点 | 类属性 `OUTPUT_NODE = True` | 本来就有。⚠️ 少了它：图里没有别的输出节点时会被 `Prompt has no outputs` **硬拒收**，而且**根本不会被执行**（ComfyUI 只跑"输出节点及其祖先"） |
| ② 画布的导入向导把它列成"输出口候选" | 它是**终端节点**（没人引用它的输出）**且** `/object_info` 的 `output` 里有可映射类型：`IMAGE` / `VIDEO` / `AUDIO` / `STRING` | 加了 **`VIDEO`**。⚠️ 只声明 `("STRING",)` 时它会被判成**数据源头**（"没有连线型输入 + 出口全是 STRING" 那条判据），**反而不进候选** |
| ③ 画布能收到产物 | 返回值里带 `ui` 文件引用（与内置 `SaveImage` / `SaveVideo` 同一个形状） | 返回 `{"ui": {"images": [{filename, subfolder, type}]}, "result": (...)}`。⚠️ 只返回路径字符串的话，ComfyUI 的 `history.outputs` 里**什么都没有**，画布会报"出口没有产出任何东西" |

⚠️ `RETURN_TYPES` 的顺序是**刻意**的：`STRING` 留在 **0 号槽**。老工作流里指向本节点的连线取的就是
0 号槽（`siping_VideoMergeLossless → ShowText`），把它换成 `VIDEO` 会让那些连线当场断掉。
⇒ 画布那边请勾 **1 号槽（video）** 那个出口。两个槽都是终端类型，导入向导会把它们都列出来。

## 输入侧的一件事

`video_paths` 从 `("*", ...)` 改成了 `("STRING", {"multiline": True, ...})`。

原来那个 `"*"`（通配）在前端是**连线口**、不是文本框 —— 存下来的工作流里它是
`{"name":"video_paths","type":"*","link":271}`，没有 `widget` 字段，也就是说
`INPUT_TYPES` 里那个多行默认值**从来没被渲染过**。后果是画布**没法给它输入**：
通配类型不在"可作为画面的输入"那几类里，能力卡上连一个能填的框都没有。

改成 `STRING` + `multiline` 之后：ComfyUI 里它是一个多行文本框（作者原本的意图），
而**已经被连线喂着的**老工作流照旧能用（ComfyUI 允许把连线接到控件口上，
`CLIPTextEncode.text` 就是这么用的）。想保留"纯连线口、不要文本框"就加 `"forceInput": True`。
"""

import os
import shutil
import subprocess
import tempfile
import folder_paths

try:
    # ★ 与内置 `LoadVideo` 同一个写法：把磁盘上的视频包成 ComfyUI 的 `VIDEO` 值。
    from comfy_api.latest import InputImpl as _InputImpl
except Exception:  # pragma: no cover - 老版本 ComfyUI 没有 comfy_api
    _InputImpl = None


def _as_video_output(path):
    """
    把落地好的视频包成 `VIDEO` 值（1 号出口）。

    ⚠️ 装不上 `comfy_api.latest` 时退化成"原样返回路径字符串" —— 产物照旧落地（0 号出口与 `ui`
    都不受影响），只是这个 VIDEO 口在 ComfyUI 里连给别的节点时类型对不上。
    """
    if _InputImpl is None:
        return path
    try:
        return _InputImpl.VideoFromFile(path)
    except Exception:
        return path


def _to_text(video_paths):
    """`video_paths` 可能是字符串、多行字符串，或列表（被上游节点喂进来时就是列表）。"""
    if isinstance(video_paths, (list, tuple)):
        if len(video_paths) == 1 and isinstance(video_paths[0], str) and "\n" in video_paths[0]:
            return video_paths[0]
        return "\n".join(str(p) for p in video_paths)
    return str(video_paths)


def _parse_lines(text):
    """按换行拆开，丢掉空行与 `#` 注释行。"""
    out = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


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


class VideoMergeLossless:
    """
    极速合并多个视频（不重新编码，仅复制流）
    要求：所有视频 编码、分辨率、帧率必须完全一致

    只给一个视频时**直接复制重命名**（不抛错）—— 保证"只要上游给得出一个视频，这个节点就一定有产物"。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video_paths": ("STRING", {
                    "multiline": True,
                    "default": "# 每行一个视频完整路径\nD:\\video1.mp4\nD:\\video2.mp4"
                }),
                "filename_prefix": ("STRING", {
                    "default": "merged/video"
                }),
            }
        }

    # ⚠️ 顺序不能动：老连线取的是 0 号槽（见文件头）
    RETURN_TYPES = ("STRING", "VIDEO")
    RETURN_NAMES = ("合并后视频路径", "video")
    FUNCTION = "merge_videos"
    CATEGORY = "🎬 Video Tools"
    OUTPUT_NODE = True

    def merge_videos(self, video_paths, filename_prefix):
        try:
            lines = _parse_lines(_to_text(video_paths))

            if len(lines) == 0:
                raise Exception(
                    "一个视频路径都没有解析到。请在 `video_paths` 里每行写一个视频的完整路径"
                    "（以 # 开头的行会被忽略）。"
                )

            # ★ 先自己查一遍存在性：ffmpeg 对"文件不存在"报的是它自己那套话（而且可能只说第一个），
            #   这里直接把缺哪几个列出来。画布那边"上游这一轮没产出文件"是常见情形。
            resolved = []
            missing = []
            for path in lines:
                hit = _resolve_existing(path)
                if hit is None:
                    missing.append(path)
                else:
                    resolved.append(hit)
            if missing:
                raise Exception("这些视频文件不存在：\n" + "\n".join(missing))

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

            if len(resolved) == 1:
                # ★★ 只有一个视频 ⇒ **直接复制重命名**，不再抛错（用户 2026-10-09）。
                #
                # 这条路保证"只要上游给得出一个视频，这个节点就一定有产物"：
                # 否则画布上"只连进来一段视频"的那一轮会因为"至少需要 2 个"而整体失败，
                # 而上游那一段视频其实已经好好的了。
                #
                # ⚠️ 容器后缀**跟着源文件走**：抄一个 `.mkv` 却命名成 `.mp4`，
                #    播放器和画布都会按后缀去认它，那就当场认错。
                ext = os.path.splitext(resolved[0])[1].lower() or ".mp4"
                output_filename = f"{filename}_{counter:05}_{ext}"
                output_path = os.path.join(full_output_folder, output_filename)

                # 极端情况：源文件就是目标文件（同一路径）—— copy 会抛 SameFileError，那就什么都不用做
                if os.path.abspath(resolved[0]) != os.path.abspath(output_path):
                    shutil.copy2(resolved[0], output_path)
            else:
                output_filename = f"{filename}_{counter:05}_.mp4"
                output_path = os.path.join(full_output_folder, output_filename)

                # 创建临时列表文件（FFmpeg 合并专用）
                # 注意：Windows路径中的反斜杠需要转义或转换为正斜杠
                list_path = None
                with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt", encoding="utf-8") as f:
                    for path in resolved:
                        # 转换为正斜杠并转义单引号
                        safe_path = path.replace("\\", "/").replace("'", "'\\''")
                        f.write(f"file '{safe_path}'\n")
                    list_path = f.name

                # FFmpeg 命令：不重新编码，极速合并
                # `-hide_banner` 只是让出错时那句 stderr 里少一段版本横幅
                cmd = [
                    "ffmpeg",
                    "-hide_banner",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", list_path,
                    "-c", "copy",  # 关键：不重新编码
                    "-y",
                    output_path
                ]

                try:
                    result = subprocess.run(
                        cmd,
                        check=False,
                        capture_output=True,
                        text=True,
                        encoding="utf-8"
                    )
                finally:
                    # 删除临时文件
                    try:
                        os.unlink(list_path)
                    except Exception:
                        pass

                if result.returncode != 0:
                    error_msg = result.stderr or result.stdout or "未知错误"
                    raise Exception(f"ffmpeg 错误 (返回码 {result.returncode}):\n{error_msg}")

            # ★★ 把产物报进 `ui` —— 形状与内置 `SaveImage` / `SaveVideo` **逐字一致**
            #    （`SaveVideo` 的 mp4 也是放进 `images` 键的，种类由**文件后缀**判）。
            #    这是"画布能收到这个文件"的唯一途径：`history.outputs[<本节点 id>]` 里要有它。
            return {
                "ui": {
                    "images": [
                        {
                            "filename": output_filename,
                            "subfolder": subfolder,
                            "type": "output",
                        }
                    ]
                },
                # 0 号槽 = 路径字符串（老连线用的就是它），1 号槽 = VIDEO（画布勾这个出口）
                "result": (output_path, _as_video_output(output_path)),
            }

        except Exception as e:
            raise Exception(f"合并失败：{str(e)}")
