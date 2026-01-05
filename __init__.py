# 导入两个节点文件中的类
from .ParseFilePath import ParseFilePath
from .GetImagePath import GetImagePathAndName

# 汇总所有节点的注册信息（关键）
NODE_CLASS_MAPPINGS = {
    # 格式："节点显示标识": 节点类名
    "siping_ParseFilePath": ParseFilePath,
    "siping_GetImagePathAndName": GetImagePathAndName
}

# 汇总节点在界面上显示的名称
NODE_DISPLAY_NAME_MAPPINGS = {
    "siping_ParseFilePath": "Parse File Path (拆分路径)",
    "siping_GetImagePathAndName": "Get Image Path & Name"
}

# 可选：声明这是一个ComfyUI自定义节点模块
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]