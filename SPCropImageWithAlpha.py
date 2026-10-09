import torch
import comfy.utils


class SPCropImageWithAlpha:
    """按 x / y / width / height 裁剪图片，并保留 alpha 通道。

    为什么需要 mask 输入：
    ComfyUI 的 LoadImage 对带透明度的图，会把 alpha 拆出来单独输出到 MASK 口
    （MASK = 1 - alpha），IMAGE 口只给 3 通道 RGB。所以光接 IMAGE 是拿不到
    alpha 的——必须把 LoadImage 的 MASK 也接进来。

    接了 mask 时：mask 与图像按同一区域裁剪，然后 alpha = 1 - mask 拼回第 4 通道，
    输出真正的 RGBA（下游 PreviewImage / SaveImage 保存 PNG 时透明区域才会保留）。
    没接 mask 时：只在 IMAGE 内部裁剪，若 IMAGE 本身已是 4 通道则原样保留。

    对齐官方 JoinImageWithAlpha 的约定：MASK 口的值越大 = 被遮罩 = 越透明。
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
                "x": ("INT", {"default": 0, "min": 0, "max": 1024 * 1024, "step": 1}),
                "y": ("INT", {"default": 0, "min": 0, "max": 1024 * 1024, "step": 1}),
                "width": ("INT", {"default": 512, "min": 1, "max": 1024 * 1024, "step": 1}),
                "height": ("INT", {"default": 512, "min": 1, "max": 1024 * 1024, "step": 1}),
            },
            "optional": {
                "mask": ("MASK",),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "crop"
    CATEGORY = "image/transform"
    DESCRIPTION = "Crop an image by x/y/width/height and keep transparency. Connect LoadImage's MASK to preserve the alpha channel."

    def crop(self, image, x, y, width, height, mask=None):
        if not isinstance(image, torch.Tensor):
            image = torch.as_tensor(image)

        img_h, img_w = image.shape[1], image.shape[2]

        # 与系统 ImageCrop 行为一致：起点最多到最后一列/行
        x = max(0, min(int(x), img_w - 1))
        y = max(0, min(int(y), img_h - 1))
        width = max(1, min(int(width), img_w - x))
        height = max(1, min(int(height), img_h - y))

        # 保留全部通道（含 alpha），不写死 :3
        out = image[:, y:y + height, x:x + width, :]

        if mask is not None:
            if not isinstance(mask, torch.Tensor):
                mask = torch.as_tensor(mask)
            # 单张 mask 广播到整个 batch
            if mask.shape[0] == 1 and out.shape[0] > 1:
                mask = mask.expand(out.shape[0], -1, -1)
            elif mask.shape[0] != out.shape[0]:
                mask = comfy.utils.repeat_to_batch_size(mask, out.shape[0])

            # mask 尺寸和原图不一致时先对齐到原图尺寸再裁剪
            # （LoadImage 遇到无 alpha 的图会吐一个 64x64 的空 mask）
            if tuple(mask.shape[-2:]) != (img_h, img_w):
                resized = comfy.utils.common_upscale(
                    mask.reshape((-1, 1, mask.shape[-2], mask.shape[-1])).float(),
                    img_w, img_h, "bilinear", "disabled",
                )
                mask = resized.reshape((-1, img_h, img_w))

            mask = mask[:, y:y + height, x:x + width].to(out.dtype)
            # ComfyUI 约定：MASK = 1 - alpha，所以取反还原成不透明度
            alpha = (1.0 - mask).unsqueeze(-1)
            # 用 mask 还原的 alpha 覆盖，保证透明度正确
            out = torch.cat((out[..., :3], alpha), dim=-1)

        return (out,)


# 注册节点（保留模板风格，方便后续在 __init__.py 中统一引入）
# NODE_CLASS_MAPPINGS = {
#     "siping_SPCropImageWithAlpha": SPCropImageWithAlpha
# }
# NODE_DISPLAY_NAME_MAPPINGS = {
#     "siping_SPCropImageWithAlpha": "SP Crop Image With Alpha"
# }