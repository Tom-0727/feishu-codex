# feishu-codex

用飞书机器人和 Codex 对话，支持连续会话，并通过本机 `codex` CLI 执行读写文件、命令等操作。

```
你（飞书）──► feishu-codex ──► codex exec --json ──► Codex
                  │                      │
            thread_id 持久化         结构化事件流
```

## 前置条件

1. 已安装并登录 `codex` CLI

   ```bash
   codex login
   codex --version
   ```

2. 已创建飞书自建应用，并开启：

   - 权限：`im:message`
   - 权限：`im:message.group_at_msg`（可选）
   - 事件：`im.message.receive_v1`
   - 连接方式：长连接

## 安装

```bash
cd ~/Codes/feishu-codex
cp .env.example .env
uv sync
```

## 启动

```bash
uv run feishu-codex
```

## 使用方式

- 直接发消息：Codex 回复，并自动维持上下文
- `/reset`：清空当前 chat 对应的 Codex thread，开启新会话

会话映射保存在 `~/.feishu-codex/sessions.json`。

## 配置说明

| 变量 | 必填 | 说明 |
|------|------|------|
| `FEISHU_APP_ID` | ✅ | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | ✅ | 飞书应用 App Secret |
| `ALLOWED_USER_IDS` | 可选 | 允许访问的飞书 open_id，逗号分隔 |
| `CODEX_CWD` | 可选 | Codex 工作目录，默认 `~` |
| `CODEX_MODEL` | 可选 | 传给 `codex` 的模型名 |
| `CODEX_SEARCH` | 可选 | 是否开启 `--search` |
| `CODEX_FULL_AUTO` | 可选 | 是否开启 `--full-auto`，默认 `true` |
| `CODEX_DANGEROUS` | 可选 | 是否开启 `--dangerously-bypass-approvals-and-sandbox`，默认 `false` |
| `CODEX_EXTRA_ARGS` | 可选 | 追加自定义 Codex CLI 参数 |

## 实现说明

- 飞书侧使用长连接，不需要公网 IP
- 每个飞书 `chat_id` 对应一个 Codex `thread_id`
- 首轮调用 `codex exec --json`
- 后续调用 `codex exec resume --json <thread_id>`
- 通过 JSON 事件流提取最终回复，并把新的 `thread_id` 持久化到本地

## 注意事项

- 这个版本使用的是本机 `codex` CLI，而不是单独的 Python SDK
- `CODEX_FULL_AUTO=true` 时，Codex 会在沙箱内尽量自动执行任务
- 如果你开启 `CODEX_DANGEROUS=true`，相当于允许 Codex 无沙箱执行，风险自负
