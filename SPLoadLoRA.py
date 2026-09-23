import folder_paths
import comfy.utils
import comfy.sd


class SPLoadLoRA:
    """对齐系统 Load LoRA（Model Only，无 CLIP 输入），新增 filter_key 单行过滤输入框。

    - filter_key 为空：下拉显示全部 lora，按 lora_name 正常加载（与原版一致）。
    - filter_key 非空：
        * 前端（web/SPLoadLoRA.js）把 lora_name 下拉过滤为名称包含关键词的项；
        * 后端 load_lora 同步校验，只加载匹配的 lora（双保险）。

    加载逻辑对齐 ComfyUI 0.37 的 LoraLoader：先用 load_torch_file 读成
    带 metadata 的 dict，再交给 comfy.sd.load_lora_for_models。
    """

    def __init__(self):
        self.loaded_lora = None

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": ("MODEL",),
                "lora_name": (folder_paths.get_filename_list("loras"),),
                "filter_key": (
                    "STRING",
                    {
                        "multiline": False,
                        "default": "",
                        "placeholder": "过滤关键词，留空显示全部",
                    },
                ),
                "strength_model": (
                    "FLOAT",
                    {"default": 1.0, "min": -100.0, "max": 100.0, "step": 0.01},
                ),
            }
        }

    RETURN_TYPES = ("MODEL",)
    RETURN_NAMES = ("MODEL",)
    FUNCTION = "load_lora"
    CATEGORY = "loaders"

    def load_lora(self, model, lora_name, filter_key, strength_model):
        # —— filter_key 后端过滤/校验 ——
        key = (filter_key or "").strip().lower()
        if key:
            all_loras = folder_paths.get_filename_list("loras")
            matches = [n for n in all_loras if key in n.lower()]
            if lora_name not in matches:
                if len(matches) == 1:
                    lora_name = matches[0]
                elif not matches:
                    raise ValueError(
                        f"[SPLoadLoRA] filter_key='{filter_key}' 未匹配到任何 lora，请检查关键词。"
                    )
                else:
                    preview = ", ".join(matches[:20])
                    more = "" if len(matches) <= 20 else f" 等共 {len(matches)} 个"
                    raise ValueError(
                        f"[SPLoadLoRA] filter_key='{filter_key}' 匹配到多个 lora，"
                        f"请在下拉框选定其一或细化关键词。候选：{preview}{more}"
                    )

        # strength 为 0 直接返回原模型（与原版一致）
        if strength_model == 0:
            return (model,)

        # —— 对齐 ComfyUI 0.37 的 LoraLoader 加载路径 ——
        lora_path = folder_paths.get_full_path_or_raise("loras", lora_name)
        lora = None
        lora_metadata = None
        if self.loaded_lora is not None:
            if self.loaded_lora[0] == lora_path:
                lora = self.loaded_lora[1]
                lora_metadata = self.loaded_lora[2] if len(self.loaded_lora) > 2 else None
            else:
                self.loaded_lora = None
        if lora is None:
            lora, lora_metadata = comfy.utils.load_torch_file(
                lora_path, safe_load=True, return_metadata=True
            )
            self.loaded_lora = (lora_path, lora, lora_metadata)

        model_lora, _ = comfy.sd.load_lora_for_models(
            model, None, lora, strength_model, 0, lora_metadata=lora_metadata
        )
        return (model_lora,)
