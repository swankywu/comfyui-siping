import json
import os
import sys

import torch
from PIL import Image, ImageDraw, ImageFont
from torchvision.transforms import ToPILImage, ToTensor


# ---------------------------------------------------------------------------
# 中文字体查找
# ---------------------------------------------------------------------------
# 按平台收集常见 CJK 字体的候选路径（按优先级排列），
# 找不到时回退到 ImageFont.load_default()（不支持中文，但保证不崩溃）。
def _candidate_font_paths():
    paths = []
    if sys.platform.startswith("win"):
        win = os.environ.get("WINDIR", r"C:\Windows")
        fonts = os.path.join(win, "Fonts")
        paths += [
            os.path.join(fonts, "msyh.ttc"),      # 微软雅黑
            os.path.join(fonts, "msyhbd.ttc"),
            os.path.join(fonts, "simhei.ttf"),    # 黑体
            os.path.join(fonts, "simsun.ttc"),    # 宋体
            os.path.join(fonts, "simkai.ttf"),    # 楷体
            os.path.join(fonts, "msyh.ttf"),
        ]
    elif sys.platform == "darwin":
        paths += [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
    else:
        paths += [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
        ]
    # 只保留真实存在的文件
    return [p for p in paths if os.path.isfile(p)]


_CANDIDATE_FONTS = _candidate_font_paths()


def _load_font(size):
    """优先加载系统 CJK 字体（支持中文），失败则退回默认位图字体。

    ImageFont.load_default() 在新版 Pillow 中不接受 size 参数，
    这里做了兼容：能传 size 就传，报错则用默认值。
    """
    for path in _CANDIDATE_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# 文本折行工具
# ---------------------------------------------------------------------------
def _wrap_text(draw, text, font, max_width, max_lines):
    """在给定像素宽度内折行，最多 max_lines 行，超出截断并以省略号结尾。

    - 中文（CJK）逐字折行；英文/数字按空白切词折行。
    - 通过 draw.textlength() 实测宽度，避免估算误差。
    - 返回行列表（保证非空，至少一行）。
    """
    if not text:
        return [""]

    # 逐段处理（保留用户显式换行）
    paragraphs = text.split("\n")
    lines = []
    for para in paragraphs:
        if para == "":
            lines.append("")
            continue
        # CJK 字符单独成 token；连续的英文/数字/空白按词切分，
        # 并用 token 的 is_sep 标记来保留词间空格。
        tokens = []  # (text, is_sep)
        buf = ""
        for ch in para:
            if _is_cjk(ch):
                if buf:
                    tokens.append((buf, False))
                    buf = ""
                tokens.append((ch, False))
            elif ch == " ":
                if buf:
                    tokens.append((buf, False))
                    buf = ""
                tokens.append((" ", True))
            else:
                buf += ch
        if buf:
            tokens.append((buf, False))

        line = ""
        for tok, is_sep in tokens:
            candidate = line + tok
            if line and not is_sep and draw.textlength(candidate, font=font) > max_width:
                lines.append(line)
                line = tok
            else:
                line = candidate
        if line:
            lines.append(line.rstrip())

    # 截断：保留前 max_lines 行，最后一行加省略号
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        # 反复去掉末尾字符直到能放下省略号
        while last and draw.textlength(last + "…", font=font) > max_width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


def _is_cjk(ch):
    """判断单个字符是否为中日韩文字（需要逐字折行的字符）。"""
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF      # CJK 统一表意文字
        or 0x3400 <= code <= 0x4DBF   # CJK 扩展 A
        or 0x3000 <= code <= 0x303F   # CJK 标点
        or 0xFF00 <= code <= 0xFFEF   # 全角字符
        or 0x3040 <= code <= 0x30FF   # 日文假名
        or 0xAC00 <= code <= 0xD7AF   # 韩文
    )


