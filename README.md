# 项目介绍
**vLLM-Router是鲲鹏参与vLLM开源社区的路由插件，旨在支持数据并行部署，提供高性能的请求路由与负载均衡能力。该路由器内置多种负载均衡算法，包括前缀缓存感知、随机和轮询，可根据实际场景灵活选择，以优化系统整体性能。**


# 版本说明
由于作为vLLM的外挂插件使用，故不限定于vLLM版本。


# 环境部署
## 已验证硬件配置
CPU：920新型号
GPU：沐曦C500

## 软件配置
操作系统：openEuler 22.03 LTS SP2
python版本：python >= 3.10


# 快速上手

## 安装vLLM推理软硬件环境
以鲲鹏920新型号CPU+沐曦的曦云C500 NPU * 8硬件环境为例，参考沐曦官方的[文档](https://developer.metax-tech.com/api/client/document/preview/781/index.html)安装驱动、固件和vLLM推理容器后，即可进行vLLM-Router的部署。

由于沐曦官方当前在aarch64架构CPU上仅支持KylinV10 2309a和kylin2309 SP2操作系统，但KylinV10 2309a所用系统内核为5.10.0-153.12.0.92.oe2203sp2.aarch64，故下载驱动、固件安装包和docker镜像时，选择KylinV10 2309a所对应的版本即可。

注：vLLM-Router作为vLLM的外挂插件使用，增加数据并行部署功能，故对实际硬件环境同样不做要求，能运行vLLm推理框架即可。上述鲲鹏920新型号CPU及沐曦的曦云C500 NPU硬件环境仅作为参考示例。

## 启动vllm实例
- 进入沐曦的推理容器，在当前会话下按以下命令设置可见的GPU并启动第一个vLLM实例
```
export CUDA_VISIBLE_DEVICES=0,1,2,3
vllm serve /path/to/model/ --port xxxx --trust_remote_code
```
其中xxxx为指定的端口号，如8001等

- 在同容器中另起一个会话，按以下命令设置可见的GPU并启动第二个vLLM实例
```
export CUDA_VISIBLE_DEVICES=4,5,6,7
vllm serve /path/to/model/ --port xxxx --trust_remote_code
```
其中xxxx为指定的端口号，如8002等

- 在同容器中再启动一个会话，并在该会话下按以下命令启动数据并行路由
```
python launch_server.py --host 127.0.0.1 --port xxxx --worker_urls http://127.0.0.1:xxxx http://127.0.0.1:xxxx --policy round_robin
```
其中port参数为路由的对外端口，如8008，worker_urls为启动的vllm实例的url，policy为路由策略，具体参数见API参考章节。

## 安装后验证
启动后可以执行vllm官方的benchmark性能测试框架
以vllm 0.8.2版本为例，下载
[backend_request_func.py](https://github.com/vllm-project/vllm/blob/v0.8.2/benchmarks/backend_request_func.py)，
[benchmark_dataset.py](https://github.com/vllm-project/vllm/blob/v0.8.2/benchmarks/benchmark_dataset.py)，
[benchmark_serving.py](https://github.com/vllm-project/vllm/blob/v0.8.2/benchmarks/benchmark_serving.py)，
[benchmark_utils.py](https://github.com/vllm-project/vllm/blob/v0.8.2/benchmarks/benchmark_utils.py)
这4个文件。在这4个文件的存放路径下执行以下命令启动性能测试
```
python benchmark_serving.py --model /path/to/model/ --port xxxx --dataset_name random --random-input-len xx --random-output-len xxx --ignore-eos --num-prompts xx --request_rate xx
```
其中
--model 为使用的模型路径，需要与启动vllm实例时设置的`/path/to/model/`一致
--port 为路由器的对外端口
--dataset_name 为进行性能测试时使用的数据集，random表示使用随机数据
--random-input-len 为进行性能测试时随机输入的长度
--random-output-len 为进行性能测试时输出的长度
--ignore-eos 表示忽略停止符
--num-prompts 为进行性能测试时发送的prompt数量
--request_rate 为进行性能测试时发送的prompt的并发数

执行benchmark性能测试的结果如下所示
```
============ Serving Benchmark Result ============
Successful requests:                     8         
Benchmark duration (s):                  72.53      
Total input tokens:                      65536         
Total generated tokens:                  2048         
Request throughput (req/s):              0.11      
Output token throughput (tok/s):         28.24      
Total Token throughput (tok/s):          931.82      
---------------Time to First Token----------------
Mean TTFT (ms):                          18127.24      
Median TTFT (ms):                        18290.29      
P99 TTFT (ms):                           28614.15      
-----Time per Output Token (excl. 1st token)------
Mean TPOT (ms):                          206.40      
Median TPOT (ms):                        205.78      
P99 TPOT (ms):                           246.14      
---------------Inter-token Latency----------------
Mean ITL (ms):                           206.40      
Median ITL (ms):                         167.95      
P99 ITL (ms):                            1737.86      
==================================================
```
# API 参考

**POST /v1/completions**

   本接口为文本补全（completion）服务的请求参数规范。由于本软件实现的是对vLLM服务实例的路
由，需要将接收到的请求透传给vLLM实例，故该接口完全兼容vLLM（0.8.2）的/v1/completions接
口。

**POST /add_workers**
  
  增加vLLM服务实例

| 参数名  |  类型 | 默认值 | 说明 | 校验规则 |
|------|---------|-----|----------|----------|
| urls | Union[str, List[str]]  | -   | 单个vLLm服务实例URL或vLLm服务实例URL列表 |不得为空；仅支持http://和https://协议；主机部分必须合法域名格式。|

**POST /remove_workers**
  
  删除vLLM服务实例。

  参数要求同POST /add_workers接口。

**GET /list_workers**

  列出所有vLLm服务实例。

**GET /health**
    
检查本服务端是否存活。

**GET /pretty_print_tree**
    
当使用缓存感知路由策略时，输出前缀缓存树结构及树的节点数，方便调试.

**路由器初始化参数**
    
本接口定义了请求路由服务（Router） 的启动配置参数，用于初始化一个智能分发请求到多个vLLM服务实例的路由服务。支持多种负载均衡策略（随机、轮询、缓存感知），并内置健康检查、缓存亲和性调度、日志管理等企业级功能。

- 基础网络配置

| 参数名  | 类型 | 默认值 |说明|校验规则|
|------|---------|-----|-------|------|
| host | str   | -   |路由服务监听的IPv4地址|必须是合法IPv4地址（如 0.0.0.0,127.0.0.1），每个段在0~255范围内|
| port | int  | -   |路由服务监听端口| 有效端口范围：7000 ~ 9000|
| worker_urls | Union[str, List[str]] | -   |后端 Worker 服务地址列表（支持单个字符串或列 表） | 每个URL必须以http://或https://开头，且为合法URL格式|

- 负载均衡策略

| 参数名  | 类型                                              | 默认值 | 说明 | 
|------|-------------------------------------------------|-----|----------|
| policy | Literal["random", "round_robin", "cache_aware"] | "cache_aware"  |请求分发策略：random：随机选择；round_robin：轮询；cache_aware：前缀缓存感知，若使用该路由策略，那么启动vLLM推理实例时，须设置enable-prefix-caching和enable-chunked-prefill两个参数|

- Worker健康检查

| 参数名  | 类型 | 默认值 | 范围      | 说明                                            |
|------|---------|-----|---------|-----------------------------------------------|
| worker_startup_timeout_secs | int   | 300 | 3 ~ 500 | 等待 Worker 启动的最大超时时间（秒） |
| worker_startup_check_interval| int  | 3   | 1 ~ 10  | 检查 Worker是否就绪的轮询间隔（秒），要求小于worker_startup_timeout_secs| 

- 缓存感知调度参数（仅 policy="cache_aware" 时生效）

|  参数名 |  类型 |  默认值 | 范围  | 说明  |
| ------------ | ------------ | ------------ | ------------ | ------------ |
| cache_threshold  |  float | 0.5  | 0 < x < 1  |  缓存命中率阈值，高于此值认为缓存“热”，优先调度到该Worker |
| balance_abs_threshold  |  int | 32 |  1 ~ 100 | 绝对负载差阈值，超过则触发负载均衡动作  |
| balance_rel_threshold  | float  | 1.0001  |  1 < x < 3 | 相对负载比阈值（max_load / min_load），超过则触发负载均衡  |
|  eviction_interval_secs |  int |  60 | 1 ~ 100  | 缓存亲和性映射表的清理间隔（秒）  |
| max_tree_size  | int  | 2^24  | 2^15 ~ 2^26  | 内部缓存路由树的最大节点数，控制内存占用  |


- 日志与调试

|  参数名 |  类型 |  默认值 | 说明  | 校验规则  |
| ------------ | ------------ | ------------ | ------------ | ------------ |
| log_dir  | str  | “”  | 日志输出目录（绝对路径）。若为空则输出到控制台  |  必须为绝对路径，父目录必须存在且可写。若文件存在，必须可写 |
|  verbose | bool  |  False | 是否启用详细日志输出  | -  |

# 通信矩阵
|  源设备 |  源IP地址 |  源端口 | 目的设备  | 目的IP地址  | 目的端口（侦听） | 协议 | 端口说明 | 侦听端口是否可更改 | 认证方式 | 加密方式 | 所属平面 | 版本 | 特殊场景 |
| ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ |
| 用户所用服务器  | 用户服务器IP地址  | * | 运行vllm-router服务的服务器  |  运行vllm-router服务的IP地址 | 7000~9000 | HTTP/HTTPS | 接收用户推理请求 | 是 | N/A | N/A | 业务面 | 所有版本 | 无 |


# 贡献指南
如果使用过程中有任何问题，或者需要反馈特性需求和bug报告，可以提交issues联系我们，具体贡献方法可参考[这里](https://gitcode.com/boostkit/community/blob/master/docs/contributor/contributing.md)。


# 免责声明
此代码仓计划参与vLLM软件开源，为vLLM增加数据并行部署能力，编码风格遵照原生开源软件，继承原生开源软件安全设计，不破坏原生开源软件设计及编码风格和方式，软件的任何漏洞与安全问题，均由相应的上游社区根据其漏洞和安全响应机制解决。请密切关注上游社区发布的通知和版本更新。鲲鹏计算社区对软件的漏洞及安全问题不承担任何责任。

# 许可证书
Apache License 2.0
