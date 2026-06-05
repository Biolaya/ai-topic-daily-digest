# ai-topic-daily-digest

AI 驱动的通用主题订阅日报系统。它会按数据库中启用的主题自动搜索新闻、去重、评分，调用大模型生成中文 HTML 摘要日报，并通过 Gmail SMTP 发送。AI 和 Football 只是默认初始化主题，不再是代码里的固定限制；你可以在 Web 后台添加 Minecraft、Finance、Coding Agents、Gaming、Anime 等任意主题。

> 项目早期叫 `ai-football-digest`,现已通用化为任意主题。代码与配置中仍可能残留该旧名,不影响功能。

## 功能

- 命令行入口：`python main.py --dry-run` 和 `python main.py --send`。
- Web 后台：FastAPI + Jinja2，无 React/Vue。
- SQLite 保存主题、关键词、搜索模板、来源评分、新闻条目、运行记录和已发送链接。
- 每个主题可独立配置每日新闻数量、最低分数线、优先级、摘要风格、关键词、搜索模板和来源评分。
- 每日任务会遍历所有启用主题，生成一封多主题日报。
- 支持 `MAIL_TO` 多邮箱，以及 Web 后台数据库收件人管理。
- 支持 `TAVILY_API_KEYS` 多 Key 故障转移，只在数据库保存 key fingerprint。
- 支持用 DeepSeek/OpenAI-compatible API 生成主题建议，用户预览确认后才写入数据库。
- 密钥仍只从 `.env` 读取，网页不展示 `SMTP_PASS`、`LLM_API_KEY`、`OPENAI_API_KEY`、`TAVILY_API_KEY`、`TAVILY_API_KEYS`。
- 发送成功后保存 HTML 日报到 `ARCHIVE_DIR`。

## 目录结构

```text
ai-topic-daily-digest/
├── main.py
├── web.py
├── requirements.txt
├── .env.example
├── README.md
├── LICENSE
├── CLAUDE.md
├── data/
│   └── app.sqlite3
├── logs/
│   └── digest.log
├── src/
│   ├── archiver.py
│   ├── cleanup.py
│   ├── config.py
│   ├── database.py
│   ├── dedupe.py
│   ├── emailer.py
│   ├── models.py
│   ├── renderer.py
│   ├── runner.py
│   ├── schedule.py
│   ├── scorer.py
│   ├── searcher.py
│   ├── security.py
│   ├── summarizer.py
│   ├── tavily_key_manager.py
│   └── topic_generator.py
├── web/
│   ├── deps.py
│   ├── forms.py
│   └── routes.py
├── templates/
│   ├── base.html
│   ├── dashboard.html
│   ├── login.html
│   ├── preview.html
│   ├── recipients.html
│   ├── runs.html
│   ├── settings.html
│   ├── sources.html
│   ├── topic_generate.html
│   ├── topic_generate_preview.html
│   ├── topic_keywords.html
│   ├── topic_queries.html
│   ├── topic_sources.html
│   └── topics.html
├── static/
│   ├── app.js
│   └── style.css
└── tests/
    ├── test_archiver.py
    ├── test_cleanup.py
    ├── test_dedupe.py
    ├── test_default_topics_seed.py
    ├── test_failure_alert.py
    ├── test_preview_persist.py
    ├── test_recipients.py
    ├── test_renderer_dynamic_sections.py
    ├── test_schedule.py
    ├── test_scorer.py
    ├── test_search_topic_news.py
    ├── test_searcher_failover.py
    ├── test_security.py
    ├── test_tavily_key_manager.py
    ├── test_topic_generator.py
    └── test_topic_model.py
```

## 安装

需要 Python 3.10+。

```bash
git clone https://github.com/Biolaya/ai-topic-daily-digest.git
cd ai-topic-daily-digest
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## .env 配置

```dotenv
TAVILY_API_KEY=你的单个 Tavily API Key
TAVILY_API_KEYS=key1,key2,key3

LLM_PROVIDER=deepseek
LLM_API_KEY=你的 DeepSeek 或 OpenAI 兼容接口 Key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-pro