# ---------------------------------------------------------------------------
# 节点定义
# ---------------------------------------------------------------------------
class SPDrawBBoxes:
    """在图像上绘制带标签的边界框（bbox），支持中文与 box 内自动换行。

    相比 ComfyUI 原生的 Draw BBoxes：
    1. 支持中文标签 —— 自动加载系统 CJK 字体（微软雅黑 / 苹方 / Noto 等），
       不再出现中文显示成方框（豆腐块）的问题。
    2. 支持 box 内自动换行 —— 文本按实际像素宽度折行，可限制最大行数并截断，
       长标签不再溢出边界框。

    输入的 bboxes 兼容原生格式：每个框为
    {"x", "y", "width", "height", "label", "score"} 的字典，或字典组成的列表。
    文本取自 label（可选带 score），未提供 image 时按框的最大范围生成黑底画布。
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                # 注意：不能用 ("BBOX",)。BBOX 类型只在新的 io.ComfyNode API（comfy_api 层）里存在，
                # 传统 class + NODE_CLASS_MAPPINGS 走的是旧类型系统，核心执行器里根本没有注册 BBOX，
                # 前端不会生成对应 socket，导致上游检测节点的框喂不进来。
                # 这里用 ("*",)（ANY）通用类型，让任意上游输出都能接入，再在 draw() 里健壮解析。
                "bboxes": ("*", {"tooltip": "边界框数据。支持 dict / [dict] / [[dict]] 或 JSON 字符串，每框为 {\"x\",\"y\",\"width\",\"height\",\"label\",\"score\"}。可接检测节点（如 RT-DETR）输出。"}),
                "font_size": ("INT", {"default": 20, "min": 6, "max": 256, "step": 1, "tooltip": "标签文字的字号（像素）。文字会按此大小渲染，行高也随字号自动调整。"}),
                "box_width": ("INT", {"default": 3, "min": 1, "max": 20, "step": 1, "tooltip": "边框线条粗细（像素）。"}),
                "text_padding": ("INT", {"default": 4, "min": 0, "max": 64, "step": 1, "tooltip": "文字与边框内侧的间距（像素）。越大文字离框边越远，可用于防止文字压到框线。"}),
                "line_spacing": ("INT", {"default": 4, "min": 0, "max": 64, "step": 1, "tooltip": "多行文字之间的额外行距（像素）。"}),
                "max_lines": ("INT", {"default": 3, "min": 1, "max": 20, "step": 1, "tooltip": "在框内最多显示的文字行数。超出部分截断，末尾加省略号。"}),
                "wrap_text": ("BOOLEAN", {"default": True, "tooltip": "是否在框内自动换行。开启时按框宽折行；关闭时文字单行显示、超出部分可能溢出框外。"}),
                "show_score": ("BOOLEAN", {"default": True, "tooltip": "是否在标签后显示置信度分数（如 0.93）。仅当框数据含 score 字段时生效。"}),
                "color_mode": (["single", "by_label"], {"default": "by_label", "tooltip": "配色方式。single=所有框统一用下方 single_color 颜色；by_label=按标签类别自动分配不同颜色（同类同色、异类异色，对齐原生 Draw BBoxes）。"}),
                "single_color": (["red", "green", "blue", "orange", "purple", "cyan", "pink", "yellow", "white"], {"default": "red", "tooltip": "边框和文字的颜色。仅在 color_mode 为 single（统一色）时生效；by_label 模式下颜色按标签自动分配。"}),
            },
            "optional": {
                "image": ("IMAGE", {"tooltip": "要叠加边框的底图。不接时按所有框的最大范围生成黑色画布。"}),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("out_image",)
    FUNCTION = "draw"
    CATEGORY = "image/detection"
    DESCRIPTION = "在图像上绘制边界框，支持中文标签与框内自动换行。"

    _COLORS = {
        "red": (255, 0, 0),
        "green": (0, 200, 0),
        "blue": (0, 0, 255),
        "orange": (255, 165, 0),
        "purple": (128, 0, 128),
        "cyan": (0, 255, 255),
        "pink": (255, 20, 147),
        "yellow": (255, 255, 0),
        "white": (255, 255, 255),
    }

    # by_label 配色用的固定色板，与原生 DrawBBoxes 保持一致（8 色循环）
    _PALETTE = [
        (255, 0, 0),      # red
        (0, 200, 0),      # green
        (0, 0, 255),      # blue
        (255, 165, 0),    # orange
        (128, 0, 128),    # purple
        (0, 255, 255),    # cyan
        (255, 20, 147),   # pink
        (100, 149, 237),  # cornflower blue
    ]

    @staticmethod
    def _normalize_frames(bboxes):
        """把任意 bboxes 结构规整为「每帧一个框列表」的嵌套列表 [[框, ...], ...]。

        兼容的输入形态：
        - None / 空          -> [[]]
        - dict               -> [[dict]]
        - [dict, ...]        -> [[dict, ...]]
        - [[dict,...], ...]  -> 原样（多帧）
        返回值里每个「框」保证是 dict。
        """
        if bboxes is None:
            return [[]]
        if isinstance(bboxes, dict):
            return [[bboxes]]
        if not isinstance(bboxes, list):
            return [[]]

        # 空列表
        if not bboxes:
            return [[]]

        first = bboxes[0]
        # 单帧：直接是 dict 列表
        if isinstance(first, dict):
            return [[d for d in bboxes if isinstance(d, dict)]]
        # 多帧：每帧应是一个列表
        frames = []
        for frame in bboxes:
            if isinstance(frame, dict):
                frames.append([frame])
            elif isinstance(frame, list):
                frames.append([d for d in frame if isinstance(d, dict)])
            else:
                frames.append([])
        return frames if frames else [[]]

    def draw(self, bboxes, font_size, box_width, text_padding, line_spacing,
             max_lines, wrap_text, show_score, color_mode="by_label", single_color="red", image=None):
        # 统一成「每帧一个列表」的结构，与原生 DrawBBoxes 的 bboxes_per_frame 一致。
        # bboxes 输入类型为 ANY，需健壮解析各种上游格式：
        #   1) dict          -> 单个框
        #   2) [dict,...]    -> 单帧多个框
        #   3) [[dict,...],...] -> 多帧
        #   4) JSON 字符串    -> 反序列化后按上述处理
        if isinstance(bboxes, str):
            bboxes = bboxes.strip()
            if bboxes:
                try:
                    bboxes = json.loads(bboxes)
                except (ValueError, TypeError):
                    bboxes = []

        frames = self._normalize_frames(bboxes)

        B = len(frames)

        # 未提供 image：按所有框的最大范围生成黑底画布（与原生行为一致）
        if image is None:
            max_w = max((int(d["x"] + d["width"]) for frame in frames for d in frame if isinstance(d, dict)), default=640)
            max_h = max((int(d["y"] + d["height"]) for frame in frames for d in frame if isinstance(d, dict)), default=640)
            image = torch.zeros((B, max_h, max_w, 3), dtype=torch.float32)
        elif image.shape[0] == 1 and B > 1:
            # 单张图 + 多帧框：广播到每帧
            image = image.repeat(B, 1, 1, 1)
        elif image.shape[0] != B:
            # 帧数不匹配：让每帧尽量对齐（不足则复制最后一张，超出则截断）
            image = image[:B]
            if image.shape[0] < B:
                image = torch.cat([image, image[-1:].repeat(B - image.shape[0], 1, 1, 1)], dim=0)

        outline = self._COLORS.get(single_color, (255, 0, 0))
        font = _load_font(font_size)

        # by_label 模式：按标签类别首次出现的顺序分配固定色板，同类同色、异类异色
        label_colors = {}
        if color_mode == "by_label":
            order = []
            for frame in frames:
                for d in frame:
                    if isinstance(d, dict):
                        lbl = d.get("label")
                        lbl = "" if lbl is None else str(lbl)
                        if lbl and lbl not in order:
                            order.append(lbl)
            for idx, lbl in enumerate(order):
                label_colors[lbl] = self._PALETTE[idx % len(self._PALETTE)]

        all_out = []
        for i in range(B):
            frame = frames[i]
            pil = ToPILImage()(image[i].movedim(-1, 0)).convert("RGB")
            draw_ctx = ImageDraw.Draw(pil)

            for d in frame:
                if not isinstance(d, dict):
                    continue
                x1 = int(d.get("x", 0))
                y1 = int(d.get("y", 0))
                x2 = int(x1 + d.get("width", 0))
                y2 = int(y1 + d.get("height", 0))
                if x2 < x1:
                    x1, x2 = x2, x1
                if y2 < y1:
                    y1, y2 = y2, y1

                # 组装标签文本（先取原始 label，用于确定配色，再拼 score）
                raw_label = d.get("label")
                raw_label = "" if raw_label is None else str(raw_label)
                score = d.get("score")
                label = raw_label
                if show_score and score is not None:
                    try:
                        label = f"{raw_label} {float(score):.2f}".strip()
                    except (TypeError, ValueError):
                        pass
                label = label.strip()

                # 确定本框颜色：by_label 按标签取色，single 统一用 outline
                c = label_colors.get(raw_label, outline) if color_mode == "by_label" else outline

                draw_ctx.rectangle([x1, y1, x2, y2], outline=c, width=box_width)

                if not label:
                    continue

                box_w = x2 - x1
                box_h = y2 - y1
                inner_w = box_w - 2 * text_padding
                inner_h = box_h - 2 * text_padding
                if inner_w <= 0 or inner_h <= 0:
                    continue

                # 折行
                if wrap_text:
                    lines = _wrap_text(draw_ctx, label, font, inner_w, max_lines)
                else:
                    lines = [label]

                # 逐行绘制，超出 box 底部的行不再画
                line_h = font_size + line_spacing
                ty = y1 + text_padding
                for line in lines:
                    if ty + font_size > y2 - text_padding:
                        break
                    draw_ctx.text((x1 + text_padding, ty), line, fill=c, font=font)
                    ty += line_h

            # 转回 ComfyUI IMAGE 张量 (B, H, W, C)
            tensor = ToTensor()(pil).movedim(0, -1).unsqueeze(0)
            all_out.append(tensor)

        out = torch.cat(all_out, dim=0)
        return (out,)


# 注册节点（统一在 __init__.py 中引入）
# NODE_CLASS_MAPPINGS = {
#     "siping_SPDrawBBoxes": SPDrawBBoxes
# }
# NODE_DISPLAY_NAME_MAPPINGS = {
#     "siping_SPDrawBBoxes": "SP Draw BBoxes (中文/换行)"
# }
