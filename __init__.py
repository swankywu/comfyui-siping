# 导入两个节点文件中的类
from .LTXResolution import LTXResolution
from .ParseFilePath import ParseFilePath
from .GetImagePath import GetImagePathAndName
from .GetVideoFileName import GetVideoFileName
from .VideoMergeLossless import VideoMergeLossless
from .ImageWithExtraInfo import SPImageLoadWithMetadata 
from .ImageWithExtraInfo import SPImageSaveWithExtraMetadata
from .LoadPromptsFromDir import LoadPromptsFromDir
from .SPEmpty import SPEmpty
from .LanguageSelect import LanguageSelect
from .SPLoadLoRA import SPLoadLoRA

# 声明前端扩展目录：ComfyUI 0.3x+ 只有显式导出 WEB_DIRECTORY
# （或 pyproject.toml 的 [tool.comfy] web）才会把 web/ 下的 JS 注册给浏览器
WEB_DIRECTORY = "./web"


# 汇总所有节点的注册信息（关键）
NODE_CLASS_MAPPINGS = {
    # 格式："节点显示标识": 节点类名
    "siping_ParseFilePath": ParseFilePath,
    "siping_GetImagePathAndName": GetImagePathAndName,
    "siping_GetVideoFileName": GetVideoFileName,
    "siping_VideoMergeLossless": VideoMergeLossless,
    "siping_LTXResolution": LTXResolution,
    "siping_SPImageLoadWithMetadata": SPImageLoadWithMetadata,
    "siping_SPImageSaveWithExtraMetadata": SPImageSaveWithExtraMetadata,
    "siping_LoadPromptsFromDir": LoadPromptsFromDir,
    "siping_SPEmpty": SPEmpty,
    "siping_LanguageSelect": LanguageSelect,
    "siping_SPLoadLoRA": SPLoadLoRA,

}

# 汇总节点在界面上显示的名称
NODE_DISPLAY_NAME_MAPPINGS = {
    "siping_ParseFilePath": "Parse File Path (拆分路径)",
    "siping_GetImagePathAndName": "Get Image Path & Name",
    "siping_GetVideoFileName": "Get Video File Name",
    "siping_VideoMergeLossless": "VideoMerge Lossless",
    "siping_LTXResolution": "LTX Resolution",
    "siping_SPImageLoadWithMetadata": "Load Image SP",
    "siping_SPImageSaveWithExtraMetadata": "Save Image SP",
    "siping_LoadPromptsFromDir": "Load Prompts From Dir (按目录加载提示词)",
    "siping_SPEmpty": "SP Empty (透传占位)",
    "siping_LanguageSelect": "Language Select (语言选择)",
    "siping_SPLoadLoRA": "SP Load LoRA (带过滤)",
}

# 可选：声明这是一个ComfyUI自定义节点模块
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]