# 兼容旧变量；如果没有配置 LLM_*，程序会读取 OPENAI_*。
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-v4-pro
DEEPSEEK_THINKING=true

ADMIN_USERNAME=admin
ADMIN_PASSWORD=设置一个强密码
SESSION_SECRET=设置一段随机字符串

SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=完整 Gmail 地址
SMTP_PASS=Gmail App Password
MAIL_TO=a@gmail.com,b@qq.com,c@outlook.com

TIMEZONE=Asia/Shanghai
SUMMARY_STYLE=清晰、具体、简洁
ARCHIVE_DIR=data/archives
```

主题数量、关键词、搜索模板和来源评分都保存在 SQLite 中，通过 Web 后台管理。

### Tavily API Key

推荐使用 `TAVILY_API_KEYS` 配置多个 Key：

```dotenv
TAVILY_API_KEYS=key1,key2,key3
```

程序优先读取 `TAVILY_API_KEYS`，没有时回退到旧变量 `TAVILY_API_KEY`。搜索时如果某个 Key 返回 `401/403`，会标记为 invalid 并切换下一个；`429` 会临时禁用 1 小时；网络异常或 `5xx` 会切换下一个。数据库表 `tavily_key_status` 只保存 fingerprint，不保存完整 Key。

### DeepSeek / OpenAI-compatible LLM

当前推荐使用 DeepSeek 的 OpenAI-compatible API：

```dotenv
LLM_PROVIDER=deepseek
LLM_API_KEY=你的 DeepSeek API Key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-pro
DEEPSEEK_THINKING=true
```

兼容旧变量：如果没有配置 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`，程序会读取 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`。

如果 `LLM_API_KEY`/`OPENAI_API_KEY` 为空或模型调用失败，程序会降级使用 Tavily 返回的原始摘要。

### 多邮箱收件人

`.env` 的 `MAIL_TO` 支持英文逗号或分号分隔多个邮箱：

```dotenv
MAIL_TO=a@gmail.com,b@qq.com,c@outlook.com
```

Web 后台 `/recipients` 可以管理数据库收件人。发送时优先使用 `email_recipients` 表中 `enabled=true` 的收件人；如果没有启用收件人，则回退到设置页或 `.env` 的 `MAIL_TO`。单个收件人也可以在 `/recipients` 页面发送测试邮件。

## Gmail App Password

`SMTP_PASS` 必须是 Gmail App Password，不是 Gmail 登录密码。

获取方式：

1. 登录 Google 账号。
2. 打开 Security。
3. 开启 2-Step Verification。
4. 进入 App passwords。
5. 创建一个用于 Mail 的 App Password。
6. 将生成的 16 位密码填入 `.env` 的 `SMTP_PASS`。

Gmail SMTP 固定配置：

```dotenv
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=你的完整 Gmail 地址
SMTP_PASS=Gmail App Password
```

发件人会使用 `SMTP_USER`，代码不会打印 `SMTP_PASS`。

## 初始化和迁移数据库

```bash
cd ai-topic-daily-digest
source .venv/bin/activate
python -m src.database
```

等效 CLI 命令：

```bash
.venv/bin/python main.py --init-db
```

初始化会创建 `data/app.sqlite3`，并在 `topics` 为空时自动创建默认主题：

- AI
- Football

新增表包括：

- `email_recipients`：数据库收件人。
- `tavily_key_status`：Tavily Key fingerprint、状态、失败次数和临时禁用时间。

迁移旧库时不会粗暴删除旧表。带 `category` 字段的旧表会重命名为：

- `keywords_legacy_category`
- `sources_legacy_category`
- `news_items_legacy_category`
- `sent_news_legacy_category`

旧数据会按 `category` 映射到默认 AI / Football 主题的 `topic_id`。如果存在旧的 `data/sent_news.sqlite3`，也会迁移已发送链接到新库。旧 runs 表中的 AI / Football 固定计数字段会迁移到新的 `topic_counts` 字段，旧表保留为 `runs_legacy_fixed_counts`。

## 命令行运行

预览，不发送：

```bash
.venv/bin/python main.py --dry-run
```

正式发送：

```bash
.venv/bin/python main.py --send
```

日报标题格式：

```text
【每日简报】今日 N 个主题更新 - YYYY-MM-DD
```

## Web 后台启动

```bash
cd ai-topic-daily-digest
.venv/bin/uvicorn web:app --host 127.0.0.1 --port 8001
```

访问：

```text
http://127.0.0.1:8001
```

远程服务器建议通过 SSH 隧道访问：

```bash
ssh -i "你的私钥路径" -L 18001:127.0.0.1:8001 root@服务器公网IP
```

然后在本地浏览器打开：

```text
http://127.0.0.1:18001
```

登录账号密码来自 `.env`：

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD=你的后台密码
```

