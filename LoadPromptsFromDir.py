import os
import re


class LoadPromptsFromDir:
    """从磁盘目录加载提示词列表。

    目录下每个 ``.md`` 文件按以下规则抽取提示词：
      - ``is_chinese=True``  → 抓取 ``## 中文版提示词（...）`` section 后的第一个 fenced code block
      - ``is_chinese=False`` → 抓取 ``## H3 Prompt（English）`` section 后的第一个 fenced code block

    秒数从文件名尾部 ``_Ns.md`` 解析（如 ``EP01_S01_cold-open_15s.md`` → 15）。

    人物名从文件名解析：按 ``_`` 切段，跳过编号段（``EP00`` / ``P05`` / ``01`` ...）
    与纯 ASCII 场景 slug（``voice-design`` / ``upside-down-sky``），第一个「像人物名」
    的段（含中文，或含多角色分隔符 ``x`` / ``X`` / ``×`` / ``&`` / ``+`` / ``·`` /
    ``、`` / ``,`` / ``/``）即人物段，段内按这些分隔符切分出多个角色。
    不依赖 ``EPxx_Pxx`` 前缀，因此下列命名都支持：
      - ``EP00_P00_姬八稳_upside-down-sky_15s`` → ``['姬八稳', 'None', 'None']``
      - ``EP00_P05_姬八稳x陈美婷_teacher-pats-his-shoulder_20s`` → ``['姬八稳','陈美婷','None']``
      - ``01_姬八稳x吴淼_voice-design_20s`` → ``['姬八稳', '吴淼', 'None']``
      - ``EP01_S01_cold-open_15s``（纯场景镜）→ ``['None', 'None', 'None']``
    结果统一补齐到固定槽位，缺失位填字符串 ``'None'``，方便下游稳定取值。

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

    RETURN_TYPES = ("LIST", "LIST", "LIST", "LIST")
    RETURN_NAMES = ("prompts", "filenames", "seconds", "characters")
    FUNCTION = "load_prompts"
    CATEGORY = "utils/path"

    _SECTION_ZH_RE = re.compile(r'^.*中文版提示词.*$', re.MULTILINE)
    _SECTION_H3_RE = re.compile(r'^.*Prompt.*$', re.MULTILINE | re.IGNORECASE)
    _FENCE_RE = re.compile(r'```([^\n`]*)\n(.*?)```', re.DOTALL)
    _SECONDS_RE = re.compile(r'_(\d+)s\.md$', re.IGNORECASE)
    # 文件名开头的编号段（EP00 / P05 / S01 / E03 / 01 ...），这些不是人物名
    _INDEX_TOKEN_RE = re.compile(r'^(?:ep|p|s|e)?\d+$', re.IGNORECASE)
    # 人物段内的多角色分隔符：x / X / × / & / ＋ / + / · / 、 / ， / ,
    _NAME_SEP_RE = re.compile(r'\s*(?:[xX×&＋+·]|、|，|,|/|＆)\s*')
    # 文件名尾部的秒数后缀（不带扩展名时使用）
    _SECONDS_TAIL_RE = re.compile(r'_\d+s$', re.IGNORECASE)
    # 场景 slug 特征：纯 ASCII（字母/数字/连字符），如 upside-down-sky、voice-design
    _SLUG_WITH_DASH_RE = re.compile(r'^[a-z0-9-]+$', re.IGNORECASE)

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

    def _extract_characters(self, filename: str) -> list:
        """从文件名解析出现的人物名，返回固定多槽位列表（缺失位补 ``'None'``）。

        判定逻辑（不依赖 ``EPxx_Pxx`` 前缀，按段内容识别）：

        1. 去掉扩展名与尾部 ``_Ns`` 后按 ``_`` 切段；
        2. 跳过开头的裸编号段（``EP00`` / ``P05`` / ``S01`` / ``01`` ...）与
           纯 ASCII 场景 slug（``voice-design``、``upside-down-sky`` 等）；
        3. 第一个「像人物名」的段即人物段 —— 含中文，或含多角色分隔符
           （``x`` / ``X`` / ``×`` / ``&`` / ``+`` / ``·`` / ``、`` / ``,`` / ``/``）；
        4. 人物段内按上述分隔符切分，得到多个角色。

        于是以下命名都能解析：
          - ``EP00_P00_姬八稳_upside-down-sky_15s`` → ``['姬八稳', 'None', 'None']``
          - ``EP00_P05_姬八稳x陈美婷_teacher-pats-his-shoulder_20s`` → 两角色
          - ``01_姬八稳x吴淼_voice-design_20s`` → ``['姬八稳', '吴淼', 'None']``
          - ``EP01_S01_cold-open_15s`` → 全 ``'None'``（纯场景镜，无人物）
        """
        base = os.path.splitext(filename)[0]
        base = self._SECONDS_TAIL_RE.sub("", base)
        parts = [p for p in base.split("_") if p.strip()]

        names: list = []
        for token in parts:
            if self._INDEX_TOKEN_RE.match(token):
                continue  # 编号段，跳过
            cand = [n.strip() for n in self._NAME_SEP_RE.split(token) if n.strip()]
            if len(cand) >= 2:
                # 明确的多角色段（含分隔符），优先采纳
                names = cand
                break
            if self._SLUG_WITH_DASH_RE.match(token) and "-" in token:
                continue  # 纯 ASCII 场景 slug，如 voice-design / upside-down-sky
            if self._has_cjk(token):
                # 含中文的单人角色段
                names = [token]
                break

        # 统一补齐到固定槽位（默认 3，不截断超出者）
        while len(names) < 3:
            names.append('None')
        return names

    @staticmethod
    def _has_cjk(text: str) -> bool:
        """段内是否含中日韩字符（用于把中文人物名与 ASCII 场景 slug 区分开）。"""
        return any('\u4e00' <= ch <= '\u9fff' for ch in text)

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
        characters: list = []

        if not directory or not os.path.isdir(directory):
            return (prompts, filenames, seconds, characters)

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
            characters.append(self._extract_characters(name))

        return (prompts, filenames, seconds, characters)