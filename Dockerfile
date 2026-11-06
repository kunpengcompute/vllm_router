FROM swr.cn-north-4.myhuaweicloud.com/cloud_boostkit/openeuler22.03_lts_sp3:latest

# ---- 镜像获取方式 (二选一) ----
# 方式一 自行构建: 在仓库根目录（含 requirements.txt）执行
#   docker build -t vllm-router:1.0.0 .
# 方式二 直接拉取: 使用已按本 Dockerfile 构建并推送好的镜像, 无需再构建
#   docker pull swr.cn-north-4.myhuaweicloud.com/cloud_boostkit/openeuler22.03_lts_sp3:vllm-router_26.3.0.1

# ---- 0. 系统依赖 + Python 编译工具链 + tzdata ----
# tzdata 必装: src/tree.py:401 使用 ZoneInfo("Asia/Shanghai"), 缺少会导致 8 个 UT 失败
# 编译工具链必装: openEuler 22.03 LTS SP3 自带 python3 为 3.9.9, 不满足 vLLM-Router 的 Python>=3.10 要求, 需从源码编译 3.10
RUN dnf update -y && dnf install -y \
        gcc gcc-c++ make \
        zlib-devel bzip2-devel readline-devel sqlite-devel \
        libffi-devel openssl-devel xz-devel tk-devel \
        tzdata \
        git curl wget \
        ca-certificates \
    && dnf clean all \
    && gcc --version

# ---- 1. 从源码编译安装 Python 3.10.15 (华为云镜像) ----
# openEuler 22.03 LTS SP3 dnf 仓库不提供 python3.10, 需源码编译
# 使用 --enable-shared 以便 uvicorn 等 C 扩展正常工作
# 阿里云镜像路径 python-release/python-3.10.15.tgz 返回 404 (文件名应为大写 Python-), 改用华为云镜像
ENV PYTHON_ROOT=/opt/python3.10
ENV PATH="${PYTHON_ROOT}/bin:${PATH}"
ENV LD_LIBRARY_PATH="${PYTHON_ROOT}/lib:${LD_LIBRARY_PATH}"
RUN cd /tmp \
    && curl -fsSL https://mirrors.huaweicloud.com/python/3.10.15/Python-3.10.15.tgz -o python.tgz \
    && tar xzf python.tgz \
    && cd Python-3.10.15 \
    && ./configure --prefix=${PYTHON_ROOT} --enable-shared --with-system-ffi --with-openssl=/usr LDFLAGS="-Wl,-rpath=${PYTHON_ROOT}/lib" \
    && make -j$(nproc) \
    && make install \
    && cd / && rm -rf /tmp/python.tgz /tmp/Python-3.10.15 \
    && ln -sf ${PYTHON_ROOT}/bin/python3.10 /usr/local/bin/python3 \
    && ln -sf ${PYTHON_ROOT}/bin/pip3.10 /usr/local/bin/pip3 \
    && python3 --version \
    && pip3 --version

# ---- 2. 设置 pip 阿里云镜像 (清华源对部分 wheel 返回 403) ----
RUN pip3 config set global.index-url https://mirrors.aliyun.com/pypi/simple/ \
    && pip3 config set global.trusted-host mirrors.aliyun.com \
    && pip3 config list

# ---- 3. 安装 Python 依赖 ----
# requirements.txt: pydantic>=2.5.3, aiohttp>=3.9.5, fastapi>=0.115.12
# uvicorn: launch_server.py/online_test*.py 的 ASGI 服务器
# pytest: UT 驱动框架 (test_*.py 使用 unittest.TestCase)
# 直接内联依赖, 无需构建上下文提供 requirements.txt
# 使用 python3 -m pytest 避免 pytest 可执行脚本不在 PATH 中
RUN pip3 install --no-cache-dir \
        "pydantic>=2.5.3" \
        "aiohttp>=3.9.5" \
        "fastapi>=0.115.12" \
        uvicorn \
        pytest \
    && python3 -c "import pydantic, aiohttp, fastapi; print('pydantic', pydantic.VERSION, '| aiohttp', aiohttp.__version__, '| fastapi', fastapi.__version__)" \
    && python3 -m pytest --version

# ---- 4. 验证时区数据可用 (ZoneInfo Asia/Shanghai) ----
RUN python3 -c "from zoneinfo import ZoneInfo; from datetime import datetime; print(datetime.fromtimestamp(1700000000, tz=ZoneInfo('Asia/Shanghai')))"

# ---- 5. 工作目录 ----
WORKDIR /workspace

# ---- 默认 CMD: 交互式 shell ----
CMD ["/bin/bash"]
