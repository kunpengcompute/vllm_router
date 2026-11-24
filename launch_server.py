#  Copyright (c) 2026 Huawei Technologies Co., Ltd.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

import argparse
import asyncio
import copy
import ipaddress
import json
import logging
import os
import socket
import time
from asyncio import Semaphore
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from typing import Dict, List, Set, Tuple
from urllib.parse import urlparse

import aiohttp
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.requests import Request

from router.protocol import CompletionRequest, RouterArgs, WorkUrls
from src.router import (
    CacheAwareConfig,
    CacheAwareRouter,
    PolicyConfig,
    RandomConfig,
    RandomRouter,
    RoundRobinConfig,
    RoundRobinRouter,
    RouteSelector,
)
from utils.config import CapacityConfig
from utils.error import NoAvailableWorkerError
from utils.logger import logger

# Maximum number of worker URLs accepted in a single registration request.
#
# Every registered worker is polled by the health monitor, so an unbounded
# batch size turns the registration endpoint into an amplifier for the
# outbound traffic and for the worker pool.
#
# The limit is configurable so that it can be adapted to a deployment, but the
# default keeps the limit that the request model already enforced (WorkUrls
# accepts at most 1000 URLs per request). Lowering the default would be an
# API compatibility change and does not belong into a fix for a single
# vulnerability.
MAX_WORKER_URLS_PER_REQUEST_ENV = "VLLM_ROUTER_MAX_WORKER_URLS_PER_REQUEST"
MAX_WORKER_URLS_PER_REQUEST = 1000

_LOCAL_ADDRESSES_CACHE: Set[str] | None = None


def _env_int(name: str, default: int) -> int:
    """Read a positive integer from the environment.

    An invalid or non positive value falls back to the default: a
    misconfigured environment variable must not take the router down.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("Ignoring invalid value %r for %s, using %s", raw, name, default)
        return default
    if value <= 0:
        logger.warning("Ignoring non positive value %r for %s, using %s", raw, name, default)
        return default
    return value


def _local_addresses() -> Set[str]:
    """Return the addresses that belong to this host."""
    global _LOCAL_ADDRESSES_CACHE
    if _LOCAL_ADDRESSES_CACHE is not None:
        return _LOCAL_ADDRESSES_CACHE

    addresses: Set[str] = set()
    for candidate in (socket.gethostname(), "127.0.0.1", "::1"):
        try:
            for info in socket.getaddrinfo(candidate, None):
                sockaddr = info[4]
                if sockaddr and sockaddr[0]:
                    addresses.add(sockaddr[0])
        except OSError:
            continue
    _LOCAL_ADDRESSES_CACHE = addresses
    return addresses


def _resolve_addresses_sync(host: str, port: int) -> List[str]:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return []
    return [info[4][0] for info in infos if info[4] and info[4][0]]


def normalize_worker_url(url: str) -> str:
    """Normalise a worker URL so that the same worker is registered once.

    Without this, ``http://worker:8001`` and ``http://worker:8001/`` are two
    different keys in the worker pool, and the second one produces malformed
    request paths such as ``http://worker:8001//health``.

    The registration path and the removal path use this very same function,
    so that a worker that was registered as ``http://worker:8001/`` can also
    be removed with that spelling.
    """
    return url.strip().rstrip("/")


def normalize_worker_urls(urls: List[str]) -> List[str]:
    """Normalise and de-duplicate a list of worker URLs, keeping the order."""
    normalized: List[str] = []
    for url in urls:
        candidate = normalize_worker_url(url)
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return normalized


def parse_endpoint(url: str):
    """Return ``(hostname, port)`` of a URL, or ``None`` when it is malformed.

    ``urlparse`` raises a ValueError for an unterminated IPv6 literal, and
    the ``port`` and ``hostname`` properties raise a ValueError for a non
    numeric or an out of range port. Every access has to be guarded: a
    malformed worker URL must be rejected, it must not turn the registration
    endpoint into an internal server error.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None

    if not hostname:
        return None

    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    return hostname.lower(), port


