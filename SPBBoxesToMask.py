import torch


class SPBBoxesToMask:
    """把边界框（bbox）转成 ComfyUI 的 MASK 张量。

    相比 ComfyUI-llama-cpp 的 BBoxesToMask，本节点增强了两点：
    1. 兼容两种 bbox 坐标格式：
       - (x1, y1, x2, y2) 元组/列表 —— llama-cpp 的 json_to_bbox 输出格式（绝对像素、左上/右下角）
       - {"x", "y", "width", "height"} 字典 —— 系统 BoundingBox 格式（绝对像素、左上角+宽高）
    2. 多帧结构：bboxes 可为单帧或多帧嵌套列表，逐帧输出对应 mask。

    MASK 语义：框内区域值为 1（前景），其余为 0，支持 dilation 外扩与 feather 羽化。
    输出形状 (B, H, W)，尺寸取自 image。
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                # 用 ANY 通用类型：BBOX 类型在传统节点注册方式下无有效 socket，
                # 且上游可能是 llama-cpp 的元组格式或系统的字典格式，统一健壮解析。
                "bboxes": ("*", {"tooltip": "边界框数据。支持 (x1,y1,x2,y2) 元组/列表，或 {\"x\",\"y\",\"width\",\"height\"} 字典；可为单帧或多帧嵌套列表。"}),
                "image": ("IMAGE", {"tooltip": "用于确定 mask 尺寸 (H, W) 的参考图像。mask 与 image 逐帧对应。"}),
                "dilation": ("INT", {"default": 0, "min": 0, "max": 512, "step": 1, "tooltip": "边框向外扩张的像素数（负值不可用，需缩小请自行调整 bbox）。"}),
                "feather": ("INT", {"default": 0, "min": 0, "max": 256, "step": 1, "tooltip": "mask 边缘羽化强度（高斯模糊 sigma，像素）。0 表示硬边。"}),
            },
        }

    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("mask",)
    FUNCTION = "process"
    CATEGORY = "image/detection"
    DESCRIPTION = "把边界框转为 ComfyUI 的 MASK，兼容 (x1,y1,x2,y2) 与 {x,y,width,height} 两种坐标格式。"

    @staticmethod
    def _to_boxes(bboxes):
        """把任意 bboxes 结构解析为「每帧一个框列表」，每框统一为 (x1, y1, x2, y2) 的 int 元组。

        兼容：
        - 单框：(x1,y1,x2,y2) 或 {"x","y","width","height"}
        - 单帧：[框, 框, ...]
        - 多帧：[[框,...], [框,...], ...]
        """
        def normalize_one(b):
            # 字典格式：{x, y, width, height} -> (x1, y1, x2, y2)
            if isinstance(b, dict):
                x = int(b.get("x", 0))
                y = int(b.get("y", 0))
                w = int(b.get("width", 0))
                h = int(b.get("height", 0))
                return (x, y, x + w, y + h)
            # 元组/列表格式：(x1, y1, x2, y2) 或 [x1,y1,x2,y2]
            if isinstance(b, (list, tuple)) and len(b) >= 4:
                try:
                    x1, y1, x2, y2 = (int(v) for v in b[:4])
                except (TypeError, ValueError):
                    return None
                if x2 < x1:
                    x1, x2 = x2, x1
                if y2 < y1:
                    y1, y2 = y2, y1
                return (x1, y1, x2, y2)
            return None

        if bboxes is None:
            return [[]]

        # 单个框（dict 或 tuple/list）
        if isinstance(bboxes, dict):
            box = normalize_one(bboxes)
            return [[box] if box is not None else []]
        if isinstance(bboxes, (list, tuple)) and len(bboxes) >= 4 and not isinstance(bboxes[0], (list, tuple, dict)):
            box = normalize_one(bboxes)
            return [[box] if box is not None else []]

        if not isinstance(bboxes, list):
            return [[]]
        if not bboxes:
            return [[]]

        first = bboxes[0]
        # 单帧：直接是框列表
        if isinstance(first, (dict, tuple)) or (
            isinstance(first, list) and len(first) >= 4 and not isinstance(first[0], (list, tuple, dict))
        ):
            boxes = [normalize_one(b) for b in bboxes]
            return [[b for b in boxes if b is not None]]

        # 多帧
        frames = []
        for frame in bboxes:
            if isinstance(frame, (dict, tuple)):
                box = normalize_one(frame)
                frames.append([box] if box is not None else [])
            elif isinstance(frame, list):
                boxes = [normalize_one(b) for b in frame]
                frames.append([b for b in boxes if b is not None])
            else:
                frames.append([])
        return frames if frames else [[]]

    @staticmethod
    def _feather_mask(mask, sigma):
        """对 mask 做高斯羽化（sigma>0 时）。用 torch 实现，避免引入 scipy 依赖。"""
        if sigma <= 0:
            return mask
        # 高斯核半径取 3*sigma，向上取整为奇数
        radius = max(1, int(round(3 * sigma)))
        kernel_size = radius * 2 + 1
        # 一维高斯核
        x = torch.arange(kernel_size, dtype=torch.float32, device=mask.device) - radius
        g = torch.exp(-(x ** 2) / (2 * sigma * sigma))
        g = g / g.sum()
        g = g.view(1, 1, -1)
        # 分离卷积：先沿 W 再沿 H
        b, h, w = mask.shape
        m = mask.unsqueeze(1)  # (B,1,H,W)
        # pad
        pad = radius
        m = torch.nn.functional.pad(m, (pad, pad, pad, pad), mode="replicate")
        # 水平
        m = torch.nn.functional.conv2d(m, g.unsqueeze(0), padding=0)
        # 垂直
        m = torch.nn.functional.conv2d(m, g.unsqueeze(0).transpose(2, 3), padding=0)
        return m.squeeze(1)

    def process(self, bboxes, image, dilation=0, feather=0):
        frames = self._to_boxes(bboxes)
        B_frames = len(frames)

        B, H, W, _ = image.shape

        # 帧数对齐：单帧框广播到所有图，或按图帧数截断/补足
        if B_frames == 1 and B > 1:
            frames = frames * B
        elif B_frames != B:
            # 截断或复制最后一帧补足
            frames = frames[:B]
            while len(frames) < B:
                frames.append(frames[-1] if frames else [])

        masks = []
        for i in range(B):
            mask = torch.zeros((H, W), dtype=torch.float32, device=image.device)
            for (x1, y1, x2, y2) in frames[i]:
                # 扩张
                x1e = x1 - dilation
                y1e = y1 - dilation
                x2e = x2 + dilation
                y2e = y2 + dilation
                # 裁剪到图像范围
                cx1 = max(0, x1e)
                cy1 = max(0, y1e)
                cx2 = min(W, x2e)
                cy2 = min(H, y2e)
                if cx2 > cx1 and cy2 > cy1:
                    mask[cy1:cy2, cx1:cx2] = 1.0
            if feather > 0:
                mask = self._feather_mask(mask.unsqueeze(0), float(feather)).squeeze(0)
            masks.append(mask)

        out = torch.stack(masks, dim=0)
        return (out,)


# 注册节点（统一在 __init__.py 中引入）
# NODE_CLASS_MAPPINGS = {
#     "siping_SPBBoxesToMask": SPBBoxesToMask
# }
# NODE_DISPLAY_NAME_MAPPINGS = {
#     "siping_SPBBoxesToMask": "SP BBoxes To Mask"
# }