## Web 后台使用

- `/topics`：新增、编辑、软删除、启用、禁用主题，设置 `daily_limit`、`min_score`、`priority`、`summary_style`。
- `/topics/{topic_id}/keywords`：管理某个主题的关键词。
- `/topics/{topic_id}/queries`：管理某个主题的 Tavily 搜索模板。
- `/topics/{topic_id}/sources`：管理某个主题的专属来源评分。
- `/topics/generate`：用 DeepSeek 生成主题建议，预览确认后写入数据库。
- `/recipients`：管理日报收件人，支持新增、编辑、删除、启用、禁用和单人测试邮件。
- `/sources`：管理全局来源评分。
- `/settings`：修改收件人、发送时间、全局摘要风格、自动发送开关。
- `/preview`：执行一次 dry-run 并在网页中预览 HTML 日报。
- `/send-test`：后台发送一封测试邮件，并写入 runs 表。
- `/runs`：查看运行记录、HTML 快照、错误信息和日志尾部。

## 新增 Minecraft 主题

1. 打开 Web 后台 `/topics`。
2. 新增主题：
   - 名称：`Minecraft`
   - Slug：`minecraft`
   - 每日数量：例如 `8`
   - 最低分数线：例如 `40`
   - 优先级：例如 `7`
   - 启用：勾选
3. 进入该主题的“关键词”，添加：
   - `Minecraft`，weight `10`
   - `Mojang`，weight `8`
   - `我的世界`，weight `8`
4. 进入“搜索模板”，添加：
   - `today Minecraft news Mojang update`
   - `今日 我的世界 Minecraft 新闻 Mojang 更新`
5. 进入“来源评分”，按需添加：
   - `minecraft.net`，base_score `95`
   - `mojang.com`，base_score `90`

也可以添加更细的关键词，例如 `Fabric`、`Forge`、`PaperMC`、`模组`，并通过权重控制优先级。

## 用 DeepSeek 自动生成主题

1. 打开 Web 后台 `/topics/generate`。
2. 输入兴趣描述，例如“关注 Minecraft 服务端、Fabric、Forge、PaperMC 和模组生态新闻”。
3. 选择语言、每日数量、最低分数线，以及是否排除娱乐八卦/体育。
4. 点击“生成并预览”。
5. 检查生成的 topic、keywords、query_templates、sources。
6. 确认无误后点击“确认写入数据库”。

生成结果只会先展示在预览页，不会自动写入数据库。若生成的 slug 已存在，页面会提示你选择合并到现有主题或重新生成。

## 评分规则

```text
最终分 = 来源基础分 * 0.35
      + 新鲜度分 * 0.25
      + 关键词命中分 * 0.25
      + 多源重复报道分 * 0.10
      + 用户偏好分 * 0.05
```

来源评分优先级：

1. 主题专属来源评分。
2. 全局来源评分。
3. 默认 45 分。

如果匹配到禁用来源，该新闻会被过滤。新闻最终分数低于主题的 `min_score` 时也会被过滤。

## 去重策略

当前采用全局 URL 去重策略：

