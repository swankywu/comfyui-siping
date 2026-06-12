class LTXResolution:
    """
    通过下拉选项选择分辨率，输出对应的宽度和高度整型值。
    """

    RESOLUTION_OPTIONS = [
        "1280x736",
        "1536x832",
        "1920x1088",
        "2048x1152",
        "2560x1440",
    ]

    RESOLUTION_MAP = {
        "1280x736": (1280, 736),
        "1536x832": (1536, 832),
        "1920x1088": (1920, 1088),
        "2048x1152": (2048, 1152),
        "2560x1440": (2560, 1440),
    }

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "resolution": (cls.RESOLUTION_OPTIONS, {"default": "1280x736"}),
            }
        }

    RETURN_TYPES = ("INT", "INT")
    RETURN_NAMES = ("width", "height")
    FUNCTION = "get_resolution"
    CATEGORY = "utils/视频工具"

    def get_resolution(self, resolution):
        width, height = self.RESOLUTION_MAP[resolution]
        return (width, height)
