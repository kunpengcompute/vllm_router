# Introduction to vLLM-Router

## Project Introduction

vLLM-Router is a routing plugin contributed by Kunpeng to the vLLM open-source community. It aims to support parallel data deployment and provide high-performance request routing and load balancing capabilities. This router includes multiple load balancing algorithms, such as prefix cache awareness, random, and round-robin, which can be flexibly chosen based on actual scenarios to optimize overall system performance.

Among these features, the prefix cache function can send prompts with the same prefix to the same service instance. Combined with vLLM's prefix-cache feature, this significantly reduces the time to first token (TTFT) and improves end-to-end throughput.

## Directory Structure

```text
vllm-router/
├── router                    
│   ├── __init__.py        
│   └── protocol.py              # Routing API definition
├── src                          # Source code implementation directory
│   ├── __init__.py        
│   ├── router.py                # Routing function implementation
│   └── tree.py                  # Prefix cache tree implementation
├── test                      
│   ├── online_test1.py          # Simulating the first server
│   ├── online_test2.py          # Simulating the second server
│   ├── test_router.py           # Routing function unit test
│   ├── test_server.py           # Server unit test
│   └── test_tree.py             # Prefix cache tree unit test
├── utils                     
│   ├── __init__.py            
│   ├── error.py                 # Customized errors
│   └── logger.py                # Log
├── docs 
|   └── en                       # Document directory
│      ├── api_reference.md      # API reference            
│      ├── menu_vllm_router.md   # Documentation guide           
│      ├── release_notes.md      # Basic information and feature updates of each released version
│      └── user_guide.md         # User guide
├── LICENSE                      # Open-source license file
├── CC-BY                        # Open-source document license file
├── README.md                    # Project description
├── launch_server.py             # Service startup entry script
└── requirements.txt             # Python dependency list

```

## Release Notes

For details about the vLLM-Router version description, see [Release Notes](./docs/en/release_notes.md).

## Documents

|  Resource Type|  Resource Name  |Resource Description|
| ------------ | ------------ |------------ |
|  Document| [API Reference](./docs/en/api_reference.md)| Provides the vLLM-Router API description.|
|  Document| [Release Notes](./docs/en/release_notes.md)| Provides basic information and feature updates of each vLLM-Router version.|
|  Document| [User Guide](./docs/en/user_guide.md)| Provides a quick start guide for vLLM-Router.|

## Communication Matrix

|  Source Device|  Source IP Address|  Source Port| Destination Device | Destination IP Address | Destination Port (Listening)| Protocol| Port Description| Listening Port Configurable (Yes/No)| Authentication Mode| Encryption Mode| Plane| Version| Special Scenario|
| ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ |
| User server | IP address of the user server | * | Server running the vLLM-Router service |  IP address of the server running the vLLM-Router service| 7000–9000 | HTTP/HTTPS | Receiving inference requests from users| Yes| N/A | N/A | Service plane| All| None|

## Contribution Statement

We welcome your contributions to the community. If you have any questions/suggestions or want to provide feedback on feature requirements and bug reports, you can submit issues. For details, see the [contribution guideline](https://gitcode.com/boostkit/community/blob/master/docs/contributor/contributing.md). You are also welcome to share insights in the [Discussions](https://gitcode.com/boostkit/community/discussions). Thank you for your support.

## Disclaimer

This code repository contributes to the vLLM open-source project by adding the parallel data deployment capability to vLLM. It strictly adheres to the coding style and methods, as well as security design of the native open-source software. Any vulnerability and security issues of the software shall be resolved by the corresponding upstream communities according to their response mechanisms. Please pay attention to the notifications and version updates released by the upstream communities. The Kunpeng computing community does not assume any responsibility for software vulnerabilities and security issues.

## License

This project is released under the Apache License 2.0. For details, see [LICENSE](./LICENSE).
The documents of this project are licensed under CC-BY 4.0. For details, see [CC-BY](./CC-BY).

## Acknowledgments

vLLM-Router is jointly developed by the following Huawei department:

Kunpeng Computing BoostKit Development Dept

Thank you to everyone in the community for your PRs. We warmly welcome contributions to vLLM-Router!
