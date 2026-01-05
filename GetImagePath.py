import os
import json
from PIL import Image

class GetImagePathAndName:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),  # 接收图片数据
                "save_dir": ("STRING", {"default": "./output"}),  # 默认保存目录
            },
            "optional": {
                "prefix": ("STRING", {"default": "ComfyUI_"}),  # 文件名前缀
            }
        }
    
    RETURN_TYPES = ("STRING", "STRING", "STRING")  # 完整路径、目录、文件名
    RETURN_NAMES = ("full_path", "directory", "filename")
    FUNCTION = "get_path"
    CATEGORY = "utils/path"

    def get_path(self, image, save_dir, prefix="ComfyUI_"):
        # 确保保存目录存在
        os.makedirs(save_dir, exist_ok=True)
        
        # 生成唯一文件名（基于时间戳）
        import time
        timestamp = str(int(time.time() * 1000))
        filename = f"{prefix}{timestamp}.png"
        
        # 拼接完整路径
        full_path = os.path.join(save_dir, filename)
        
        # 返回结果
        return (full_path, save_dir, filename)

# 注册节点
# NODE_CLASS_MAPPINGS = {
#     "siping_GetImagePathAndName": GetImagePathAndName
# }
# NODE_DISPLAY_NAME_MAPPINGS = {
#     "siping_GetImagePathAndName": "Get Image Path & Name"
# }