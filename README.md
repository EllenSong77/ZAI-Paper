# 智谱 arXiv 论文检索

公开页面：

```text
https://ellensong77.github.io/ZAI-Paper/
```

全量查询以下 7 位作者的 arXiv 论文：

- 唐杰 `Jie Tang`
- 刘德兵 `Debing Liu`
- 张鹏 `Peng Zhang`
- 顾晓韬 `Xiaotao Gu`
- 刘潇 `Xiao Liu`
- 曾奥涵 `Aohan Zeng`
- 郑问笛 `Wendi Zheng`

产品关键词：`GLM`、`CogView`、`CogVideoX`、`CogVLM`。

收录规则：

- 命中 3 位及以上指定作者：直接收录；
- 命中产品词且至少有 2 位指定作者：直接收录；
- 仅命中 2 位作者且无产品词：由 GLM 结合标题、摘要和完整作者审核。

LLM 审核和汉译合并执行，每批 15 篇，单并发请求，结果缓存在 `.cache`。

```bash
.venv/bin/python search_papers.py
```

检索数据写入 `outputs/zhipu_papers.json`。生成 Excel：

```bash
node scripts/build_excel.mjs \
  outputs/zhipu_papers.json \
  outputs/zhipu_arxiv_all_history.xlsx
```

## GitHub Pages

静态页面在 `public/index.html`，读取 `public/data/zhipu_papers.json`。

部署方式：

1. 将仓库公开并推到 `git@github.com:EllenSong77/ZAI-Paper.git`。
2. 在 GitHub 仓库 Secrets 中添加 `ZHIPU_API_KEY`。
3. 到 `Settings` -> `Pages`，Source 选择 `GitHub Actions`。
4. 手动运行一次 `Update and deploy Pages` workflow，或等待每天北京时间 09:00 自动运行。

页面地址通常是：

```text
https://ellensong77.github.io/ZAI-Paper/
```
