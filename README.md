# vLLM-Router介绍

## 项目介绍

vLLM-Router是鲲鹏参与vLLM开源社区的路由插件，旨在支持数据并行部署，提供高性能的请求路由与负载均衡能力。该路由器内置多种负载均衡算法，包括前缀缓存感知、随机和轮询，可根据实际场景灵活选择，以优化系统整体性能。

其中前缀缓存功能能够将前缀相同的prompt发送给同一服务实例，结合vLLM的prefix-cache功能，能够大幅降低TTFT时延，提升端到端吞吐。

## 目录结构

```text
vllm-router/
├── router                    
│   ├── __init__.py        
│   └── protocol.py           # 路由接口定义
├── src                       # 源代码实现目录
│   ├── __init__.py        
│   ├── router.py             # 路由功能实现
│   └── tree.py               # 前缀缓存树实现
├── test                      
│   ├── online_test1.py       # 模拟第一个服务端
│   ├── online_test2.py       # 模拟第二个服务端
│   ├── test_router.py        # 路由功能单元测试
│   ├── test_server.py        # 服务端单元测试
│   └── test_tree.py          # 前缀缓存树单元测试
├── utils                     
│   ├── __init__.py            
│   ├── error.py              # 自定义错误
│   └── logger.py             # 日志
├── docs               
│   ├── api_reference.md      # api参考             
│   ├── menu_vllm_router.md   # 文档指南            
│   ├── release_notes.md      # 每个发布版本的基础信息和特性更新信息
│   └── user_guide.md         # 用户指南
├── LICENSE                   # 开源许可证文件
├── CC-BY                     # 开源文档许可证文件
├── README.md                 # 项目说明文档
├── launch_server.py          # 服务启动入口脚本
└── requirements.txt          # Python依赖列表文件

```

## 版本说明
vLLM-Router本身的版本说明，具体请参见[版本说明](./docs/zh/release_notes.md)。

## 学习文档
| 资源类别| 资源名称 | 资源简介|
| ------------ | ------------ |------------ |
|  文档 | [版本说明书](./docs/zh/release_notes.md) | 提供vLLM-Router每个发布版本的基础信息和特性更新信息。 |
|  文档 | [用户指南](./docs/zh/user_guide.md) | 提供vLLM-Router快速上手指导。 |
|  文档 | [API参考](./docs/zh/api_reference.md) | 提供vLLM-Router的接口说明。 |

## 通信矩阵

|  源设备 |  源IP地址 |  源端口 | 目的设备  | 目的IP地址  | 目的端口（侦听） | 协议 | 端口说明 | 侦听端口是否可更改 | 认证方式 | 加密方式 | 所属平面 | 版本 | 特殊场景 |
| ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ | ------------ |
| 用户所用服务器  | 用户服务器IP地址  | * | 运行vllm-router服务的服务器  |  运行vllm-router服务的IP地址 | 7000~9000 | HTTP/HTTPS | 接收用户推理请求 | 是 | N/A | N/A | 业务面 | 所有版本 | 无 |

## 贡献声明

欢迎大家为社区做贡献，如果使用过程中有任何问题/建议，或者需要反馈特性需求和bug报告，可以提交issues联系我们，具体贡献方法可参考[这里](https://gitcode.com/boostkit/community/blob/master/docs/contributor/contributing.md)。同时也欢迎大家在[讨论专区](https://gitcode.com/boostkit/community/discussions)展开讨论交流。感谢您的支持。

## 免责声明

此代码仓计划参与vLLM软件开源，为vLLM增加数据并行部署能力，编码风格遵照原生开源软件，继承原生开源软件安全设计，不破坏原生开源软件设计及编码风格和方式，软件的任何漏洞与安全问题，均由相应的上游社区根据其漏洞和安全响应机制解决。请密切关注上游社区发布的通知和版本更新。鲲鹏计算社区对软件的漏洞及安全问题不承担任何责任。

## 许可证书

本项目采用Apache License 2.0，详见[LICENSE](./LICENSE)文件
本项目文档适用CC-BY 4.0许可证，具体请参见[LICENSE](./CC-BY)文件。

## 致谢

vLLM-Router由华为公司的下列部门联合贡献：

鲲鹏计算Boostkit开发部

感谢来自社区的每一个PR，欢迎贡献vLLM-Router！
