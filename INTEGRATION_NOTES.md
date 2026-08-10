# 对接说明：飞书通知模块

本压缩包是给原 ZAI-Paper 项目加上了**飞书应用机器人推送新论文**的功能。
原项目代码（`main.py`、前端、`public/data/zhipu_papers.json` schema）**完全未改动**，
所有新功能都在新增的 `notifications/` package 和改造后的 workflow 里。

## 一、你需要做的事（按顺序）

### 1. 把代码合并到你的仓库

把压缩包解压后，按目录结构覆盖到你仓库里同名文件即可。会出现的几个情况：

- **新增目录**：`notifications/`（整个 package）
- **新增文件**：`.notification-state/feishu.json`（初始空状态）、`tests/test_notifications.py`
- **覆盖更新**：`.github/workflows/update-and-deploy.yml`、`README.md`、`.env.example`、`.gitignore`

合并后跑一次 `python -m unittest discover -s tests -v`，应看到
`Ran 85 tests ... OK`。这就证明代码完整可用。

### 2. 在 GitHub 仓库配 Secrets 和 Variable

打开 **Settings → Secrets and variables → Actions**：

**Secrets**（4 个，都加密存储，绝不进代码）：

| Name | 怎么拿 |
|---|---|
| `ZHIPU_API_KEY` | 你自己的（去 https://bigmodel.cn/ 申请。fork 来的代码**读不到原仓库**的 Key） |
| `FEISHU_APP_ID` | 你飞书自建应用的 App ID |
| `FEISHU_APP_SECRET` | 你飞书自建应用的 App Secret |
| `FEISHU_TARGETS_JSON` | 推送目标 JSON（格式见下方） |

`FEISHU_TARGETS_JSON` 示例：

```json
[{"id":"group","name":"我的群","receive_id_type":"chat_id","receive_id":"oc_xxxxxxxxxxxxxxxx"}]
```

发给个人：

```json
[{"id":"me","name":"我自己","receive_id_type":"open_id","receive_id":"ou_xxxxxxxxxxxxxxxx"}]
```

多个目标可以放数组里：`[{...},{...}]`。

**Variable**（1 个，**先不要设**，跑通后再开）：

| Name | 何时设 |
|---|---|
| `FEISHU_NOTIFICATIONS_ENABLED` | smoke-test + bootstrap + send 全部验证通过后，**才设为 `true`** |

在没设这个 Variable 时，定时任务和 push 都不会发飞书消息——这是设计的安全保护，
防止代码刚合并、还没 bootstrap 就误发整个历史论文清单。

### 3. 在飞书开发者后台准备应用

去 https://open.feishu.cn ：

1. 创建「企业自建应用」，启用「机器人」能力
2. 「权限管理」开通 `im:message:send_as_bot`（以应用身份发消息）
3. 「版本管理与发布」创建版本、提交、管理员审核通过
4. 「可用范围」包含目标群成员或目标个人
5. **推送到群**：机器人必须先「加入目标群」——在群里点群设置 → 群机器人 → 添加你的应用。
   这步不做的话，smoke-test 会返回 `business code 230002`（权限错）。

### 4. 在 GitHub Actions 按顺序首发

打开 **Actions → Update and deploy Pages → Run workflow**，按下面顺序每次选一个
`notification_mode` 跑：

| 次序 | notification_mode | 期望结果 |
|---|---|---|
| 1 | `none` | update + deploy 两 job 都绿，正常部署 Pages |
| 2 | `dry-run` | 日志显示 `bootstrapped=False ... total=206 ...` 等，无报错 |
| 3 | `smoke-test` | 日志显示 `smoke-test sent message_id=...`，目标群（或个人）真的收到测试卡 |
| 4 | `bootstrap` | 日志显示 `bootstrapped 206 historical ids`，Actions 自动提交 `.notification-state/feishu.json` |
| 5 | `send` | 首次应为 `no pending papers`（刚 bootstrap 完没有新增，正常） |

全部通过后：

6. 回 **Settings → Variables** 设 `FEISHU_NOTIFICATIONS_ENABLED = true`
7. 每天定时任务（UTC 01:00 / 北京 09:00）会自动跑 `send` 推新论文

## 二、本地测试（可选但推荐）

如果想先在本地验证再上线：

```bash
# 把 .env.example 复制成 .env，填占位符外的真实值
cp .env.example .env
# 然后用你喜欢的 env 加载方式（PowerShell 用 $env:xxx = "..."）
python -m notifications dry-run
python -m notifications smoke-test
python -m notifications bootstrap
python -m notifications send
```

`.env` 已经被 `.gitignore` 忽略，不会进仓库。**但仍然不要在共享渠道贴真实凭证。**

## 三、关于安全的设计

模块在设计上**强制**了几个安全特性，不需要你额外操心：

- **`send` 是 fail-closed 的**：未 bootstrap 的目标绝对不会被发送（不会误发全部历史 206 篇）
- **状态文件只存 SHA-256 fingerprint**：真实的 chat_id / open_id 永远不会进 git
- **App Secret / token / 完整 receive_id 永远不进日志**
- **多目标互相隔离**：一个失败不会影响另一个
- **增量落盘**：每条消息发成功就立即写入状态，下一次重试只补真正没发出去的
- **状态 commit 带 `[skip ci]` 且 `paths-ignore` 排除通知状态目录**：不会陷入 Actions 循环触发

## 四、卡片版式说明

每张卡里每篇论文这样显示（纯文本块，不依赖 column_set / markdown 表格——
这些在飞书卡片里渲染不可靠，已踩坑验证）：

```
「1 / 4」
English Title（粗体）
中文标题
作者: xxxx
发表: 2026-08-08
标签: 产品技术支持
研究标签: 文本 · 智能体
[arXiv]（蓝主按钮） [PDF]（灰） [ZAI-Paper]（灰）

─────────────（hr 分隔）

「2 / 4」...
```

如果你想加摘要、改字段、改按钮文字等，改 `notifications/cards.py` 即可。
所有卡片字段都有清晰的常量和函数注释。

## 五、文件清单

新增：

- `notifications/__init__.py`、`__main__.py`、`config.py`、`client.py`、`cards.py`、`state.py`、`service.py`
- `tests/test_notifications.py`
- `.notification-state/feishu.json`（初始空状态，会被自动更新和提交）
- `INTEGRATION_NOTES.md`（本文件）

更新：

- `.github/workflows/update-and-deploy.yml`：单 job → 三 job（update / deploy / notify）
- `README.md`：补充通知模块完整文档
- `.env.example`：补充飞书环境变量
- `.gitignore`：忽略 `.notification-state/*.tmp` 临时写入文件

## 六、出问题时

| 现象 | 原因 | 处理 |
|---|---|---|
| `tenant token business code 99991663` | App ID 或 Secret 写错 | 核对后台凭证 |
| `business code 230002 (permission)` | 没开发消息权限 / 应用没发布 / 机器人没加群 | 飞书后台修 |
| `business code 230099 (http 400)` | 卡片结构问题（一般不会出现，本地已验证） | 检查 cards.py 改动 |
| `network error` | 公司网/代理拦了 `open.feishu.cn` | 换网或关代理 |
| bootstrap 正常，但 send 返回 `not bootstrapped` | 状态文件没正确 commit/push | 检查 `.notification-state/feishu.json` 是否在仓库里 |
| smoke-test 一直 fail closed | 状态文件状态对不上 | 看 logs，按提示 `bootstrap --target ... --replace-target` |
