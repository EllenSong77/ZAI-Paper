# 对接说明

本仓库当前包含两个独立功能模块，各自对接说明如下。

## 第一部分：论文摘要中文翻译（PR #3，已合并）

本 PR 只新增论文摘要的中文翻译字段及其前端展示，不涉及飞书推送、不修改
`notifications/`、不修改 workflow。

---


## 第二部分：飞书通知模块（PR #2）

本 PR 只新增论文推送到飞书的模块，不涉及论文翻译、不修改 `public/data/zhipu_papers.json`、不修改前端页面。

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

---

| 新增 | `notifications/__init__.py` `__main__.py` `config.py` `client.py` `cards.py` `state.py` `service.py` | 飞书推送包，7 个文件 |
| 新增 | `tests/test_notifications.py` | 单元测试，102 用例 |
| 新增 | `.github/workflows/update-and-deploy.yml`（重构） | 把原本单 job 拆成 `update → deploy → notify` 三个 job |
| 修改 | `main.py` | `merge_rows` 增加 `new_ids` 返回值；`main()` 末尾在增量模式下写 `.notification-state/pending_push.json` 缓存（仅这一段，无翻译改动） |
| 修改 | `.env.example` | 加上飞书相关字段的示例值 |
| 修改 | `.gitignore` | 忽略 `.notification-state/*.tmp`、`.env`、`*.bak` |
| 修改 | `README.md` | 增加「Feishu paper notifications」「Security」章节 |

> 本 PR **未**碰：`public/data/zhipu_papers.json`、`public/index.html`、`backfill_translated_abstracts.py`、任何翻译相关代码。这些都放到另一个独立 PR。

## 二、你需要做的事

1. 在飞书开放平台创建**自建企业应用**，启用 **机器人** 能力，发布一个版本，
   并把目标群加 bot 为成员（或目标个人在应用可见范围内）。
2. 权限：授予 `im:message:send_as_bot`。
3. 在仓库 **Settings → Secrets and variables → Actions** 配置三个 Secret：
   - `FEISHU_APP_ID`
   - `FEISHU_APP_SECRET`
   - `FEISHU_TARGETS_JSON`（JSON 数组，格式见 README）
4. （可选）设置仓库 Variable `FEISHU_NOTIFICATIONS_ENABLED=true`，开放定时
   推送。**不设的话只是关闭定时 `schedule` 触发，手动 `workflow_dispatch`
   仍然可用。**

## 三、安全设计（强制保证）

- 真实的 App ID / App Secret / `receive_id`（chat_id、open_id 等）/
  tenant token / Authorization 头，**绝不**写入代码、测试、文档、日志、
  state 文件。日志只打印 target 的 `id`/`name` 和脱敏后的 `receive_id`。
- 持久化的 `.notification-state/feishu.json` 只存每个 target 的 SHA-256
  指纹 + delivered 论文 id/时间戳/message_id。
- **未 bootstrap 的 target 是 fail-closed**：`send` 永远不群发历史。
  必须显式 `bootstrap` 才能解锁。
- target 的 `receive_id`/type 变更时，指纹不匹配 → `send` 拒绝执行，
  需手动 `bootstrap --target <id> --replace-target`。
- 通知 state 文件位于 `public/` 之外，**不会**被发布到 GitHub Pages。
- bot 自动 commit 带 `[skip ci]`，且 `push.paths-ignore` 进一步防止
  触发循环。

## 四、推送设计（本 PR 的核心）

每轮增量同步时，`main.py` 只把**本轮新增的 arXiv id** 写入小缓存
`.notification-state/pending_push.json`（不进 git、不进 Pages）。

```
incremental sync → main.py 写 pending_push.json = {本轮新 ids}
                                       │
                                       ▼
send: pending_push.json 作为本轮真值 ──► 推一张卡片 ──► 成功则删缓存
       （缓存缺失时回退到「全集 - 已 delivered 滚窗」diff，
         这样刚 bootstrap 的 target 仍能补全历史。）
```

`delivered` 状态只保留 `DELIVERED_RETENTION = 200` 条滚窗，作为短期内
防重复推送的安全网，**不是**需要长期维护的全部历史。

## 五、卡片版式说明（CardKit 2.0）

- 卡片用 CardKit 2.0 schema，直接作为 `interactive` 消息 content 提交，
  无需在线模板/template id。
- 每轮 pending 论文渲染在同一张卡里，按 `(published, arxiv_id)` 升序排列；
  card 底部有「查看完整论文列表」按钮指向 Pages 站点。
- 单篇论文版式（column_set）：
  - 左列：badge 图 + 蓝色序号；
  - 右列：标题、作者（灰色斜体小号）、灰/蓝/紫三色 tag chips、可折叠摘要
    面板（`collapsible_panel` grey border、默认折叠、CTA「点击查看完整摘要」）、
    arXiv / PDF 按钮（同一行 column_set）。
- `translated_abstract` 字段如果存在，会在折叠面板的英文原文下面显示
  「**中文翻译**」段落；如果缺失则降级为仅显示英文，不影响卡片版式。
  本 PR **不负责**填充该字段；那个由翻译 PR 处理。

## 六、本地测试

```bash
cp .env.example .env      # 编辑成你的占位凭据，永远不要 commit
# Bash:
set -a; . .env; set +a
# 或 PowerShell 逐行 $env:XXX = "..."
python -m notifications dry-run
python -m notifications bootstrap
# send 需要真实凭据 + 把 bot 加进目标群后才会成功
python -m unittest discover -s tests -v
```

## 七、出问题排查

| 现象 | 原因 / 处理 |
| --- | --- |
| `230002 bot not in chat` | 把应用 bot 加进目标群（chat_id 模式必需） |
| `230002 open_id cross app` | open_id 是别的 app 签发的，需要本应用重新签发 |
| `9499 too frequent` | 短时间内重复 send，等几分钟 |
| state 报 "fingerprint mismatch" | target 的 `receive_id`/type 改过，重新 `bootstrap --target <id> --replace-target` |
| `pending_push.json is not valid JSON` | 缓存被外部写坏。删掉它，下一次 `main.py` 增量运行会重生成 |

## 八、文件清单

```
notifications/
├── __init__.py     # package marker
├── __main__.py     # CLI: python -m notifications <command>
├── config.py       # env 解析 + 严格 target 校验 + 指纹
├── client.py       # tenant token + send API（有界重试）
├── cards.py        # CardKit 2.0 卡片构建
├── state.py        # 每 target 的原子化、指纹守卫的 delivery state
└── service.py      # dry-run / smoke-test / bootstrap / send 编排
tests/test_notifications.py
.github/workflows/update-and-deploy.yml   # 三 job 拆分
main.py                                    # 仅 pending_push 写缓存段
.env.example
.gitignore
README.md
INTEGRATION_NOTES.md
```
