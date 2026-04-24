# feishu-codex

用飞书机器人和 Codex 对话。一个服务进程可以管理多个飞书应用和多个 Codex runtime，每个 runtime 绑定一个稳定的 `chat_id`、一个独立的 `codex app-server` 子进程、一个工作目录和一个 thread session。

```
飞书 app ──► feishu-codex ──► runtime router ──► codex app-server ──► Codex
                                  │
                         app + chat_id 路由
```

## 前置条件

1. 已安装并登录 `codex` CLI

   ```bash
   codex login
   codex --version
   ```

   需要使用支持 `codex app-server` 的 Codex CLI 版本。

2. 已创建飞书自建应用，并开启：

   - 权限：`im:message`
   - 权限：`im:message.group_at_msg`（可选）
   - 事件：`im.message.receive_v1`
   - 连接方式：长连接

## 安装

```bash
cd ~/Codes/feishu-codex
uv sync
cp feishu-codex.yaml.example feishu-codex.yaml
```

## 配置

`feishu-codex.yaml` 是唯一运行配置入口。

```yaml
apps:
  main:
    app_id: cli_xxx
    app_secret: xxx

runtimes:
  long-run-agent-harness:
    app: main
    chat_id: oc_xxx
    allowed_user_ids: []
    codex:
      cwd: /home/ubuntu/agents/long-run-agent-harness
      bin: codex
      sandbox: workspace-write
      approval_policy: never
      skip_git_repo_check: true
      rpc_timeout_seconds: 60
      turn_timeout_seconds: 1800
      compact_timeout_seconds: 600
      stop_timeout_seconds: 10
```

同一个飞书应用下有多个 `chat_id` 时，只需要在 `runtimes` 下增加 runtime；服务会复用同一个飞书长连接，再按 `app + chat_id` 路由。只有多组 `app_id/app_secret` 时，才会建立多个飞书长连接。

每个 runtime 默认把 session 保存在 `~/.feishu-codex/runtimes/<runtime_id>/session.json`。也可以在 runtime 顶层显式设置 `session_path`。

## 启动

```bash
uv run feishu-codex --config feishu-codex.yaml
```

不传 `--config` 时默认读取当前目录的 `feishu-codex.yaml`。

## 使用方式

- 直接发消息：对应 runtime 的 Codex 回复，并自动维持上下文
- `/compact`：对当前 runtime 的 Codex thread 手动 compact
- `/reset`：清空当前 runtime 的 Codex thread，开启新会话
- 其他 `/` 开头的消息会作为指令处理，不会发送给 Codex

## 实现说明

- 飞书侧使用长连接，不需要公网 IP
- 一个 Feishu app 建一个 WebSocket 连接
- 一个配置的 `app + chat_id` 路由到一个独立 Codex runtime
- 每个 Codex runtime 拥有自己的 `codex app-server` 子进程、工作目录、sandbox、approval policy、timeout 和 session 文件
- 首轮通过 JSON-RPC `thread/start` 创建 Codex thread
- 后续通过 `thread/resume` 恢复已持久化的 thread
- 普通消息通过 `turn/start` 执行
- `/compact` 通过 `thread/compact/start` 对当前 thread 手动 compact

## 注意事项

- 这个版本只使用本机 `codex app-server`，没有 `codex exec` fallback
- `feishu-codex.yaml` 会包含飞书密钥，默认已被 `.gitignore` 忽略
- 如果你把 `sandbox` 设置为 `danger-full-access`，相当于允许 Codex 无沙箱执行，风险自负
