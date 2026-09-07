class SPEmpty:
    """
    SPEmpty - 透传节点 (Passthrough / Empty)
    接受任意输入（仅用于在图上"占位 / 连接"），输出用户自定义的字符串（默认 "empty"）。
    常用于：在不需要数据流的分支末端输出一段固定文本，或者干脆什么都不传。
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                # 任意输入：ComfyUI 中 "*" 表示接受任意类型，仅用于占位连接，实际取值会被忽略
                "any": ("*",),
            },
            "optional": {
                # 自定义输出字符串，默认为 "empty"
                "output_text": ("STRING", {
                    "default": "empty",
                    "multiline": False,
                }),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "passthrough"
    CATEGORY = "utils/empty"
    OUTPUT_NODE = True

    def passthrough(self, any=None, output_text="empty"):
        # 忽略任意输入，直接返回用户自定义字符串（默认 "empty"）
        return (output_text,)

# 注册节点（保留模板风格，方便后续在 __init__.py 中统一引入）
# NODE_CLASS_MAPPINGS = {
#     "siping_SPEmpty": SPEmpty
# }
# NODE_DISPLAY_NAME_MAPPINGS = {
#     "siping_SPEmpty": "SP Empty (透传占位)"
# }
