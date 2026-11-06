FROM swr.cn-north-4.myhuaweicloud.com/cloud_boostkit/openeuler22.03_lts_sp3:latest

# ---- 镜像获取方式 (二选一) ----
# 方式一 自行构建: 在仓库根目录（含 requirements.txt）执行
#   docker build -t vllm-router:1.0.0 .
# 方式二 直接拉取: 使用已按本 Dockerfile 构建并推送好的镜像, 无需再构建
#   docker pull swr.cn-north-4.myhuaweicloud.com/cloud_boostkit/openeuler22.03_lts_sp3:vllm-router_26.3.0.1

# ---- 0. 系统依赖 + Python 运行时 + tzdata ----
# tzdata 必装: src/tree.py:401 使用 ZoneInfo("Asia/Shanghai"), 缺少会导致 8 个 UT 失败
RUN dnf update -y && dnf install -y \
        python3 python3-pip \
        tzdata \
        git curl wget \
        ca-certificates \
    && dnf clean all \
    && python3 --version \
    && pip3 --version

# ---- 1. 设置 pip 阿里云镜像 (清华源对部分 wheel 返回 403) ----
RUN pip3 config set global.index-url https://mirrors.aliyun.com/pypi/simple/ \
    && pip3 config set global.trusted-host mirrors.aliyun.com \
    && pip3 config list

# ---- 2. 安装 Python 依赖 ----
# requirements.txt: pydantic>=2.5.3, aiohttp>=3.9.5, fastapi>=0.115.12
# uvicorn: launch_server.py/online_test*.py 的 ASGI 服务器
# pytest: UT 驱动框架 (test_*.py 使用 unittest.TestCase)
# 直接内联依赖, 无需构建上下文提供 requirements.txt
RUN pip3 install --no-cache-dir \
        "pydantic>=2.5.3" \
        "aiohttp>=3.9.5" \
        "fastapi>=0.115.12" \
        uvicorn \
        pytest \
    && python3 -c "import pydantic, aiohttp, fastapi; print('pydantic', pydantic.VERSION, '| aiohttp', aiohttp.__version__, '| fastapi', fastapi.__version__)" \
    && pytest --version

# ---- 3. 验证时区数据可用 (ZoneInfo Asia/Shanghai) ----
RUN python3 -c "from zoneinfo import ZoneInfo; from datetime import datetime; print(datetime.fromtimestamp(1700000000, tz=ZoneInfo('Asia/Shanghai')))"

# ---- 4. 工作目录 ----
WORKDIR /workspace

# ---- 默认 CMD: 交互式 shell ----
CMD ["/bin/bash"]
