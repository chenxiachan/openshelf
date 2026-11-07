# Openshelf

The latest research from people's own websites, collected every day.

Openshelf collects research reports and explainers that people publish on their own sites (personal pages, lab blogs,
company research sites), found through author feeds, archives and discussion sites. Each post is checked by a language
model, and posts that answer it are linked underneath as threads. Inclusion is not peer review.

- Site: https://chenxiachan.github.io/openshelf/ (Chinese edition at `/zh/`)
- Submit a website or ask for removal: open an issue with the templates in this repository
- Code: MIT (see LICENSE). Metadata and summaries: CC BY 4.0. Linked works belong to their authors.
- Build locally: `python3 site/build_openshelf.py`, then serve `site/public/`

The rest of this file is the maintainers' working notes (in Chinese).

---

## 开发笔记（gitpost）

GitPost 的网站与数据管线（未来的 git 仓库根目录）。

## 目录
- `crawl/common.py`：共用函数（限速、链接规范化、页面特征、feed 解析、分片写入）。
- `crawl/backfill.py`：一次性重抓当前三个来源（A：HN 帖子列表，B：Rogue Scholar，C：RSS 失败的学术主页，D：全部 HN 页面）。
- `crawl/candidates.py`：在全部 HN 页面上重跑试运行的规则初筛，挑出还没复核过的候选，分批写到 `data/llm/new_items_N.jsonl`。
- `crawl/pairs.py`：在全部外链里找指向已收录文章或新候选的链接，输出 `data/llm/pairs_todo.jsonl`。
- `crawl/context.py`：限速重抓这些链接的来源页，截取链接上下文（`RPM=60`）。人工改正写在 `data/llm/pair_overrides.json`。
- `site/merge_backfill.py`：把 LLM 结果（`data/llm/out_*.jsonl`）并入试运行数据，重算统计，输出 `site/issues/pilot.json`（网站现在读这个文件）。
- `pipeline/runlog.py`：运行日志、健康检查和报警。每次运行写 `data/logs/runs/<run_id>.jsonl`，并在 `data/logs/runs.jsonl` 里追加一行摘要。出错或检查不通过时报警。用户决定（2026-11-07）只开 GitHub 这一个渠道，下面另外两个渠道保留在代码里，默认不开：
  - `ALERT_GITHUB_REPO` 加 `GITHUB_TOKEN`：开一个带 alert 标签的 issue；同一个问题只追加评论，不重复开。
  - `NTFY_TOPIC`：推送到手机。
  - `HEARTBEAT_URL`：healthchecks.io 之类的心跳检测，任务根本没跑也能知道。
  - 都没配置时，写入 `data/logs/ALERT.txt`。
- `pipeline/llm.py`：调用模型，按 Qwen3.7-Flash、DeepSeek V4 Flash、Nemotron（免费）的顺序逐个尝试。
  - 每批结果都校验 id、取值范围和中英文摘要；漏掉或格式不对的条目，先缩小批量重试，再换下一个模型。
  - 一个模型连续失败 3 次，本次运行就不再用它。
  - 全部失败的条目留到下次运行再处理。
  - 测试开关：`LLM_FORCE_FAIL=模型名` 可以假装某个模型不可用；`LLM_CHAIN` 可以改模型顺序。
