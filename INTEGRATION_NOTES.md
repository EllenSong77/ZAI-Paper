# 对接说明：论文摘要中文翻译

本 PR 只新增论文摘要的中文翻译字段及其前端展示，不涉及飞书推送、不修改
`notifications/`、不修改 workflow。

## 一、改了哪些文件

| 类型 | 文件 | 说明 |
| --- | --- | --- |
| 修改 | `main.py` | GLM review prompt 增加 translated_abstract 输出要求；`review_and_translate` 解析并缓存 translated_abstract；`row_from_candidate` 把它注入到公共 row schema（**仅这 3 处**，无 pending_push 改动） |
| 新增 | `backfill_translated_abstracts.py` | 一次性脚本：把已存在行的英文摘要翻译为中文 `translated_abstract`，已翻译的跳过；支持 `BACKFILL_LIMIT` 试点 |
| 修改 | `public/data/zhipu_papers.json` | 208 行论文现已 100% 覆盖 `translated_abstract`（之前 0 篇）；其中 63 篇长摘要（>1400 字符）基于完整输入重新翻译，确保不断句 |
| 修改 | `public/index.html` | 论文预览 modal 在「论文概览」标题旁加中英切换按钮（默认英文、点「查看中文」原地替换、无译文时按钮自动隐藏；+85 / -1 行） |
| 新增 | `tests/test_backfill.py` | 覆盖 `backfill_translated_abstracts.py` 的网络无关辅助函数 |
| 修改 | `README.md` | 增加「Bilingual abstracts (EN + ZH)」章节 |

> 本 PR **未**碰：`notifications/`、`.github/workflows/`、`main.py` 里写
> pending_push 的逻辑、`.env.example`。这些都在另一个独立的推送 PR 里。

## 二、翻译质量约定

- 第三人称客观叙述，不补充评论。
- 标题和摘要共享同一套术语（人名、模型名、数据集名等专有名词保持一致，
  如 Transformer 一律不译）。
- 若 abstract 已是中文或为空，`translated_abstract` 等同原文或空字符串。
- 不省略关键论点，长度与原文相当。

## 三、前端 UX

- 「论文概览」标题右侧加胶囊状切换按钮，默认英文摘要、按钮「查看中文」；
  点击原地替换为中文摘要，按钮变「查看英文」，再点切回。
- 该论文无 `translated_abstract` 时按钮整隐藏，不影响版式。
- 中文摘要较长时 modal 内部（`.preview-content`）出现纵向滚动条，不会被截断。
- 不改变现有预览交互的其它部分。

## 四、Backfill 脚本

```bash
# 全量（推荐先备份 zhipu_papers.json）
cp public/data/zhipu_papers.json public/data/zhipu_papers.json.bak
ZHIPU_API_KEY=... python backfill_translated_abstracts.py

# 试点 15 篇看翻译质量
BACKFILL_LIMIT=15 ZHIPU_API_KEY=... python backfill_translated_abstracts.py
```

- 每批 15 篇，3 次重试，超时 180s。
- 已翻译的行（`translated_abstract` 非空）自动跳过；可重入。
- 原子写：先写 `.tmp` 再 `os.replace`，避免中途崩溃留下半截文件。

## 五、单元测试

- `tests/test_backfill.py` 覆盖：
  - `_parse_json_array` 处理纯 JSON、markdown fence、prose 前缀、非数组输入
  - `_classify_model` 的默认值和空格处理
- 不发网络请求，无需真实 GLM key。

跑测试：

```bash
python -m unittest discover -s tests -v
```

## 六、文件清单

```
main.py                                  # 翻译改动 + v8 缓存版本 + 校验
backfill_translated_abstracts.py         # 新增（完整摘要输入，无 1400 截断）
public/data/zhipu_papers.json            # 208 篇全量回填
public/index.html                        # 中英切换按钮（+85 / -1）
tests/test_backfill.py                   # 新增
tests/test_search.py                     # 摘要校验回归测试
README.md
```
