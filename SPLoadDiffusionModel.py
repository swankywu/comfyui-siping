import folder_paths
import comfy.sd


class SPLoadDiffusionModel:
    """对齐系统 Load Diffusion Model（UNETLoader），新增 filter_key 单行过滤输入框。

    - filter_key 为空：下拉显示全部 diffusion model，按 unet_name 正常加载（与原版一致）。
    - filter_key 非空：
        * 前端（web/SPLoadDiffusionModel.js）把 unet_name 下拉过滤为名称包含关键词的项；
        * 后端 load_model 同步校验，只加载匹配的 diffusion model（双保险）。

    加载逻辑对齐 ComfyUI 0.37 的 UNETLoader：用 comfy.sd.load_diffusion_model 读取。
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "unet_name": (folder_paths.get_filename_list("diffusion_models"),),
                "filter_key": (
                    "STRING",
                    {
                        "multiline": False,
                        "default": "",
                        "placeholder": "过滤关键词，留空显示全部",
                    },
                ),
                "weight_dtype": (
                    ["default", "fp8_e4m3fn", "fp8_e4m3fn_fast", "fp8_e5m2"],
                    {"advanced": True},
                ),
            }
        }

    RETURN_TYPES = ("MODEL",)
    RETURN_NAMES = ("MODEL",)
    FUNCTION = "load_model"
    CATEGORY = "model/loaders"

    def load_model(self, unet_name, filter_key, weight_dtype):
        # —— filter_key 后端过滤/校验 ——
        key = (filter_key or "").strip().lower()
        if key:
            all_models = folder_paths.get_filename_list("diffusion_models")
            matches = [n for n in all_models if key in n.lower()]
            if unet_name not in matches:
                if len(matches) == 1:
                    unet_name = matches[0]
                elif not matches:
                    raise ValueError(
                        f"[SPLoadDiffusionModel] filter_key='{filter_key}' 未匹配到任何 "
                        f"diffusion model，请检查关键词。"
                    )
                else:
                    preview = ", ".join(matches[:20])
                    more = "" if len(matches) <= 20 else f" 等共 {len(matches)} 个"
                    raise ValueError(
                        f"[SPLoadDiffusionModel] filter_key='{filter_key}' 匹配到多个 "
                        f"diffusion model，请在下拉框选定其一或细化关键词。候选：{preview}{more}"
                    )

        # —— 对齐 ComfyUI 0.37 的 UNETLoader 加载路径 ——
        import torch

        model_options = {}
        if weight_dtype == "fp8_e4m3fn":
            model_options["dtype"] = torch.float8_e4m3fn
        elif weight_dtype == "fp8_e4m3fn_fast":
            model_options["dtype"] = torch.float8_e4m3fn
            model_options["fp8_optimizations"] = True
        elif weight_dtype == "fp8_e5m2":
            model_options["dtype"] = torch.float8_e5m2

        unet_path = folder_paths.get_full_path_or_raise("diffusion_models", unet_name)
        model = comfy.sd.load_diffusion_model(unet_path, model_options=model_options)
        return (model,)