- 同一 URL 在一次日报中只出现一次，即使它命中多个主题。
- `sent_news.url` 全局唯一，发送过的 URL 不会再次进入后续日报。
- URL 会先规范化：去掉 `utm_*` 等追踪参数、去掉尾部斜杠、统一域名大小写。
- 标题高度相似的新闻也会在本次日报中去重。

这种策略会减少重复阅读。如果你希望同一链接可以分别出现在多个主题，可以把 `sent_news` 的唯一约束和 `filter_new_items` 改为按 `(topic_id, url)` 去重。

## Gmail SMTP 测试

```bash
cd ai-topic-daily-digest
.venv/bin/python - <<'PY'
from src.config import get_settings
from src.emailer import send_email

settings = get_settings()
settings.validate(send=True)
send_email(settings, "ai-topic-daily-digest SMTP 测试", "<h1>Gmail SMTP OK</h1>")
print("sent")
PY
```

也可以在 Web 后台 Dashboard 点击“发送测试邮件”。

## cron 每日任务

每小时整点运行一次,由代码内的 send_time 守卫决定当天是否真正发送(到点后当天首个整点运行才发,且当天仅发一次)。把下面的 `/path/to/ai-topic-daily-digest` 替换成你的实际部署路径:

```cron
0 * * * * cd /path/to/ai-topic-daily-digest && /path/to/ai-topic-daily-digest/.venv/bin/python main.py --send >> logs/digest.log 2>&1
```

发送时间在 Web 后台 `/settings` 的「发送时间」配置(默认 06:00)。`--send --force` 可绕过守卫强制发送(调试/补发)。

## systemd Web 服务示例

创建 `/etc/systemd/system/ai-topic-daily-digest-web.service`(把 `/path/to/ai-topic-daily-digest` 和 `YOUR_USER` 替换成实际值)：

```ini
[Unit]
Description=ai-topic-daily-digest Web Admin
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/ai-topic-daily-digest
ExecStart=/path/to/ai-topic-daily-digest/.venv/bin/uvicorn web:app --host 127.0.0.1 --port 8001
Restart=always
RestartSec=5
User=YOUR_USER

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ai-topic-daily-digest-web
sudo systemctl status ai-topic-daily-digest-web
```

## 公网部署安全提醒

- 后台必须设置强 `ADMIN_PASSWORD` 和随机 `SESSION_SECRET`。
- 不要把 `.env`、日志、数据库、HTML 归档目录暴露到公网静态服务。
- 不要在 README、issue、聊天记录或截图中粘贴真实 `LLM_API_KEY`、`TAVILY_API_KEYS`、`SMTP_PASS`、`ADMIN_PASSWORD`。
- 推荐使用 SSH 隧道访问后台；如果必须公网开放，请加 Nginx Basic Auth、HTTPS 和 IP 白名单。
- 当前第一版后台未实现 CSRF token，公网部署时应通过反向代理访问限制降低风险，后续可补 CSRF 保护。

## 测试

```bash
cd ai-topic-daily-digest
.venv/bin/python -m pytest -q
```

## 常见问题

### 535 Authentication failed

- 确认 `SMTP_USER` 是完整 Gmail 地址。
- 确认 `SMTP_PASS` 是 Gmail App Password，不是 Gmail 登录密码。
- 确认 Google 账号已开启 2-Step Verification。
- 确认 From 等于 `SMTP_USER`。

### Connection timeout

- 确认服务器能访问 `smtp.gmail.com:465`。
- 检查云服务器安全组、防火墙、网络出口限制。
- 某些云厂商默认限制 SMTP 出站，需要在控制台申请开放。

### Gmail App Password 不可用

- 账号未开启两步验证时通常不可用。
- Google Workspace 账号可能被管理员禁用。
- 开启 Advanced Protection 的账号可能不支持 App Password。

### 邮件进入垃圾箱

- 把发件人加入联系人。
- 避免频繁发送测试邮件。
- 自定义域名发信时配置 SPF、DKIM、DMARC。

## 许可证

本项目基于 [MIT License](LICENSE) 开源,可自由使用、修改和分发,只需保留版权与许可声明。
