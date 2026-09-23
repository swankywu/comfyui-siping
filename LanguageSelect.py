class LanguageSelect:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "language": (["中文", "英文", "日语", "法语", "德语", "西班牙语", "葡萄牙语"], {
                    "default": "中文"
                }),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("language",)
    FUNCTION = "select_language"
    CATEGORY = "utils/选择"

    def select_language(self, language):
        return (language,)