def _is_local_address(address: str) -> bool:
    """Return True when the address points at this very host."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False

    # "::ffff:127.0.0.1" is the IPv4 loopback in its IPv6 notation.
    mapped = getattr(parsed, "ipv4_mapped", None)
    if mapped is not None:
        parsed = mapped

    # The unspecified address ("0.0.0.0", "::") reaches every local socket.
    if parsed.is_loopback or parsed.is_unspecified:
        return True

    return address in _local_addresses()


async def is_router_self_reference(url: str, router_port: int) -> bool:
    """Return True when ``url`` points back at this very router instance.

    The router binds ``0.0.0.0`` by default, so every local address reaches
    it. Registering the router as its own worker makes the inference
    forwarding recurse into the router itself: ``/v1/completions`` is
    forwarded to the router, which forwards it again, exhausting the
    connection pool and the event loop (denial of service).
    """
    if not router_port:
        return False

    endpoint = parse_endpoint(url)
    if endpoint is None:
        # A malformed URL is not a self reference. The request model rejects
        # it later on, this check must not raise.
        return False

    hostname, port = endpoint
    if port != router_port:
        return False

    loop = asyncio.get_running_loop()
    addresses = await loop.run_in_executor(None, _resolve_addresses_sync, hostname, port)
    if not addresses:
        # Nothing to compare against, the health check will decide.
        return False

    return any(_is_local_address(address) for address in addresses)


class CustomHelpFormatter(argparse.RawDescriptionHelpFormatter, argparse.ArgumentDefaultsHelpFormatter):
    """Custom formatter that preserves both description formatting and shows defaults"""

    pass


def parse_router_args() -> argparse.Namespace:
    """Parse command line arguments and return RouterArgs instance."""
    parser = argparse.ArgumentParser(
        description="""High-performance request distribution across worker nodes

Usage:
This launcher enables starting a router with individual worker instances. It is useful for
multi-node setups or when you want to start workers and router separately.

