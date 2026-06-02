# API Reference

## POST /v1/completions

This API is the request parameter specification for the text completion service. Since this software implements routing to vLLM service instances, it needs to transparently pass through the received requests to the vLLM instances. Therefore, this API is fully compatible with `/v1/completions` of vLLM (version 0.8.2).

## POST /add_workers
  
Adds vLLM service instances.

| Parameter |  Type| Default Value| Description| Verification Rule|
|------|---------|-----|----------|----------|
| urls | Union[str, List[str]]  | -   | A URL of a single vLLM service instance or a URL list of vLLM service instances|The value cannot be empty. Only HTTP and HTTPS are supported. The host must be in the valid domain name format.|

## POST /remove_workersd

Deletes vLLM service instances.

The parameter requirements are the same as those of `POST /add_workers`.

## GET /list_workers

Lists all vLLM service instances.

## GET /health

Checks whether the server is alive.

## GET /pretty_print_tree

Outputs the prefix cache tree structure and the number of nodes in the tree, for debugging, when the cache-aware routing policy is used.

## Router Initialization Parameters

This API defines the startup configuration parameters for the request routing service (Router). The Router initializes a service that intelligently distributes requests to multiple vLLM service instances. It supports multiple load balancing policies (random, round robin, and cache awareness), and provides built-in enterprise-level functions such as health check, cache-aware scheduling, and log management.

- Basic network configuration

  | Parameter | Type| Default Value|Description|Verification Rule|
  |------|---------|-----|-------|------|
  | host | str   | -   |IPv4 address listened by the routing service|The value must be a valid IPv4 address (for example, <code>0.0.0.0</code> or <code>127.0.0.1</code>), and each segment must be in the range of 0 to 255.|
  | port | int  | -   |Listening port of the routing service| The valid port number ranges from 7000 to 9000.|
  | worker_urls | Union[str, List[str]] | -   |List of backend worker service addresses (a single string or a list is supported)| Each URL must start with <code>http://</code> or <code>https://</code> and be in the valid URL format.|

- Load balancing policy

  | Parameter | Type                                             | Default Value| Description| 
  |------|-------------------------------------------------|-----|----------|
  | policy | Literal["random", "round_robin", "cache_aware"] | "cache_aware"  |Request distribution policy. The options are as follows: <code>random</code> (random selection), <code>round_robin</code> (round robin), and <code>cache_aware</code> (prefix cache awareness). To set this policy, the <code>enable-prefix-caching</code> and <code>enable-chunked-prefill</code> parameters must be set when a vLLM inference instance is started.|

- Worker health check

  | Parameter | Type| Default Value| Range     | Description                                           |
  |------|---------|-----|---------|-----------------------------------------------|
  | worker_startup_timeout_secs | int   | 300 | 3–500| Maximum timeout interval for waiting for a worker to start, in seconds.|
  | worker_startup_check_interval| int  | 3   | 1–10 | Round robin interval for checking whether a worker is ready, in seconds. The value must be less than that of <code>worker_startup_timeout_secs</code>.| 

- Cache-aware scheduling parameters (valid only when `policy` is set to `cache_aware`)

  |  Parameter|  Type|  Default Value| Range | Description |
  | ------------ | ------------ | ------------ | ------------ | ------------ |
  | cache_threshold  |  float | 0.5  | (0, 1) |  Cache hit rate threshold. If the cache hit rate exceeds this value, the cache is considered active, and requests are prioritized to be scheduled to the corresponding worker.|
  | balance_abs_threshold  |  int | 32 |  1–100| Absolute load difference threshold. If the difference exceeds the threshold, load balancing is triggered. |
  | balance_rel_threshold  | float  | 1.0001  |  (1, 3)| Relative load ratio threshold (<code>max_load</code>/<code>min_load</code>). If the ratio exceeds the threshold, load balancing is triggered. |
  |  eviction_interval_secs |  int |  60 | 1–100 | Interval for clearing the cache affinity mapping table, in seconds. |
  | max_tree_size  | int  | 2<sup>24</sup> | 2<sup>15</sup>–2<sup>26</sup> | Maximum number of nodes in the internal cache routing tree, which controls the memory usage. |

- Logging and debugging

  |  Parameter|  Type|  Default Value| Description | Verification Rule |
  | ------------ | ------------ | ------------ | ------------ | ------------ |
  | log_dir  | str  | "" | Log output directory (absolute path). If this parameter is left blank, logs are output to the console. |  The value must be an absolute path. The parent directory must exist and be writable. If the file exists, it must be writable.|
  |  verbose | bool  |  False | Whether to enable detailed log output. | -  |
