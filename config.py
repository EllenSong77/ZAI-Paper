"""Domain configuration for the Z.AI paper index.

Keep editable names, product terms, taxonomy, and curated exceptions here.
The synchronization pipeline and network settings live in ``main.py``.

Author:
    Ellen Song <jiaqi.song@z.ai>
"""

PEOPLE = {
    "唐杰": "Jie Tang",
    "刘德兵": "Debing Liu",
    "张鹏": "Peng Zhang",
    "顾晓韬": "Xiaotao Gu",
    "刘潇": "Xiao Liu",
    "曾奥涵": "Aohan Zeng",
    "郑问笛": "Wendi Zheng",
    "杜政晓": "Zhengxiao Du",
    "黄明烈": "Minlie Huang",
    "张笑涵": "Xiaohan Zhang",
    "洪文逸": "Wenyi Hong",
    "吕鑫": "Xin Lv",
}

PRODUCT_ALIASES = (
    "GLM",
    "ChatGLM",
    "AutoGLM",
    "WebGLM",
    "CogView",
    "CogVideo",
    "CogVLM",
    "CogAgent",
    "CogCoM",
    "CogCartoon",
    "CodeGeeX",
    "CharacterGLM",
    "MathGLM",
)

SUPPORT_TITLE_TERMS = ("ZCube", "PhoneUse", "Phone-Use", "FastMoE")

# These links cannot be inferred reliably from arXiv metadata alone.
CURATED_SUPPORT_RELATIONS = {
    (
        "From ATOP to ZCube: Automated Topology Optimization Pipeline and A "
        "Highly Cost-Effective Network Topology for Large Model Training"
    ): "ZCube is used by Zhipu as GLM inference-cluster network infrastructure",
    (
        "P-Tuning v2: Prompt Tuning Can Be Comparable to Fine-tuning "
        "Universally Across Scales and Tasks"
    ): "P-Tuning is a foundation tuning technique for the GLM model family",
    "GPT Understands, Too": "P-Tuning supports adaptation of the GLM model family",
    "FastMoE: A Fast Mixture-of-Expert Training System": (
        "FastMoE provides distributed mixture-of-experts training infrastructure"
    ),
    (
        "Relay Diffusion: Unifying diffusion process across resolutions for "
        "image synthesis"
    ): "Relay Diffusion is the generation technique used by CogView3",
    (
        "ImageReward: Learning and Evaluating Human Preferences for "
        "Text-to-Image Generation"
    ): "ImageReward supports preference alignment for the CogView image-model line",
    (
        "WebRL: Training LLM Web Agents via Self-Evolving Online Curriculum "
        "Reinforcement Learning"
    ): "WebRL supports the web-agent capabilities used by AutoGLM",
    "AndroidLab: Training and Systematic Benchmarking of Android Autonomous Agents": (
        "AndroidLab supports phone-agent training and evaluation for AutoGLM"
    ),
}

DIRECT_PRODUCT_TITLES = {
    "GPT Can Solve Mathematical Problems Without a Calculator",
}

SUPPORT_TAG_HINTS = (
    "zcube",
    "fastmoe",
    "phoneuse",
    "inference",
    "serving",
    "infrastructure",
    "training system",
    "mixture-of-expert",
    "mixture of experts",
    "reinforcement learning",
    "reward model",
    "benchmark",
    "evaluation",
    "alignment",
    "prompt tuning",
    "p-tuning",
    "phone-use",
    "phone use",
    "gui agent",
    "computer use",
)

TSINGHUA_MARKERS = ("THUDM", "Tsinghua", "Qinghua")
PAPER_TAGS = ("产品相关", "产品技术支持", "非产品相关")
LEGACY_TAGS = {"产品强相关": "产品相关", "学术输出": "非产品相关"}
TOPIC_TAGS = (
    "文本",
    "图像",
    "视频",
    "语音",
    "多模态",
    "代码",
    "智能体",
    "推理",
    "生成",
    "理解",
    "搜索",
    "检索",
    "推荐",
    "知识图谱",
    "图学习",
    "预训练",
    "后训练",
    "强化学习",
    "对齐",
    "微调",
    "蒸馏",
    "训练系统",
    "推理系统",
    "加速",
    "部署",
    "Infra",
    "模型",
    "框架",
    "数据集",
    "Benchmark",
    "评测",
    "安全",
    "综述",
)

INSTITUTION_ALIASES = {
    "tsinghua": "Tsinghua University",
    "tsinghua university": "Tsinghua University",
    "zhipu ai": "Z.AI",
    "z.ai": "Z.AI",
    "stern school of business, new york university": "New York University",
    "bytedance ai lab": "ByteDance",
}

JIE_TANG_CORE_COAUTHORS = (
    "Yuxiao Dong",
    "Juanzi Li",
    "Ming Ding",
    "Zhiyuan Liu",
    "Maosong Sun",
    "Lei Hou",
    "Zhilin Yang",
    "Jian Tang",
    *tuple(author for author in PEOPLE.values() if author != "Jie Tang"),
)

JIE_TANG_RESEARCH_CATEGORIES = {
    "cs.AI",
    "cs.CL",
    "cs.CV",
    "cs.DB",
    "cs.DC",
    "cs.HC",
    "cs.IR",
    "cs.LG",
    "cs.SE",
    "cs.SI",
}

# These two-person papers were manually verified after same-name ambiguity.
VERIFIED_TITLES = {
    "Training-Free Vector Quantization via Gaussian VAEs",
    "ZeroFlow: Overcoming Catastrophic Forgetting is Easier than You Think",
    "DreamPolish: Domain Score Distillation With Progressive Geometry Generation",
    (
        "Relay Diffusion: Unifying diffusion process across resolutions for "
        "image synthesis"
    ),
}

# External papers that mention Z.AI products but are not Z.AI output.
EXCLUDED_ARXIV_IDS = {
    "2607.02518",  # China Unicom GLM-5 serving-parameter report.
    "2601.03267",  # OpenAI GPT-5 System Card with a same-name Jie Tang.
}
