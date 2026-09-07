import os
import re


class LoadPromptsFromDir:
    """从磁盘目录加载提示词列表。

    目录下每个 ``.md`` 文件按以下规则抽取提示词：
      - ``is_chinese=True``  → 抓取 ``## 中文版提示词（...）`` section 后的第一个 fenced code block
      - ``is_chinese=False`` → 抓取 ``## H3 Prompt（English）`` section 后的第一个 fenced code block

    秒数从文件名尾部 ``_Ns.md`` 解析（如 ``EP01_S01_cold-open_15s.md`` → 15）。
    找不到目标 section 的文件会被跳过，避免空字符串污染下游。
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "directory": ("STRING", {
                    "default": "",
                    "multiline": False,
                }),
                "is_chinese": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ("LIST", "LIST", "LIST")
    RETURN_NAMES = ("prompts", "filenames", "seconds")
    FUNCTION = "load_prompts"
    CATEGORY = "utils/path"

    _SECTION_ZH_RE = re.compile(r'^##\s*中文版提示词.*$', re.MULTILINE)
    _SECTION_H3_RE = re.compile(r'^##\s*H3\s*Prompt.*$', re.MULTILINE | re.IGNORECASE)
    _FENCE_RE = re.compile(r'```([^\n`]*)\n(.*?)```', re.DOTALL)
    _SECONDS_RE = re.compile(r'_(\d+)s\.md$', re.IGNORECASE)

    @classmethod
    def IS_CHANGED(s, directory, is_chinese):
        # 目录 mtime 或语言切换时强制重跑
        mtime = 0
        if directory and os.path.isdir(directory):
            try:
                mtime = os.path.getmtime(directory)
            except OSError:
                mtime = 0
        return f"{directory}::{bool(is_chinese)}::{mtime}"

    def _extract_seconds(self, filename: str) -> int:
        m = self._SECONDS_RE.search(filename)
        return int(m.group(1)) if m else 0

    def _extract_section_code(self, content: str, section_re: "re.Pattern"):
        """定位 ``section_re`` 命中的二级标题，返回紧随其后的第一个 fenced code block 内容。
        找不到 section 或 section 后无代码块时返回 ``None``。"""
        m = section_re.search(content)
        if not m:
            return None
        tail = content[m.end():]
        fm = self._FENCE_RE.search(tail)
        if not fm:
            return None
        return fm.group(2).rstrip("\n")

    def load_prompts(self, directory: str, is_chinese: bool):
        directory = os.path.normpath(directory or "")
        prompts: list = []
        filenames: list = []
        seconds: list = []

        if not directory or not os.path.isdir(directory):
            return (prompts, filenames, seconds)

        section_re = self._SECTION_ZH_RE if is_chinese else self._SECTION_H3_RE

        for name in sorted(os.listdir(directory)):
            if not name.lower().endswith(".md"):
                continue
            full_path = os.path.join(directory, name)
            if not os.path.isfile(full_path):
                continue

            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            prompt = self._extract_section_code(content, section_re)
            if prompt is None:
                # 找不到目标 section，跳过该文件
                continue

            prompts.append(prompt)
            # 输出不带扩展名的文件名（保持与秒数解析的样本命名一致）
            filenames.append(os.path.splitext(name)[0])
            seconds.append(self._extract_seconds(name))

        return (prompts, filenames, seconds)