Examples:
  python -m launch_router --worker-urls http://worker1:xxxx http://worker2:xxxx
  python -m launch_router --worker-urls http://worker1:xxxx http://worker2:xxxx --cache-threshold 0.7 --balance-abs-threshold 64 --balance-rel-threshold 1.2

    """,
        formatter_class=CustomHelpFormatter,
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host address to bind the router server",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8008,
        help="Port number to bind the router server",
    )
    parser.add_argument(
        "--worker-urls",
        "--worker_urls",
        dest="worker_urls",
        type=str,
        default=["http://127.0.0.1:8001", "http://127.0.0.1:8002"],
        nargs="+",
        help="List of worker URLs (e.g., http://worker1:xxxx http://worker2:xxxx)",
    )

    # Routing policy configuration
    parser.add_argument(
        "--policy",
        type=str,
        default="cache_aware",
        choices=["random", "round_robin", "cache_aware"],
        help="Load balancing policy to use",
    )
    parser.add_argument(
        "--worker-startup-timeout-secs",
        "--worker_startup_timeout_secs",
        dest="worker_startup_timeout_secs",
        type=int,
        default=30,
        help="Timeout in seconds for worker startup",
    )
    parser.add_argument(
        "--worker-startup-check-interval",
        "--worker_startup_check_interval",
        dest="worker_startup_check_interval",
        type=int,
        default=1,
        help="Interval in seconds between checks for worker startup",
    )
    parser.add_argument(
        "--cache-threshold",
        "--cache_threshold",
        dest="cache_threshold",
        type=float,
        default=0.5,
        help="Cache threshold (0.0-1.0) for cache-aware routing",
    )
    parser.add_argument(
        "--balance-abs-threshold",
        "--balance_abs_threshold",
        dest="balance_abs_threshold",
        type=int,
        default=32,
        help="Load balancing is triggered when (max_load - min_load) > abs_threshold AND max_load > min_load * rel_threshold. Otherwise, use cache aware",
    )
    parser.add_argument(
        "--balance-rel-threshold",
        "--balance_rel_threshold",
        dest="balance_rel_threshold",
        type=float,
        default=1.0001,
        help="Load balancing is triggered when (max_load - min_load) > abs_threshold AND max_load > min_load * rel_threshold. Otherwise, use cache aware",
    )
    parser.add_argument(
        "--eviction-interval-secs",
        "--eviction_interval_secs",
        dest="eviction_interval_secs",
        type=int,
        default=60,
        help="Interval in seconds between cache eviction operations",
    )
    parser.add_argument(
        "--max-tree-size",
        "--max_tree_size",
        dest="max_tree_size",
        type=int,
        # The limit is resolved by the capacity configuration, so that it can
        # also be set with an environment variable.
        default=argparse.SUPPRESS,
        help=(
            "Maximum number of characters a single worker may keep in the prefix cache tree "
            "(0 disables the limit). Default: the value of VLLM_ROUTER_MAX_TREE_SIZE or 16777216"
        ),
    )
    parser.add_argument(
        "--max-tree-total-size",
        "--max_tree_total_size",
        dest="max_tree_total_size",
        type=int,
        # The limit is resolved by the capacity configuration, so that it can
        # also be set with an environment variable.
        default=argparse.SUPPRESS,
        help=(
            "Maximum number of characters all workers together may keep in the prefix cache tree "
            "(0 disables the limit). Default: the value of VLLM_ROUTER_MAX_TREE_TOTAL_SIZE or 16777216"
        ),
    )
    parser.add_argument(
        "--max-tree-nodes",
        "--max_tree_nodes",
        dest="max_tree_nodes",
        type=int,
        # The limit is resolved by the capacity configuration, so that it can
        # also be set with an environment variable.
        default=argparse.SUPPRESS,
        help=(
            "Maximum number of nodes of the prefix cache tree (0 disables the limit). "
            "Default: the value of VLLM_ROUTER_MAX_TREE_NODES or 16777216"
        ),
    )
    parser.add_argument(
        "--max-cache-depth",
        "--max_cache_depth",
        dest="max_cache_depth",
        type=int,
        # The limit is resolved by the capacity configuration, so that it can
        # also be set with an environment variable.
        default=argparse.SUPPRESS,
        help=(
            "Maximum length of a chain in the prefix cache tree (0 disables the limit). "
            "Default: the value of VLLM_ROUTER_MAX_CACHE_DEPTH or 64"
        ),
    )
    parser.add_argument(
        "--max-cache-text-length",
        "--max_cache_text_length",
        dest="max_cache_text_length",
        type=int,
        # The limit is resolved by the capacity configuration, so that it can
        # also be set with an environment variable.
        default=argparse.SUPPRESS,
        help=(
            "Maximum number of characters of a request that are stored in the prefix cache tree "
            "(0 disables the limit). Default: the value of VLLM_ROUTER_MAX_CACHE_TEXT_LENGTH or 4096"
        ),
    )
    parser.add_argument(
        "--max-workers",
        "--max_workers",
        dest="max_workers",
        type=int,
        # The limit is resolved by the capacity configuration, so that it can
        # also be set with an environment variable.
        default=argparse.SUPPRESS,
        help=(
            "Maximum number of worker instances that may be registered at the same time "
            "(0 disables the limit). Default: the value of VLLM_ROUTER_MAX_WORKERS or 256"
        ),
    )
    parser.add_argument(
        "--log-dir",
        "--log_dir",
        dest="log_dir",
        type=str,
        default="",
        help="Directory to store log files",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    return args


def setup_logger(log_file: str, verbose: bool = False) -> logging.Logger:
    if logger.handlers:
        return logger

    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(
        "[Router (Python)] %(asctime)s - %(levelname)s - %(message)s - %(filename)s:%(lineno)d",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        MAX_BYTES = 10 * 1024 * 1024  # 10 MB

        file_handler = RotatingFileHandler(log_file, maxBytes=MAX_BYTES, backupCount=0, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        try:
            os.chmod(log_file, 0o640)
        except FileNotFoundError:
            _original_emit = file_handler._original_emit

            def emit_with_chmod(record):
                _original_emit(record)
                if os.path.exists(log_file):
                    try:
                        os.chmod(log_file, 0o640)
                        file_handler.emit = _original_emit
                    except OSError:
                        pass

            file_handler.emit = emit_with_chmod

    return logger


class BackendService:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.request_count = 0
        self.health = True
        self.health_check_interval = 30  # seconds

    async def health_check(self, session: aiohttp.ClientSession) -> bool:
        if session.closed:
            logger.warning(f"Health check skipped for {self.base_url}: session is closed")
            return False

        try:
            async with session.get(f"{self.base_url}/health") as resp:
                if resp.status == 200:
                    return True
                return False

        except aiohttp.ClientConnectorError as e:
            # 连接类错误（DNS、拒绝连接、网络不通等）
            logger.warning(
                "Health check connection failed", extra={"base_url": self.base_url, "error": str(e)}, exc_info=True
            )
            return False

        except aiohttp.ServerTimeoutError:
            # 超时（读/写/连接超时）
            logger.warning("Health check timed out", extra={"base_url": self.base_url})
            return False

        except (aiohttp.InvalidURL, ValueError) as e:
            # URL 无效（配置错误）
            logger.critical(
                "Health check failed due to invalid URL", extra={"base_url": self.base_url, "error": str(e)}
            )
            return False

        except Exception as e:
            logger.error(f"Health check failed for {self.base_url}: {str(e)}")
            return False


class Router:
    def __init__(
        self,
        capacity: Optional[CapacityConfig] = None,
    ):
        self.router = None
        # Every resource limit of the router, see utils/config.py.
        self.capacity = capacity or CapacityConfig()
        self.router_selector = RouteSelector()
        self.backends = {}
        self.session_timeout = aiohttp.ClientTimeout(
            total=None,  # 长连接支持
            sock_connect=10,  # 连接建立超时
            sock_read=None,  # 单次读取超时
            connect=10,
        )
        self.session = aiohttp.ClientSession(timeout=self.session_timeout)
        self.lock = asyncio.Lock()
        self._monitor_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()  # 用于通知任务退出
        self._start_background_tasks()
        self.timeout_secs = 999999
        self.interval_secs = 999999

    async def init_router(self, worker_urls: str | List[str], policy_config: PolicyConfig):
        if worker_urls:
            add_urls_succeed, add_urls_failed, msg = await self.add_workers(worker_urls)

            logger.info(msg)
            if len(add_urls_succeed) > 0:
                self.router = self.router_selector.create_router(add_urls_succeed, policy_config)
                self.timeout_secs = policy_config.timeout_secs
                self.interval_secs = policy_config.interval_secs
            else:
                logger.error(msg)
                await self.session.close()
                raise NoAvailableWorkerError
        else:
            logger.warning("Need urls")

    @staticmethod
    async def wait_for_healthy_worker(worker_url: str, timeout_secs: int, interval_secs: int) -> bool:
        """
        Wait worker responds to health check
        Raises:
            RuntimeError: If worker doesn't become healthy within timeout
        """
        start_time = time.time()

        # A single session is used for the whole wait loop. It used to be
        # closed inside the loop, which made every retry after the first one
        # fail with "Session is closed".
        async with aiohttp.ClientSession(timeout=HEALTH_CHECK_TIMEOUT) as client:
            while True:
                # Check timeout
                if time.time() - start_time > timeout_secs:
                    error_msg = (
                        f"Timeout {timeout_secs}s waiting for workers {worker_url} to become healthy. "
                        "Please set --worker-startup-timeout-secs (router.launch_server) "
                        "or --worker-startup-timeout-secs (worker.router) to a larger value"
                    )
                    logger.error(error_msg)
                    is_healthy = False
                    return is_healthy

                # Check health status
                is_healthy = True
                unhealthy_worker: str = ""

                health_url = f"{worker_url}/health"
                try:
                    async with client.get(health_url) as resp:
                        if resp.status != 200:
                            status_msg = f"Worker health check failed with status {resp.status}"
                            logger.info(status_msg)
                            is_healthy = False
                            unhealthy_worker = status_msg
                except aiohttp.client_exceptions.ClientConnectorError as e:
                    conn_msg = f"Worker connection error: {str(e)}"
                    logger.info(conn_msg)
                    is_healthy = False
                    unhealthy_worker = conn_msg
                except Exception as e:
                    err_msg = f"Worker health check error: {str(e)}"
                    logger.info(err_msg)
                    is_healthy = False
                    unhealthy_worker = err_msg
                # Handle health check results
                if is_healthy:
                    logger.info(f"{worker_url} worker is healthy")
                    break
                else:
                    logger.info("Initializing worker:")
                    logger.info(f"  {worker_url} - {unhealthy_worker}")
                    # Yield control instead of blocking the whole event loop.
                    # A blocking sleep here lets every unreachable worker stall
                    # the router for the whole interval.
                    await asyncio.sleep(interval_secs)

        return is_healthy

    async def add_workers(self, urls: str | List[str]) -> Tuple[List[str], List[str], str]:
        if isinstance(urls, str):
            urls = [urls]

        # Normalise here and not only in the registration endpoint: the
        # workers that are passed on the command line go through the same
        # path, and "http://worker:8001" and "http://worker:8001/" have to
        # be the same worker in the pool.
        urls = normalize_worker_urls(urls)

        # 分离已存在和新的 URL
        existing_urls = []
        new_urls = []
        for url in urls:
            if url in self.backends:
                existing_urls.append(url)
            else:
                new_urls.append(url)

        if existing_urls:
            conn_msg = f"{existing_urls} already exist."
            logger.info(conn_msg)

        # 并行检查新 URL 的健康状态
        health_results = await self.batch_check_url_health(new_urls)

        # 处理结果
        add_urls_succeed = []
        add_urls_failed = existing_urls.copy()  # 已存在的 URL 视为失败

        # The quota is enforced while the workers are committed, not only
        # before the health check: two concurrent registrations could both
        # pass the check above and together push the router over the quota.
        # The lock serialises the check and the commit, the second
        # registration sees the workers of the first one.
        async with self.lock:
            for url, is_healthy in health_results.items():
                if not is_healthy:
                    add_urls_failed.append(url)
                    continue

                # self.backends already contains the workers that were
                # committed by this request, so it is the number of workers
                # that are registered right now.
                if max_workers and len(self.backends) >= max_workers:
                    logger.warning(
                        "Worker limit reached: %s of %s workers are already registered, %s is rejected",
                        len(self.backends),
                        max_workers,
                        url,
                    )
                    add_urls_failed.append(url)
                    continue

                backend = BackendService(url)
                self.backends[url] = backend
                add_urls_succeed.append(url)

                if self.router:
                    await self.router_selector.add_worker(url, self.router)

        # 生成结果消息
        res_parts = []
        if add_urls_succeed:
            res_parts.append(f"add {add_urls_succeed} server succeed.")
        if add_urls_failed:
            res_parts.append(f"add {add_urls_failed} server failed.")

        return add_urls_succeed, add_urls_failed, " ".join(res_parts)

    async def batch_check_url_health(self, urls: List[str], max_concurrency=10) -> Dict[str, bool]:
        if not urls:
            return {}

        semaphore = Semaphore(max_concurrency)

        async def check_with_semaphore(url):
            async with semaphore:
                try:
                    return await asyncio.wait_for(self.wait_for_healthy_worker(url, 5, 1), timeout=10)
                except (asyncio.TimeoutError, Exception):
                    return False

        tasks = [check_with_semaphore(url) for url in urls]

        checked_results = await asyncio.gather(*tasks)
        results = {url: is_healthy for url, is_healthy in zip(urls, checked_results)}

        return results

    def remove_workers(self, urls: str | List[str]) -> str:
        if isinstance(urls, str):
            urls = [urls]

        # The same normalisation as in the registration path: a worker that
        # was registered as "http://worker:8001/" is found again when it is
        # removed with that spelling.
        urls = normalize_worker_urls(urls)

        delete_urls_succeed = []
        delete_urls_failed = []

        for url in urls:
            if url in self.backends:
                self.backends.pop(url)
                delete_urls_succeed.append(url)
                self.router_selector.remove_worker(url, self.router)
            else:
                delete_urls_failed.append(url)
                logger.info(f"Worker {url} not found, skipping removal")
        res = ""
        if delete_urls_succeed:
            res = f"delete {delete_urls_succeed} server succeed. "
        if delete_urls_failed:
            res += f"delete {delete_urls_failed} server failed."
        return res

    def _start_background_tasks(self):
        """启动后台任务，并保存引用"""
        if self._monitor_task is None or self._monitor_task.done():
            self._shutdown_event.clear()
            self._monitor_task = asyncio.create_task(self._monitor_services())

    async def _monitor_services(self):
        temp_backends = copy.deepcopy(self.backends)
        while not self._shutdown_event.is_set():
            try:
                async with self.lock:
                    if self.session.closed:
                        logger.warning("Session closed, stopping health monitor.")
                        break

                    for _, backend in self.backends.items():
                        is_healthy = await backend.health_check(self.session)
                        if is_healthy:
                            backend.health = True
                            if backend.base_url not in temp_backends:
                                temp_backends[backend.base_url] = backend
                                self.backends[backend.base_url] = backend
                                self.router_selector.update_router(backend.base_url, self.router, add=True)
                        else:
                            backend.health = False
                            if backend.base_url in temp_backends:
                                temp_backends.pop(backend.base_url)
                                self.router_selector.update_router(backend.base_url, self.router, add=False)
                        logger.info(f"Service {backend.base_url} health: {is_healthy}")
            except asyncio.CancelledError:
                logger.info("Health monitor task was cancelled.")
                break
            except Exception as e:
                logger.error(f"Unexpected error in health monitor: {e}", exc_info=True)
            finally:
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=3)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break

    async def send_request(self, worker_url: str, api: str):
        target_url = f"{worker_url}{api}"
        try:
            if api != "/health":
                headers = {"Content-Type": "application/json"}
                async with self.session.post(
                    target_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    # 读取响应内容
                    body = await response.read()
                    return response.status, body
            else:
                headers = {}
                async with self.session.get(
                    target_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    body = await response.read()
                    return response.status, body

        except aiohttp.ClientError as e:
            error_msg = f"Failed to send request to worker {worker_url}: {str(e)}"
            return 500, error_msg.encode()

        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            return 500, error_msg.encode()

    async def send_generate_request(self, worker_url, api, body: dict = None):
        content_type = "Content-Type"
        headers = {
            "Content-Type": "application/json",
        }
        is_stream = body.get("stream", False)

        url = f"{worker_url}/{api}"

        if not is_stream:
            async with self.session.post(url, json=body, headers=headers) as response:
                status = response.status
                content_type = response.headers.get(content_type, "")
                full_body = await response.read()
                if isinstance(self.router, CacheAwareRouter):
                    self.router.running_queue[worker_url] -= 1
                return Response(content=full_body, status_code=status, headers=response.headers)
        elif isinstance(self.router, CacheAwareRouter):

            async def stream_gen():
                # 流式响应需要在内部定义会话
                async with aiohttp.ClientSession(timeout=self.session_timeout) as session:
                    async with session.post(url, json=body) as response:
                        done_marker = b"data: [DONE]"
                        async for chunk in response.content.iter_chunked(64 * 1024):
                            yield chunk
                            if done_marker in chunk:
                                self.router.running_queue[worker_url] -= 1
                                logger.debug("Streaming is done!!")

            return StreamingResponse(stream_gen(), media_type="text/event-stream")
        else:

            async def stream_gen():
                async with aiohttp.ClientSession(timeout=self.session_timeout) as session:
                    async with session.post(url, json=body) as response:
                        async for chunk in response.content.iter_chunked(64 * 1024):
                            yield chunk

            return StreamingResponse(stream_gen(), media_type="text/event-stream")

    @staticmethod
    def extract_request_text(body, api: str) -> str:
        """Extract the raw text of a request, without truncating it."""
        if api == "generate":
            # For "/generate", always use the "text" field.
            value = body.get("text")
            if isinstance(value, str):
                return value
            logger.warning("No 'text' field found in request body for /generate")
            return ""

        if api in ("v1/completions",):
            # try "messages", then "prompt", then "text".
            if messages := body.get("messages"):
                return json.dumps(messages)
            if prompt := body.get("prompt"):
                return str(prompt)
            logger.warning("Missing 'messages' or 'prompt' in request body")
            return ""

        logger.warning(f"Unknown route: {api} - using fallback")
        return ""

    def get_text_from_request(self, body, api: str) -> str:
        """Extract the text that is used for the cache aware routing.

        The text is truncated to the configured maximum length. Only a
        bounded prefix of a prompt is relevant for the routing decision, but
        an unbounded text is stored in the prefix cache tree and would let a
        client blow up the memory of the router (CWE-400).

        The limit is the very same one that the tree uses, so that the
        request path and the tree always agree on how much of a prompt is
        cached. Before, the request path truncated with a hard coded value
        while the tree used a configurable one, so a prompt could be cached
        completely or not at all depending on the path it took.
        """
        text = self.extract_request_text(body, api)

        max_length = self.capacity.max_cache_text_length
        if max_length and len(text) > max_length:
            text = text[:max_length]
        return text

    def select_generate_worker(self, body, api: str) -> str:
        if isinstance(self.router, (RoundRobinRouter, RandomRouter)):
            return self.router.select_generate_worker()
        elif isinstance(self.router, CacheAwareRouter):
            text = self.get_text_from_request(body, api)
            return self.router.select_generate_worker(text)

    async def route_generate_request(self, body, api):
        max_request_retries = 3
        max_total_retries = 6
        total_retries = 0

        while total_retries < max_total_retries:
            worker_url = self.select_generate_worker(body, api)
            request_retries = 0
            # Try the same worker multiple times
            while request_retries < max_request_retries:
                if total_retries >= 1:
                    logger.info(f"Retrying request after {total_retries} failed attempts")

                response = await self.send_generate_request(worker_url, api, body)

                if response.status_code == 200:
                    return response
                else:
                    # if the worker is healthy, it means the request is bad, so return the error response
                    health_response = await self.send_request(worker_url, "/health")
                    if health_response[0] == 200:
                        return Response(
                            content=response.body, status_code=response.status_code, headers=response.headers
                        )

                logger.warning(
                    f"Generate request to {worker_url} failed (attempt {request_retries + 1}/{max_request_retries})"
                )

                request_retries += 1
                total_retries += 1

                if request_retries == max_request_retries:
                    logger.warning(f"Removing failed worker: {worker_url}")
                    self.router_selector.remove_worker(worker_url, self.router)
                    break
        return Response(status_code=500, content="All retry attempts failed")

    async def forward_request(self, body, api):
        if api not in ("v1/completions",):
            return JSONResponse(status_code=500, content={"detail": "Incorrect API."})
        return await self.route_generate_request(body, api)

    @staticmethod
    async def async_deepcopy(obj):
        """异步执行深拷贝（避免阻塞事件循环）"""
        loop = asyncio.get_running_loop()
        # 在线程池中执行阻塞操作
        return await loop.run_in_executor(None, copy.deepcopy, obj)

    async def get_worker_urls(self) -> List[str]:
        """Get a deep copy of the worker URLs for thread safety"""
        async with self.lock:
            return await self.async_deepcopy(set(self.router.worker_urls))


def make_lifespan(worker_urls: list, policy_config, capacity: Optional[CapacityConfig] = None):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.router = Router(capacity=capacity)
        await app.router.init_router(worker_urls, policy_config)
        logger.info("Load router initialized")
        yield
        await app.router.session.close()
        logger.info("Load router resources released")

    return lifespan


def create_app(worker_urls, policy_config, router_port: int = 0) -> FastAPI:
    app = FastAPI(lifespan=make_lifespan(worker_urls, policy_config))

    @app.get("/health")
    async def alive():
        print("收到测活请求")
        return "OK"

    @app.post("/v1/completions")
    async def generate(body: CompletionRequest, request: Request):
        body = body.model_dump()
        res = await app.router.forward_request(body, "v1/completions")
        return res

    @app.post("/add_workers")
    async def add_workers(body: WorkUrls, request: Request) -> Response:
        body = body.model_dump()
        urls = body.get("urls")
        if not urls:
            return Response(content="Worker URL required. Provide 'url' query parameter", status_code=400)

        urls_list = [urls] if isinstance(urls, str) else list(urls)

        # Bound the batch size: every registered worker is polled by the
        # health monitor every 3 seconds, so an unbounded batch turns the
        # registration endpoint into an outbound traffic amplifier. The limit
        # is configurable, its default keeps the limit that the request model
        # already enforced.
        max_urls = _env_int(MAX_WORKER_URLS_PER_REQUEST_ENV, MAX_WORKER_URLS_PER_REQUEST)
        if len(urls_list) > max_urls:
            return Response(
                content=(
                    f"Too many worker URLs: {len(urls_list)} (maximum is {max_urls}, "
                    f"configurable with {MAX_WORKER_URLS_PER_REQUEST_ENV})"
                ),
                status_code=400,
            )

        # Reject the router itself as a worker. Registering it would make the
        # inference forwarding recurse into the router and exhaust the
        # connection pool and the event loop (denial of service).
        for url in urls_list:
            if await is_router_self_reference(url, router_port):
                logger.warning("Rejected worker registration pointing at the router itself: %s", url)
                return Response(
                    content=f"Refusing to register the router itself as a worker: {url}",
                    status_code=400,
                )

        # Normalise and de-duplicate so that the same worker cannot be
        # registered several times under different spellings. The same
        # normalisation happens in Router.add_workers, this is only the
        # first of the two.
        normalized = normalize_worker_urls(urls_list)

        _, _, msg = await app.router.add_workers(normalized)
        return Response(content=msg)

    @app.get("/list_workers")
    async def list_workers() -> Response:
        urls = await app.router.get_worker_urls()
        msg = f"{urls}"
        return Response(content=msg)

    @app.post("/remove_workers")
    async def remove_workers(body: WorkUrls, request: Request) -> Response:
        body = body.model_dump()
        urls = body.get("urls")
        if not urls:
            return Response(status_code=400)
        urls_list = [urls] if isinstance(urls, str) else list(urls)
        # The removal path normalises with the same function as the
        # registration path, so that "http://worker:8001/" and
        # "http://worker:8001" refer to the same worker.
        msg = app.router.remove_workers(normalize_worker_urls(urls_list))
        return Response(content=msg)

    @app.get("/pretty_print_tree")
    async def pretty_print_tree() -> Response:
        if isinstance(app.router.router, CacheAwareRouter):
            msg, node_count = app.router.router.tree.pretty_print()
            msg = msg + "\n" + "The total number of node is: " + str(node_count)
        else:
            msg = "Non-cache-aware router does not use a multi tenant radix tree"
        return Response(content=msg)

    @app.exception_handler(NoAvailableWorkerError)
    async def worker_exception_handler(request: Request, exc: NoAvailableWorkerError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(404)
    async def sink_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=404,
            content={
                "code": 404,
                "message": f"invalid route: {request.url.path}",
            },
        )

    return app


if __name__ == "__main__":
    import uvicorn

    router_args = parse_router_args()
    router_args_dict = dict(vars(router_args))

    # Every resource limit of the router, see utils/config.py. The limits are
    # read from the parsed arguments directly, the request model does not
    # know them.
    capacity = CapacityConfig.from_namespace(router_args_dict)

    # The request model requires a value for max_tree_size, the limit itself
    # is resolved by the capacity configuration above.
    router_args_dict["max_tree_size"] = router_args_dict.get("max_tree_size") or 2**24

    router_configuration = RouterArgs(**router_args_dict)

    setup_logger(router_configuration.log_dir, router_configuration.verbose)

    logger.info(f"Capacity limits: {capacity.describe()}")
    route_policy = router_configuration.policy
    if route_policy == "random":
        policy_config = RandomConfig(
            timeout_secs=router_configuration.worker_startup_timeout_secs,
            interval_secs=router_configuration.worker_startup_check_interval,
        )
    elif route_policy == "round_robin":
        policy_config = RoundRobinConfig(
            timeout_secs=router_configuration.worker_startup_timeout_secs,
            interval_secs=router_configuration.worker_startup_check_interval,
        )
    else:
        policy_config = CacheAwareConfig(
            cache_threshold=router_configuration.cache_threshold,
            balance_abs_threshold=router_configuration.balance_abs_threshold,
            balance_rel_threshold=router_configuration.balance_rel_threshold,
            eviction_interval_secs=router_configuration.eviction_interval_secs,
            capacity=capacity,
            timeout_secs=router_configuration.worker_startup_timeout_secs,
            interval_secs=router_configuration.worker_startup_check_interval,
        )
    app = create_app(
        router_configuration.worker_urls,
        policy_config,
        router_port=router_configuration.port,
    )
    logging.info(f"Initializing router on {router_configuration.host}:{router_configuration.port}")
    logging.info(f"Initializing workers on {router_configuration.worker_urls}")
    logging.info(f"Policy Config: {router_configuration.policy}")
    uvicorn.run(app, host=router_configuration.host, port=router_configuration.port)
