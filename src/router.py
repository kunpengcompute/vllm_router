# Adapted from https://github.com/sgl-project/sglang/blob/v0.5.1/sgl-router/src/routers/router.rs

import random
import threading
import time
from abc import ABC
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum, auto
from threading import Thread
from typing import List, Dict, Optional, Tuple

from src.tree import Tree
from utils.error import NoAvailableWorkerError
from utils.logger import logger


def copy_request_headers(req) -> List[Tuple[str, str]]:
    result = []
    for name, value in req.headers.items():
        result.append((name, str(value)))
    return result


class PolicyConfig(ABC):
    timeout_secs: int
    interval_secs: int


@dataclass
class RandomConfig(PolicyConfig):
    timeout_secs: int = 300
    interval_secs: int = 10


@dataclass
class RoundRobinConfig(PolicyConfig):
    timeout_secs: int = 300
    interval_secs: int = 10


@dataclass
class CacheAwareConfig(PolicyConfig):
    timeout_secs: int = 300
    interval_secs: int = 10
    cache_threshold: float = 0.5
    balance_abs_threshold: int = 32
    balance_rel_threshold: float = 1.0001
    eviction_interval_secs: int = 60
    max_tree_size: int = 2 ** 24


class RouterType(Enum):
    RoundRobin = auto()
    Random = auto()
    CacheAware = auto()


@dataclass
class RouterBase(ABC):
    worker_urls: List[str]
    timeout_secs: int
    interval_secs: int


@dataclass
class RoundRobinRouter(RouterBase):
    current_index: int

    def select_generate_worker(self, *_) -> str:
        urls_count = len(self.worker_urls)
        if urls_count == 0:
            logger.info("Worker list is empty")
            raise NoAvailableWorkerError()
        self.current_index += 1
        idx = self.current_index % urls_count
        return self.worker_urls[idx]


@dataclass
class RandomRouter(RouterBase):
    def select_generate_worker(self, *_) -> str:
        urls_count = len(self.worker_urls)
        if urls_count == 0:
            logger.info("Worker list is empty")
            raise NoAvailableWorkerError()
        return self.worker_urls[random.randint(0, urls_count - 1)]


@dataclass
class CacheAwareRouter(RouterBase):
    """
        Cache-Aware Load Balancing Router

        This router combines two strategies to optimize both cache utilization and request distribution:

        1. Cache-Aware Routing (Approximate Tree)
        2. Load Balancing (Shortest Queue with Balance Thresholds)

        The router dynamically switches between these strategies based on load conditions:
        - Uses load balancing when the system is imbalanced
        - Uses cache-aware routing when the system is balanced

        A system is considered imbalanced if both conditions are met:
        1. (max - min) > abs_threshold
        2. max > rel_threshold * min

        Strategy Details:

        1. Cache-Aware Routing (Approximate Tree)
        -------------------------------------------
        This strategy maintains an approximate radix tree for each worker based on request history,
        eliminating the need for direct cache state queries. The tree stores raw text characters
        instead of token IDs to avoid tokenization overhead.

        Process:
        a. For each request, find the worker with the highest prefix match
        b. If match rate > cache_threshold:
        Route to the worker with the highest match (likely has relevant data cached)
        c. If match rate ≤ cache_threshold:
        Route to the worker with the smallest tree size (most available cache capacity)
        d. Background maintenance:
        Periodically evict least recently used leaf nodes to prevent memory overflow

        2. Load Balancing (Shortest Queue)
        -------------------------------------------
        This strategy tracks pending request counts per worker and routes new requests
        to the least busy worker when the system is detected to be imbalanced.

        Configuration Parameters:
        ------------------------
        1. cache_threshold: (float, 0.0 to 1.0)
        Minimum prefix match ratio to use highest-match routing.
        Below this threshold, routes to worker with most available cache space.

        2. balance_abs_threshold: (integer)
        Absolute difference threshold for load imbalance detection.
        System is potentially imbalanced if (max_load - min_load) > abs_threshold

        3. balance_rel_threshold: (float)
        Relative ratio threshold for load imbalance detection.
        System is potentially imbalanced if max_load > min_load * rel_threshold
        Used in conjunction with abs_threshold to determine final imbalance state.

        4. eviction_interval_secs: (integer)
        Interval between LRU eviction cycles for the approximate trees.

        5. max_tree_size: (integer)
        Maximum nodes per tree. When exceeded, LRU leaf nodes are evicted
        during the next eviction cycle.
    """
    tree: Tree
    running_queue: Dict[str, int]
    processed_queue: Dict[str, int]
    cache_threshold: float
    balance_abs_threshold: int
    balance_rel_threshold: float
    _eviction_thread: Optional[Thread] = None
    lock: threading.RLock = threading.RLock()

    def delete_worker(self, worker_url: str):
        self.worker_urls.remove(worker_url)
        with self.lock:
            self.processed_queue.pop(worker_url, None)
            self.running_queue.pop(worker_url, None)

        self.tree.remove_tenant(worker_url)

    def select_generate_worker(self, text: str):
        urls_count = len(self.worker_urls)
        if urls_count == 0:
            logger.info("Worker list is empty")
            raise NoAvailableWorkerError()
        # Get current load statistics
        current_loads = list(self.running_queue.values())
        max_load = max(current_loads) if current_loads else 0
        min_load = min(current_loads) if current_loads else 0

        # Load is considered imbalanced if:
        # (max - min) > abs_threshold and max > rel_threshold * min
        if max_load - min_load > self.balance_abs_threshold and max_load > min_load * self.balance_rel_threshold:
            logger.info(
                f"Load balancing triggered due to workload imbalance:\n Max load: {max_load} Min load: {min_load} "
                f"\n Current running queue: {self.running_queue}")

            # Use the shortest queue routing when load is imbalanced
            try:
                selected_url = (min(self.running_queue.items(), key=lambda x: x[1])[0])
            except Exception:
                selected_url = self.worker_urls[0]
        else:
            # Use cache-aware routing when load is balanced
            matched_text, matched_worker = self.tree.prefix_match(text)
            matched_rate = len(matched_text) / len(text) if text else 0

            selected_url = matched_worker if matched_rate > self.cache_threshold else self.tree.get_smallest_tenant()

        with self.lock:
            updated = False
            for queue in (self.running_queue, self.processed_queue):
                if selected_url in queue:
                    queue[selected_url] = queue.get(selected_url, 0) + 1
                    updated = True

            if updated:
                self.tree.insert(text, selected_url)
        return selected_url


