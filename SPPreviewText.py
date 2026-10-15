class SPPreviewText:
    """
    SPPreviewText - 文本预览节点 (Text Preview)
    接受任意类型输入（字符串 / 数字 / 图像张量 / 列表 / 字典等），
    将其转换为可读的文本进行预览，并作为输出节点直接输出文本。
    由于输出类型为 STRING，ComfyUI 不会对结果做任何文件保存。
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                # "*" 表示接受任意类型连接，仅用于预览，不会被修改
                "any": ("*",),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "preview"
    CATEGORY = "utils/preview"
    OUTPUT_NODE = True  # 标记为输出节点

    @staticmethod
    def _to_text(value):
        """将任意输入转换为可读文本"""
        if value is None:
            return "None"
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)

        # torch 张量（IMAGE / LATENT / MASK 等），仅显示形状与类型，避免超长输出
        try:
            import torch
            if isinstance(value, torch.Tensor):
                return "Tensor(shape={}, dtype={})".format(tuple(value.shape), value.dtype)
        except ImportError:
            pass

        # 字典：转 JSON，失败则回退 str
        if isinstance(value, dict):
            try:
                import json
                return json.dumps(value, ensure_ascii=False, default=str)
            except Exception:
                return str(value)

        # 列表 / 元组及其余类型
        return str(value)

    def preview(self, any=None):
        text = self._to_text(any)
        # 与官方 PreviewAny 一致：前端 onExecuted(message) 收到的 message
        # 只来自返回值的 "ui" 字段，直接返回元组会导致预览区拿不到内容。
        return {"ui": {"text": (text,)}, "result": (text,)}

# 注册节点（统一在 __init__.py 中引入）
# NODE_CLASS_MAPPINGS = {
#     "siping_SPPreviewText": SPPreviewText
# }
# NODE_DISPLAY_NAME_MAPPINGS = {
#     "siping_SPPreviewText": "SP Preview Text (文本预览)"
# }
