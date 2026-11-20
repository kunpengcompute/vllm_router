# API参考

## POST /v1/completions

本接口为文本补全（completion）服务的请求参数规范。由于本软件实现的是对vLLM服务实例的路由，需要将接收到的请求透传给vLLM实例，故该接口完全兼容vLLM（0.8.2）的/v1/completions接口。

## 鉴权说明

管理面接口（`/add_workers`、`/remove_workers`、`/list_workers`、`/pretty_print_tree`）与数据面接口（`/v1/completions`）监听同一端口。鉴权为**可选开启**，默认不改变升级前的行为：

| 接口 | 默认鉴权 | 说明 |
|------|----------|------|
| `GET /health` | 无 | 存活探测，始终公开。 |
| 管理面接口 | **可选（默认关闭）** | 未配置 `--admin-token`（或环境变量 `VLLM_ROUTER_ADMIN_TOKEN`）时不鉴权，与升级前行为一致；配置后需携带 `Authorization: Bearer <token>` 或 `X-Admin-Token: <token>`，否则返回 401。 |
| `POST /v1/completions` | 可选 | 配置 `--api-token`（或环境变量 `VLLM_ROUTER_API_TOKEN`）后需携带令牌，否则返回 401；未配置时不鉴权。 |

令牌长度建议不少于 16 个字符，短于该长度时 router 会打印告警但不会拒绝该令牌。管理面鉴权失败与所有管理操作均会记录审计日志。

### 开启鉴权的迁移步骤

1. 生成一个随机字符串作为令牌（建议不少于 16 个字符）：
   ```bash
   openssl rand -hex 16
   ```
2. 通过环境变量注入（推荐，避免令牌出现在进程列表中）：
   ```bash
   export VLLM_ROUTER_ADMIN_TOKEN='<token>'
   export VLLM_ROUTER_API_TOKEN='<token>'
   ```
   也可使用命令行参数 `--admin-token` / `--api-token`（优先级高于环境变量）。
3. 重启 router，管理面接口即开始校验令牌：未携带令牌或令牌错误的请求返回 401。
4. 客户端需在请求中携带 `Authorization: Bearer <token>` 或 `X-Admin-Token: <token>`。

> 鉴权开关不改变任何接口的路径与报文格式。未配置令牌时客户端无需任何改动；已配置令牌后，未携带令牌的既有客户端会收到 401，请先完成第 4 步再重启。

## POST /add_workers
  
增加vLLM服务实例。

| 参数名  |  类型 | 说明 | 校验规则 |
|------|---------|----------|----------|
| urls | Union[str, List[str]]   | 单个vLLM服务实例URL或vLLM服务实例URL列表 | 不得为空；仅支持`http://`和`https://`协议；主机部分必须合法域名格式。|

## POST /remove_workers
  
删除vLLM服务实例。

参数要求同POST /add_workers接口。

## GET /list_workers

列出所有vLLM服务实例。

## GET /health

检查本服务端是否存活。

## GET /pretty_print_tree

当使用缓存感知路由策略时，输出前缀缓存树结构及树的节点数，方便调试。

## 路由器初始化参数

本接口定义了请求路由服务（Router） 的启动配置参数，用于初始化一个智能分发请求到多个vLLM服务实例的路由服务。支持多种负载均衡策略（随机、轮询、缓存感知），并内置健康检查、缓存亲和性调度、日志管理等企业级功能。

- 基础网络配置

  | 参数名  | 类型 |说明|校验规则|
  |------|---------|-------|------|
  | host | str | 路由服务监听的IPv4地址 | 必须是合法IPv4地址（如 0.0.0.0,127.0.0.1），每个段在0~255范围内 。 |
  | port | int | 路由服务监听端口 | 有效端口范围：7000 ~ 9000 。 |
  | worker_urls | Union[str, List[str]] | 后端 Worker 服务地址列表（支持单个字符串或列表） | 每个URL必须以`http://`或`https://`开头，且为合法URL格式。 |

- 负载均衡策略

  | 参数名 | 类型 | 默认值 | 说明 |
  |------ | --------------------- | ----- | ---------- |
  | policy | Literal["random", "round_robin", "cache_aware"] | "cache_aware"  | 请求分发策略。<br>random：随机选择。<br>round_robin：轮询。<br>cache_aware：前缀缓存感知，若使用该路由策略，那么启动vLLM推理实例时，须设置enable-prefix-caching和enable-chunked-prefill两个参数 。 |

- Worker健康检查

  | 参数名 | 类型 | 默认值 | 范围 | 说明 |
  | ------ | --------- | ----- | --------- | ---------- |
  | worker_startup_timeout_secs | int  | 300 | 3 ~ 500 | 等待Worker启动的最大超时时间（秒） 。 |
  | worker_startup_check_interval | int | 3  | 1 ~ 10 | 检查 Worker是否就绪的轮询间隔（秒），要求小于worker_startup_timeout_secs。 |

- 缓存感知调度参数（仅 policy="cache_aware" 时生效）

  | 参数名 | 类型 | 默认值 | 范围 | 说明 |
  | ------------ | ------------ | ------------ | ------------ | ------------ |
  | cache_threshold | float | 0.5 | 0 < x < 1 | 缓存命中率阈值，高于此值认为缓存“热”，优先调度到该Worker。 |
  | balance_abs_threshold | int | 32 | 1 ~ 100 | 绝对负载差阈值，超过则触发负载均衡动作。 |
  | balance_rel_threshold | float | 1.0001 | 1 < x < 3 | 相对负载比阈值（max_load / min_load），超过则触发负载均衡。 |
  | eviction_interval_secs | int | 60 | 1 ~ 100 | 缓存亲和性映射表的清理间隔（秒）。 |
  | max_tree_size | int | 2^24 | 2^15 ~ 2^26 | 内部缓存路由树的最大节点数，控制内存占用。 |

- 鉴权参数

  | 参数名 | 类型 | 默认值 | 说明 | 校验规则 |
  | ------ | ---- | ------ | ---- | -------- |
  | admin_token | str | "" | 管理面接口（`/add_workers`、`/remove_workers`、`/list_workers`、`/pretty_print_tree`）所需的共享密钥。也可用环境变量 `VLLM_ROUTER_ADMIN_TOKEN` 提供。**未配置时管理面接口不鉴权**，与升级前行为一致。 | 建议不少于 16 个字符，短于该长度时打印告警但不拒绝。首尾空白会被去除。 |
  | api_token | str | "" | 推理接口 `/v1/completions` 的可选共享密钥。也可用环境变量 `VLLM_ROUTER_API_TOKEN` 提供。未配置时该接口不鉴权。 | 建议不少于 16 个字符，短于该长度时打印告警但不拒绝。首尾空白会被去除。 |

- 日志与调试

  | 参数名 | 类型 | 默认值 | 说明 | 校验规则 |
  | ------------ | ------------ | ------------ | ------------ | ------------ |
  | log_dir | str | "" | 日志输出目录（绝对路径）。若为空则输出到控制台。 | 必须为绝对路径，父目录必须存在且可写。若文件存在，必须可写。 |
  | verbose | bool | False | 是否启用详细日志输出。 | - |