class RouteSelector:
    def __init__(self):
        self.type = None
        self.lock = threading.RLock()

    def create_router(self, worker_urls: List[str], policy_config: PolicyConfig):
        if isinstance(policy_config, (RandomConfig, RoundRobinConfig, CacheAwareConfig)):
            timeout_secs = policy_config.timeout_secs
            interval_secs = policy_config.interval_secs
        else:
            raise ValueError("Invalid policy config type")

        # 根据策略创建 router
        if isinstance(policy_config, RandomConfig):
            return RandomRouter(
                worker_urls=deepcopy(worker_urls),
                timeout_secs=timeout_secs,
                interval_secs=interval_secs
            )
        elif isinstance(policy_config, RoundRobinConfig):
            return RoundRobinRouter(
                worker_urls=deepcopy(worker_urls),
                current_index=0,
                timeout_secs=timeout_secs,
                interval_secs=interval_secs
            )
        elif isinstance(policy_config, CacheAwareConfig):
            worker_urls = deepcopy(worker_urls)
            cache_threshold = policy_config.cache_threshold
            balance_abs_threshold = policy_config.balance_abs_threshold
            balance_rel_threshold = policy_config.balance_rel_threshold

            # 初始化树和队列
            tree = Tree()
            running_queue = {url: 0 for url in worker_urls}
            processed_queue = {url: 0 for url in worker_urls}

            def _eviction_loop():
                while True:
                    # Sleep for the specified interval
                    time.sleep(policy_config.eviction_interval_secs)

                    # Run eviction
                    tree.evict_tenant_by_size(policy_config.max_tree_size)

                    # Print the process queue
                    logger.info(f"Processed Queue: {processed_queue}")

                    # Print the running queue
                    logger.info(f"Running Queue: {running_queue}")

            # 创建后台线程
            eviction_thread = threading.Thread(
                target=_eviction_loop,
                daemon=True
            )
            eviction_thread.start()

            # 插入初始节点
            for url in worker_urls:
                tree.insert("", url)

            return CacheAwareRouter(
                worker_urls=worker_urls,
                tree=tree,
                running_queue=running_queue,
                processed_queue=processed_queue,
                cache_threshold=cache_threshold,
                balance_abs_threshold=balance_abs_threshold,
                balance_rel_threshold=balance_rel_threshold,
                timeout_secs=timeout_secs,
                interval_secs=interval_secs,
                _eviction_thread=eviction_thread
            )

    def update_router(self, worker_url: str, router: RouterBase, add: bool):
        if add:
            router.worker_urls.append(worker_url)
            if isinstance(router, CacheAwareRouter):
                router.tree.insert("", worker_url)
        else:
            router.worker_urls = [url for url in router.worker_urls if url != worker_url]
            if isinstance(router, CacheAwareRouter):
                router.tree.remove_tenant(worker_url)

    def get_worker_urls(self, router) -> List[str]:
        """Get a deep copy of the worker URLs for thread safety"""
        with self.lock:
            if isinstance(router, (RoundRobinRouter, RandomRouter, CacheAwareRouter)):
                return deepcopy(router.worker_urls)

    async def add_worker(self, worker_url: str, router: RouterBase):
        start_time = time.time()

        while True:
            if time.time() - start_time > router.timeout_secs:
                error_msg = (
                    f"Timeout {router.timeout_secs}s waiting for worker {worker_url} to become healthy."
                )
                logger.error(error_msg)
                return False, error_msg

            if worker_url in router.worker_urls:
                return False, f"Worker {worker_url} already exists"
            logger.info(f"Added worker: {worker_url}")
            router.worker_urls.append(worker_url)

            # If cache aware, initialize the queues for the new worker
            if isinstance(router, CacheAwareRouter):
                with router.running_queue as r_q:
                    r_q[worker_url] = 0
                with router.processed_queue as p_q:
                    p_q[worker_url] = 0
                with router.tree as t:
                    t.insert("", worker_url)
            return True, f"Successfully added worker: {worker_url}"

    def remove_worker(self, worker_url: str, router: RouterBase):
        router.worker_urls.remove(worker_url)
        logger.info(f"Removed worker: {worker_url}")

        # if cache aware, remove the worker from the tree
        if isinstance(router, CacheAwareRouter):
            with self.lock:
                router.running_queue.pop(worker_url, None)
                router.processed_queue.pop(worker_url, None)
            router.tree.remove_tenant(worker_url)
            logger.info(f"Removed worker from tree and cleaned up queues: {worker_url}")
