# 用户指南

## 安装vLLM推理软硬件环境

以鲲鹏920新型号处理器+沐曦的曦云C500 GPU * 8硬件环境为例，参考沐曦官方的[文档](https://developer.metax-tech.com/api/client/document/preview/781/index.html)安装驱动、固件和vLLM推理容器后，即可进行vLLM-Router的部署。

由于沐曦官方当前在aarch64架构CPU上仅支持KylinV10 2309a和kylin2309 SP2操作系统，但KylinV10 2309a所用系统内核为5.10.0-153.12.0.92.oe2203sp2.aarch64，故下载驱动、固件安装包和docker镜像时，选择KylinV10 2309a所对应的版本即可。

> **说明：** vLLM-Router作为vLLM的外挂插件使用，增加数据并行部署功能，故对实际硬件环境同样不做要求，能运行vLLm推理框架即可。上述鲲鹏920新型号处理器及沐曦的曦云C500 NPU硬件环境仅作为参考示例。

## 启动vLLM实例

- 进入沐曦的推理容器，在当前会话下按以下命令设置可见的GPU并启动第一个vLLM实例。

  ```bash
  export CUDA_VISIBLE_DEVICES=0,1,2,3
  vllm serve /path/to/model/ --port xxxx --trust_remote_code
  ```

  其中xxxx为指定的端口号，如8001等。

- 在同容器中另起一个会话，按以下命令设置可见的GPU并启动第二个vLLM实例。

  ```bash
  export CUDA_VISIBLE_DEVICES=4,5,6,7
  vllm serve /path/to/model/ --port xxxx --trust_remote_code
  ```
  
  其中xxxx为指定的端口号，如8002等。

- 在同容器中再启动一个会话，并在该会话下按以下命令启动数据并行路由。

  ```bash
  python launch_server.py --host 127.0.0.1 --port xxxx --worker_urls http://127.0.0.1:xxxx http://127.0.0.1:xxxx --policy round_robin
  ```

  其中port参数为路由的对外端口，如8008，worker_urls为启动的vllm实例的url，policy为路由策略，具体参数见《[API参考](./api_referenc.md)》。

## 安装后验证

启动后可以执行vllm官方的benchmark性能测试框架。

以vllm 0.8.2版本为例，下载[backend_request_func.py](https://github.com/vllm-project/vllm/blob/v0.8.2/benchmarks/backend_request_func.py)，
[benchmark_dataset.py](https://github.com/vllm-project/vllm/blob/v0.8.2/benchmarks/benchmark_dataset.py)，
[benchmark_serving.py](https://github.com/vllm-project/vllm/blob/v0.8.2/benchmarks/benchmark_serving.py)，
[benchmark_utils.py](https://github.com/vllm-project/vllm/blob/v0.8.2/benchmarks/benchmark_utils.py)四个文件。在这四个文件的存放路径下执行以下命令启动性能测试。

```bash
python benchmark_serving.py --model /path/to/model/ --port xxxx --dataset_name random --random-input-len xx --random-output-len xxx --ignore-eos --num-prompts xx --request_rate xx
```

其中：

- --model为使用的模型路径，需要与启动vllm实例时设置的`/path/to/model/`一致。
- --port为路由器的对外端口。
- --dataset_name为进行性能测试时使用的数据集，random表示使用随机数据。
- --random-input-len为进行性能测试时随机输入的长度。
- --random-output-len为进行性能测试时输出的长度。
- --ignore-eos表示忽略停止符。
- --num-prompts为进行性能测试时发送的prompt数量。
- --request_rate为进行性能测试时发送的prompt的并发数。

执行benchmark性能测试的结果如下所示。

```text
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
