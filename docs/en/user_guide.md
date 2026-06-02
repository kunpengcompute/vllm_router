# User Guide

## Installing the Hardware and Software Environments for vLLM Inference

This document uses the following hardware environment as an example: new Kunpeng 920 processor model + eight MetaX C500 GPUs. After installing the driver, firmware, and vLLM inference container by following the instructions in the MetaX official [documentation](https://developer.metax-tech.com/api/client/document/preview/781/index.html), you can deploy vLLM-Router.

Currently, MetaX supports only Kylin V10 2309a and Kylin 2309 SP2 on AArch64 CPUs. Kylin V10 2309a uses the kernel `5.10.0-153.12.0.92.oe2203sp2.aarch64`. Therefore, when downloading the driver, firmware installation package, and Docker image, select the versions matching Kylin V10 2309a.

> **NOTE:** vLLM-Router is used as an external plugin of vLLM to add the parallel data deployment function. Therefore, there is no requirement on the actual hardware environment as long as the vLLM inference framework can be run. The preceding hardware environment using the new Kunpeng 920 processor model and MetaX C500 GPUs is for reference only.

## Starting vLLM Instances

- Access the MetaX inference container, and run the following commands in the current session to set visible GPUs and start the first vLLM instance.

  ```bash
  export CUDA_VISIBLE_DEVICES=0,1,2,3
  vllm serve /path/to/model/ --port <port> --trust_remote_code
  ```

  `xxxx` indicates the specified port number, for example, `8001`.

- Start another session in the same container, and run the following commands to set visible GPUs and start the second vLLM instance.

  ```bash
  export CUDA_VISIBLE_DEVICES=4,5,6,7
  vllm serve /path/to/model/ --port <port> --trust_remote_code
  ```
  
  `xxxx` indicates the specified port number, for example, `8002`.

- Start another session in the same container, and run the following command in the session to start the parallel data router.

  ```bash
  python launch_server.py --host 127.0.0.1 --port <port> --worker_urls http://127.0.0.1:<port> http://127.0.0.1:<port> --policy round_robin
  ```

  `port` indicates the external port of the router, for example, `8008`. `worker_urls` indicates the URLs of the started vLLM instances. `policy` indicates the routing policy. For details about the parameters, see [API Reference](./api_reference.md).

## Verifying the Installation

After the startup, you can run the benchmark test framework of vLLM.

Take vLLM 0.8.2 as an example. Download [backend_request_func.py](https://github.com/vllm-project/vllm/blob/v0.8.2/benchmarks/backend_request_func.py),
[benchmark_dataset.py](https://github.com/vllm-project/vllm/blob/v0.8.2/benchmarks/benchmark_dataset.py),
[benchmark_serving.py](https://github.com/vllm-project/vllm/blob/v0.8.2/benchmarks/benchmark_serving.py),
and [benchmark_utils.py](https://github.com/vllm-project/vllm/blob/v0.8.2/benchmarks/benchmark_utils.py). Run the following command in the directory where the four files are stored to start the performance test:

```bash
python benchmark_serving.py --model /path/to/model/ --port xxxx --dataset_name random --random-input-len xx --random-output-len xxx --ignore-eos --num-prompts xx --request_rate xx
```

In the preceding command:

- `--model` indicates the path to the model to be used, which must be the same as `/path/to/model/` set when the vLLM instances are started.
- `--port` indicates the external port of the router.
- `--dataset_name` indicates the dataset used for the performance test. `random` indicates that random data is used.
- `--random-input-len` indicates the length of the random input used for the performance test.
- `--random-output-len` indicates the output length used for the performance test.
- `--ignore-eos` indicates that the end symbol is ignored.
- `--num-prompts` indicates the number of prompts sent during the performance test.
- `--request_rate` indicates the number of concurrent prompts sent during the performance test.

The result of the benchmark test is as follows:

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
