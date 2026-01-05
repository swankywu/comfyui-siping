import os

class ParseFilePath:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "file_path": ("STRING", {
                    "default": "./output/ComfyUI_123456.png",
                    "multiline": False
                }),  # 输入完整文件路径
            }
        }
    
    # 返回类型：目录、纯文件名（无扩展名）、完整文件名、扩展名
    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("directory", "filename_no_ext", "filename_full", "extension")
    FUNCTION = "parse_path"
    CATEGORY = "utils/path"

    def parse_path(self, file_path):
        # 处理路径，兼容不同操作系统（Windows/Linux/Mac）
        file_path = os.path.normpath(file_path)
        
        # 1. 获取目录部分
        directory = os.path.dirname(file_path)
        
        # 2. 获取完整文件名（带扩展名）
        filename_full = os.path.basename(file_path)
        
        # 3. 拆分文件名和扩展名
        filename_no_ext, extension = os.path.splitext(filename_full)
        
        # 移除扩展名前的点（如 .png → png）
        extension = extension.lstrip('.')
        
        return (directory, filename_no_ext, filename_full, extension)

# 注册节点
# NODE_CLASS_MAPPINGS = {
#     "siping_ParseFilePath": ParseFilePath
# }
# NODE_DISPLAY_NAME_MAPPINGS = {
#     "siping_ParseFilePath": "Parse File Path (拆分路径)"
# }