import json
import os
import numpy as np
from pathlib import Path
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import piexif
import folder_paths
from nodes import PreviewImage

from .core import CATEGORY, BOOLEAN, METADATA_RAW, logger
from .SPImageSaveWithExtraMetadata import buildMetadata

# 支持格式的扩展名映射（与 SPImageSaveWithExtraMetadata 保持一致）
_EXT_MAP = {
    "png": "png",
    "jpg": "jpg",
    "jpeg": "jpg",
    "webp": "webp",
}


class SPPreviewImage(PreviewImage):
    """SP Preview Image (按文件类型预览)

    基于官方 PreviewImage，增加 file_type（png / jpg / webp）与 quality 参数，
    预览区按所选格式显示缩略图，并将输入图像原样透传为 IMAGE 输出。

    与纯预览不同：当 with_workflow 开启时，会把 prompt / extra_pnginfo 写入
    文件元数据（png 走 PngInfo，jpg/webp 走 EXIF），确保下载到的文件里带信息。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "file_type": (["png", "jpg", "webp"], {"default": "png"}),
                "quality": ("INT", {"default": 100, "min": 1, "max": 100, "tooltip": "仅对 jpg / webp 生效"}),
                "with_workflow": BOOLEAN,
                "meta_key": ("STRING", {"default": "workflow"}),
            },
            "optional": {
                "metadata_extra": ("STRING", {
                    "multiline": True,
                    "default": json.dumps({
                        "Prompt": "提示词描述",
                        "Seed": "42",
                        "Style Name": "beitemian",
                    }, indent=2).replace("\\/", "/"),
                }),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    CATEGORY = CATEGORY.MAIN.value + CATEGORY.IMAGE.value
    RETURN_TYPES = ("IMAGE", "METADATA_RAW")
    RETURN_NAMES = ("image", "Metadata RAW")
    OUTPUT_NODE = True

    FUNCTION = "execute"

    def execute(self, images=None, file_type="png", quality=100,
                with_workflow=True, meta_key="workflow",
                metadata_extra=None, prompt=None, extra_pnginfo=None):
        if images is None:
            logger.debug("Source: Empty on SPPreviewImage")
            return {
                "ui": {"images": []},
                "result": (None, {}),
            }

        if with_workflow is True:
            extra_pnginfo_new = extra_pnginfo.copy() if extra_pnginfo is not None else None
            prompt_new = prompt.copy() if prompt is not None else None
        else:
            extra_pnginfo_new = None
            prompt_new = None

        if metadata_extra is not None and metadata_extra != 'undefined':
            try:
                metadata_extra = {meta_key: json.loads(metadata_extra)}
            except Exception as e:
                logger.error(f"Error parsing metadata_extra (it will send as string), error: {e}， metadata_extra: {metadata_extra}")
                metadata_extra = {meta_key: str(metadata_extra)}

            if isinstance(metadata_extra, dict):
                for k, v in metadata_extra.items():
                    if extra_pnginfo_new is None:
                        extra_pnginfo_new = {}
                    extra_pnginfo_new[k] = v

        saved = self.save_images(
            images,
            prompt_new,
            extra_pnginfo_new,
            file_type=file_type,
            quality=quality,
        )

        results = saved["ui"]["images"]

        # 回读第一张图的元数据（供 Metadata RAW 输出）
        metadata = {}
        if results:
            first = results[0]
            image_path = str(Path(self.output_dir).joinpath(first["subfolder"], first["filename"]))
            try:
                _, _, metadata = buildMetadata(image_path)
            except Exception as e:
                logger.debug(f"buildMetadata failed, skip metadata: {e}")

        return {
            "ui": {"images": results},
            "result": (images, metadata),
        }

    def save_images(self, images, prompt, extra_pnginfo,
                    file_type="png", quality=100):
        """按指定格式保存图片（供预览区显示），并尽可能嵌入元数据。

        - png: PngInfo 写入 prompt / extra_pnginfo（与官方 PreviewImage 一致）
        - jpg / webp: piexif 把 prompt 写入 EXIF 271(Make)，
          extra_pnginfo 合并 JSON 写入 EXIF 270(ImageDescription)
        """
        file_type = (file_type or "png").lower()
        ext = _EXT_MAP.get(file_type, "png")

        filename_prefix = "ComfyUI" + self.prefix_append
        full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(
            filename_prefix, self.output_dir, images[0].shape[1], images[0].shape[2]
        )

        results = list()
        for batch_number, image in enumerate(images):
            i = 255. * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            file = f"{filename}_{counter:05}_.{ext}"
            file_path = os.path.join(full_output_folder, file)

            if ext == "png":
                metadata = PngInfo()
                if prompt is not None:
                    metadata.add_text("prompt", json.dumps(prompt))
                if extra_pnginfo is not None:
                    for x in extra_pnginfo:
                        metadata.add_text(x, json.dumps(extra_pnginfo[x]))
                img.save(file_path, pnginfo=metadata, compress_level=4)
            else:
                # jpg / webp：通过 EXIF 嵌入元数据
                exif_bytes = self._build_exif_bytes(prompt, extra_pnginfo)
                img = img.convert("RGB")
                if exif_bytes:
                    img.save(file_path, format=("JPEG" if ext == "jpg" else "WEBP"),
                             quality=quality, exif=exif_bytes)
                else:
                    img.save(file_path, format=("JPEG" if ext == "jpg" else "WEBP"),
                             quality=quality)

            results.append({
                "filename": file,
                "subfolder": subfolder,
                "type": self.type,
            })
            counter += 1

        return {"ui": {"images": results}}

    @staticmethod
    def _build_exif_bytes(prompt, extra_pnginfo):
        """构造 EXIF 字节串；EXIF 单段有 ~64KB 限制，超限时逐级降级。"""
        exif_dict = {"0th": {}}
        if prompt is not None:
            exif_dict["0th"][piexif.ImageIFD.Make] = json.dumps(prompt, ensure_ascii=True)
        if extra_pnginfo is not None:
            exif_dict["0th"][piexif.ImageIFD.ImageDescription] = json.dumps(extra_pnginfo, ensure_ascii=True)

        try:
            return piexif.dump(exif_dict)
        except Exception as e:
            logger.warn(f"EXIF dump failed (metadata too large?), falling back to prompt-only: {e}")
            try:
                exif_dict = {"0th": {}}
                if prompt is not None:
                    exif_dict["0th"][piexif.ImageIFD.Make] = json.dumps(prompt, ensure_ascii=True)
                return piexif.dump(exif_dict)
            except Exception as e2:
                logger.warn(f"EXIF dump failed even prompt-only, image will be saved without metadata: {e2}")
                return b""
