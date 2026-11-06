import torch


class SPImageFlip:
    """按 mode 翻转图片。

    - none：原样输出
    - horizontal：左右镜像（沿垂直轴翻转，水平翻转）
    - vertical：上下颠倒（沿水平轴翻转，垂直翻转）

    ComfyUI 的 IMAGE 张量形状为 (B, H, W, C)，其中：
    - 水平翻转 = 沿 W 轴翻转，即 flip(dims=[2])
    - 垂直翻转 = 沿 H 轴翻转，即 flip(dims=[1])
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
                "flip": (["none", "horizontal", "vertical"],),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "flip"
    CATEGORY = "image/transform"
    DESCRIPTION = "Flip an image. flip: none (no change), horizontal (mirror left-right), vertical (flip up-down)."

    def flip(self, image, flip="none"):
        if not isinstance(image, torch.Tensor):
            image = torch.as_tensor(image)

        if flip == "none":
            return (image,)
        if flip == "horizontal":
            return (torch.flip(image, dims=[2]),)
        if flip == "vertical":
            return (torch.flip(image, dims=[1]),)

        # 未知模式兜底：原样返回
        return (image,)