- `pipeline/watchdog.py`：看门狗，单独作为一个定时任务运行。通过 GitHub API 查每日任务上次成功运行的时间，超过 36 小时就开一个 alert issue。用来发现"任务根本没跑"的情况。
- `pipeline/classify.py OUT.jsonl [IN.jsonl ...]`：标注新候选，并做三项健康检查：用备用模型的比例不超过 30%、没有漏标、每次费用不超过 0.20 美元。没有输入时只处理积压队列 `data/queue/unlabelled.jsonl`。
- `eval/openrouter_eval.py`、`eval/compare.py`：模型对比测试（2026-11-07）。
- `data/`：抓取结果，体积大，不进 git。`data/hn/pages/` 是分片的 jsonl.gz；`data/logs/` 是日志。
- `site/`：静态网站。
  - `issues/issue-0.json`：本期数据（中英双语摘要），由 `import_probe.py` 从试刊数据生成。
  - `i18n.json`：中英文文案（两种语言的 key 必须一致）。`editorial.json`：编辑排除与改标，每条要写中英文理由。
  - `template.html`：首页，按天分组的信息流（筛选：类型、主题、交互/代码/arXiv/有回应、发现渠道；`#threads` 只看有回应的帖子）。
  - `about.html`：关于与方法页（来源表、主题热力图、试运行发现、方法步骤、编辑记录）。
  - `privacy.json`、`privacy.html`：隐私说明（`/privacy/`、`/zh/privacy/`），按 GDPR 第 13、14 条写。改了正文要同时改 `updated` 日期。开启统计、登录或投票之前，必须先在 `privacy.json` 里加上 `"when": "analytics"`（或 `login`、`votes`）的段落，否则 `build.py` 拒绝构建。
  - `optout.json`：要求退出的网站（`domains`，含子域名）和单篇文章（`urls`）。列入后不再抓取、不再标注，已有条目从 `issues/live.json` 删除，其他文章的外链统计里也去掉。和 `editorial.json` 不同，退出名单不在网站上公开列出。
  - `static/site.css`：两页共用样式；主色为紫色（不用 HN 的橙色），热力图色阶已用 dataviz 校验脚本验证浅色和深色。
  - `config.json`：`site_url`（上线后填，用于 canonical、hreflang 和 sitemap）、`repo_url`、`contact`。
  - `build.py`：生成 `public/`。英文首页 `/`、关于页 `/about/`；中文 `/zh/`、`/zh/about/`。另有数据下载 `data/issue-0.json`、中英文 Atom 订阅、404、robots。页面间都用相对链接，放在子目录下也能用。
  - 排序原则：只按日期分组，同一天里研究报告在前，再按回应数和信号数（交互、代码、arXiv、DOI）。讨论网站的分数只展示，不参与排序。发现渠道按固定顺序列出：作者自己的订阅源、存档平台、讨论网站。

## 常用命令
```
python3 site/build.py                                   # 构建静态站
python3 site/build.py --out design/demo-b --brand Openshelf --logo shelf --theme celadon   # 变体构建：换名字、logo、主题，输出到别的目录（目录里有非构建产物时拒绝清空）
python3 design/build_demos.py                           # 生成 demo C 用的 design/demo-data.js
python3 -m http.server 8765 --directory site/public    # 本地预览 http://127.0.0.1:8765/ （构建时只清空 public 里的内容，预览服务器不用重启）
GH_TOKEN="$(gh auth token)" python3 crawl/backfill.py D # 续抓 HN 页面（可断点续抓）
```

## 处理删除请求（removal issue）
1. 单篇文章写进 `site/optout.json` 的 `urls`；要求停止收集整个网站时写进 `domains`。
2. `python3 pipeline/optout.py` 立即从 `site/issues/live.json` 删除；不跑也行，下一次每日运行开始时会自动删除。
3. 提交并推送，然后 `sh site/deploy_pages.sh` 重新部署（或者等第二天的定时运行）。在 issue 里回复已处理，并关闭 issue。
4. 对方要求连 git 历史一起删除时，用 `git filter-repo` 改写 `site/issues/live.json` 的历史再强制推送，事先在 issue 里说明。

## 抓取限速（本机网络）
- 默认并发 6、每分钟 90 次，代码里设了上限，不会超过。
- 运行中调速：`echo 60 > data/ctl/rpm`（20 秒内生效）。暂停：`touch data/ctl/PAUSE`，恢复：删除该文件。
- 中断后直接重跑同一命令即可续抓；临时网络错误会在本轮末尾重试一次，下次运行时再重试。

## 字体
Schibsted Grotesk 与 IBM Plex Mono（SIL Open Font License），只放了拉丁字符子集，中文走系统字体。字体本地托管，不依赖 Google Fonts。
