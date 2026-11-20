# Adapted from: https://github.com/sgl-project/sglang/blob/v0.5.1/sgl-router/src/tree.rs
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from heapq import heappop, heappush
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from utils.config import CapacityConfig
from utils.logger import logger

# The global budget is evicted down to this ratio of the budget instead of
# exactly to the budget. Evicting down to the budget makes the very next
# insertion exceed it again, which would run the (linear) eviction on every
# single request.
GLOBAL_EVICTION_LOW_WATER_RATIO = 0.9


def shared_prefix_count(a: str, b: str) -> int:
    min_len = min(len(a), len(b))
    for i in range(min_len):
        if a[i] != b[i]:
            return i
    return min_len


class Node:
    def __init__(self):
        self.children: Dict[str, Node] = {}
        self.text: str = ""
        self.tenant_last_access_time: Dict[str, int] = {}
        self.parent: Optional[Node] = None
        self.lock = threading.RLock()

    def __repr__(self) -> str:
        return f"Node(text='{self.text}')"


@dataclass
class EvictionEntry:
    timestamp: int
    tenant: str
    node: Node

    def __lt__(self, other):
        if not isinstance(other, EvictionEntry):
            return False
        return self.timestamp < other.timestamp

    def __eq__(self, other) -> bool:
        """实现等值判断"""
        if not isinstance(other, EvictionEntry):
            return False
        return self.timestamp == other.timestamp


class Tree:
    """
     Thread-safe multi tenant radix tree

    1. Storing data for multiple tenants (the overlap of multiple radix tree)
    2. Node-level lock to enable concurrent access on nodes
    3. Leaf LRU eviction based on tenant access time

    The tree is bounded on purpose:

    * ``max_nodes`` caps the total number of nodes. A request that would
      exceed the cap is not cached, the routing falls back to load balancing.
      Without this cap a client that sends many requests with a shared
      prefix and a varying tail creates one node per request, which exhausts
      the memory of the router (CWE-400).
    * ``max_depth`` caps the length of a chain. Every lookup walks the chain
      and every hit updates the access time of the whole ancestor chain, so
      an unbounded depth turns a single request into an O(depth) operation.
    * ``max_text_length`` caps the number of characters that are stored per
      insertion. Only the prefix of a prompt is relevant for the cache aware
      routing, so the tail is dropped.
    """

    def __init__(
        self,
        capacity: Optional[CapacityConfig] = None,
        max_nodes: Optional[int] = None,
        max_depth: Optional[int] = None,
        max_text_length: Optional[int] = None,
        max_total_chars: Optional[int] = None,
        max_tenant_chars: Optional[int] = None,
    ):
        capacity = capacity or CapacityConfig()

        self.root = Node()
        self.tenant_char_count: Dict[str, int] = defaultdict(int)
        self.lock = threading.RLock()
        self.capacity = capacity
        self.max_nodes = capacity.max_tree_nodes if max_nodes is None else max_nodes
        self.max_depth = capacity.max_cache_depth if max_depth is None else max_depth
        self.max_text_length = capacity.max_cache_text_length if max_text_length is None else max_text_length
        self.max_total_chars = capacity.max_tree_total_size if max_total_chars is None else max_total_chars
        self.max_tenant_chars = capacity.max_tree_size if max_tenant_chars is None else max_tenant_chars
        # The root node is part of the budget.
        self.node_count = 1
        self._node_count_lock = threading.Lock()
        # Timestamp of the last "tree is full" warning, used to throttle it.
        self._last_full_warning = 0.0

    # ------------------------------------------------------------------
    # resource accounting
    # ------------------------------------------------------------------
    def _reserve_node(self) -> bool:
        """Reserve a node slot. Returns False when the tree is full.

        A node budget of 0 disables the budget.
        """
        if not self.max_nodes:
            with self._node_count_lock:
                self.node_count += 1
            return True
        with self._node_count_lock:
            if self.node_count >= self.max_nodes:
                return False
            self.node_count += 1
            return True

    def _release_node(self) -> None:
        with self._node_count_lock:
            if self.node_count > 1:
                self.node_count -= 1

    def get_node_count(self) -> int:
        """Return the number of nodes currently held by the tree."""
        with self._node_count_lock:
            return self.node_count

    def is_full(self) -> bool:
        """Return True when no further node can be allocated."""
        if not self.max_nodes:
            return False
        with self._node_count_lock:
            return self.node_count >= self.max_nodes

    def insert(self, text: str, tenant: str):
        # 插入字符串到树中
        curr = self.root
        curr_idx = 0
        timestamp_us = int(time.time() * 10000000)

        # Only a bounded prefix of the text is relevant for the routing
        # decision, and only a bounded number of characters may be stored.
        # A limit of 0 disables the truncation.
        if self.max_text_length and len(text) > self.max_text_length:
            text = text[: self.max_text_length]

        # The global budget is enforced here and not only by the periodic
        # eviction thread: between two cycles of that thread the tree would
        # otherwise grow far beyond its budget (CWE-770).
        self.enforce_global_budget()

        with self.lock:
            curr.tenant_last_access_time[tenant] = timestamp_us
            self.tenant_char_count[tenant] += 0

        prev = self.root
        text_count = len(text)
        depth = 0

        while curr_idx < text_count:
            if self.max_depth and depth >= self.max_depth:
                # The chain is too deep, the rest of the text is not cached.
                break

            first_char = text[curr_idx]
            curr = prev

            with curr.lock:
                if first_char not in curr.children:
                    # 创建新节点
                    curr_text = text[curr_idx:text_count]

                    curr_text_count = len(curr_text)
                    # The tree has a hard node budget. When the budget is
                    # exhausted the least recently used leaves are evicted
                    # first, so that the tree keeps accepting new entries
                    # instead of staying full forever (CWE-400).
                    if not self._reserve_node():
                        if not self.reclaim_node_budget() or not self._reserve_node():
                            self._warn_tree_full(tenant)
                            return

                    new_node = Node()
                    new_node.text = curr_text
                    new_node.parent = curr
                    new_node.tenant_last_access_time[tenant] = timestamp_us
                    curr.children[first_char] = new_node
                    self.tenant_char_count[tenant] += curr_text_count

                    prev = new_node
                    curr_idx = text_count
                    depth += 1
                else:
                    # 处理匹配上的节点
                    matched_node = curr.children[first_char]
                    matched_node_text = matched_node.text
                    matched_node_text_count = len(matched_node_text)

                    curr_text = text[curr_idx:]

                    shared_count = shared_prefix_count(matched_node_text, curr_text)

                    if shared_count < matched_node_text_count:
                        """
                         split the matched node
                           [curr] -> [matched_node] =>
                           becomes
                           [curr] -> [new_node] -> [contracted_matched_node]
                        """
                        matched_text = matched_node_text[:shared_count]
                        contracted_text = matched_node_text[shared_count:matched_node_text_count]
                        matched_text_count = len(matched_text)
                        if not self._reserve_node():
                            if not self.reclaim_node_budget() or not self._reserve_node():
                                self._warn_tree_full(tenant)
                                return

                        new_node = Node()
                        new_node.text = matched_text
                        new_node.parent = curr
                        new_node.tenant_last_access_time = matched_node.tenant_last_access_time.copy()

                        first_new_char = contracted_text[0]
                        new_node.children[first_new_char] = matched_node

                        curr.children[first_char] = new_node

                        matched_node.text = contracted_text
                        matched_node.parent = new_node

                        prev = new_node

                        # Increment char count for the tenant in the new split node
                        with self.lock:
                            if tenant not in prev.tenant_last_access_time:
                                self.tenant_char_count[tenant] += matched_text_count

                        prev.tenant_last_access_time[tenant] = timestamp_us
                        curr_idx += shared_count
                        depth += 1
                    else:
                        # move to next node
                        prev = matched_node

                        # Increment char count when adding tenant to existing node
                        if tenant not in prev.tenant_last_access_time:
                            self.tenant_char_count[tenant] += matched_node_text_count

                        prev.tenant_last_access_time[tenant] = timestamp_us
                        curr_idx += shared_count
                        depth += 1

    def _warn_tree_full(self, tenant: str) -> None:
        """Log a full tree, but not for every single request."""
        now = time.monotonic()
        with self.lock:
            if now - self._last_full_warning < 60:
                return
            self._last_full_warning = now
        logger.warning(
            "Prefix cache tree is full (%s nodes), dropping the cache entry for tenant %s",
            self.get_node_count(),
            tenant,
        )

    def prefix_match(self, text) -> (str, str):
        curr_idx = 0

        prev = self.root
        text_count = len(text)
        depth = 0

        while curr_idx < text_count:
            # The lookup walks the chain, so it has to be bounded as well.
            if self.max_depth and depth >= self.max_depth:
                break

            first_char = text[curr_idx]
            curr_text = text[curr_idx:]
            curr = prev

            if first_char in curr.children:
                matched_node = curr.children[first_char]
                shared_count = shared_prefix_count(matched_node.text, curr_text)
                matched_node_text_count = len(matched_node.text)

                if shared_count == matched_node_text_count:
                    # Full match, continue to the next node
                    curr_idx += shared_count
                    prev = matched_node
                    depth += 1
                else:
                    # Partial match, stop here
                    curr_idx += shared_count
                    prev = matched_node
                    depth += 1
                    break
            else:
                break

        curr = prev

        # Select the first tenant (key in the map)
        tenant = next(iter(curr.tenant_last_access_time.keys()), "empty")

        timestamp_us = int(time.time() * 10000000)

        if tenant != "empty":
            current_node = curr
            levels = 0
            while current_node and (not self.max_depth or levels < self.max_depth):
                with current_node.lock:
                    current_node.tenant_last_access_time[tenant] = timestamp_us
                current_node = current_node.parent
                levels += 1

        ret_text = text[0:curr_idx]
        return ret_text, tenant

    def prefix_match_tenant(self, text: str, tenant: str):
        curr_idx = 0

        prev = self.root
        text_count = len(text)
        depth = 0

        while curr_idx < text_count:
            # The lookup walks the chain, so it has to be bounded as well.
            if self.max_depth and depth >= self.max_depth:
                break

            first_char = text[curr_idx]
            curr_text = text[curr_idx:text_count]

            curr = prev

            if first_char in curr.children:
                matched_node = curr.children[first_char]

                if tenant not in matched_node.tenant_last_access_time:
                    break

                shared_count = shared_prefix_count(matched_node.text, curr_text)

                matched_node_text_count = len(matched_node.text)

                if shared_count == matched_node_text_count:
                    # Full match with current node's text, continue to next node
                    curr_idx += shared_count
                    prev = matched_node
                    depth += 1
                else:
                    # Partial match, stop here
                    curr_idx += shared_count
                    prev = matched_node
                    depth += 1
                    break
            else:
                # No match found, stop here
                break

        curr = prev

        # Only update timestamp if we found a match for the specified tenant
        if tenant in curr.tenant_last_access_time:
            timestamp_us = int(time.time() * 10000000)

            current_node = curr
            levels = 0
            while current_node and (not self.max_depth or levels < self.max_depth):
                with current_node.lock:
                    current_node.tenant_last_access_time[tenant] = timestamp_us
                current_node = current_node.parent
                levels += 1

        return text[0:curr_idx]

    @staticmethod
    def leaf_of(node):
        # Return the list of tenants if it's a leaf for the tenant

        #  Initialize candidates with all tenants from `tenant_last_access_time`
        candidates = defaultdict(lambda: True)
        for tenant in node.tenant_last_access_time.keys():
            candidates[tenant] = True

        # Mark tenants that appear in children as non-leaf
        for child in node.children.values():
            for tenant in child.tenant_last_access_time:
                candidates[tenant] = False

        # Filter out tenants that are not leaves and return the result
        return [tenant for tenant, is_leaf in candidates.items() if is_leaf]

    def _collect_eviction_entries(self):
        """Collect the LRU entries of the leaves of the tree."""
        stack = [self.root]
        pq = []

        # Traverse the tree and collect leaves
        while stack:
            curr = stack.pop()
            for child in curr.children.values():
                stack.append(child)

            for tenant in self.leaf_of(curr):
                if tenant in curr.tenant_last_access_time:
                    timestamp = curr.tenant_last_access_time[tenant]
                    heappush(pq, EvictionEntry(timestamp, tenant, curr))

        return pq

    def _evict_entry(self, pq, entry) -> int:
        """Drop one tenant from one node.

        Returns the number of characters that were freed.
        """
        tenant = entry.tenant
        node = entry.node
        freed = 0

        # Decrement when removing tenant from node
        if tenant in node.tenant_last_access_time:
            freed = len(node.text)
            self.tenant_char_count[tenant] -= freed

        # Remove tenant from node
        del node.tenant_last_access_time[tenant]

        # Remove empty nodes
        if not node.children and not node.tenant_last_access_time:
            if node.parent:
                first_char = node.text[0] if node.text else None
                if first_char:
                    # The key of a child is the first character of its text,
                    # but the parent may have been split in the meantime. The
                    # identity check avoids a KeyError on a stale key.
                    if node.parent.children.get(first_char) is node:
                        del node.parent.children[first_char]
                        self._release_node()

        # Add parent to queue if it becomes a leaf
        if node.parent:
            if tenant in self.leaf_of(node.parent):
                if tenant in node.parent.tenant_last_access_time:
                    timestamp = node.parent.tenant_last_access_time[tenant]
                    heappush(pq, EvictionEntry(timestamp, tenant, node.parent))

        return freed

    def evict_tenant_by_size(self, max_size: int):
        """Trim every tenant to its own character quota.

        The quota is a *per tenant* limit, see evict_to_global_size() for the
        limit that applies to the tree as a whole. A quota of 0 disables the
        per tenant limit.
        """
        if not max_size:
            return

        pq = self._collect_eviction_entries()

        logger.debug("Before eviction - used size per tenant: %s", dict(self.tenant_char_count))

        # Process eviction
        while pq:
            entry = heappop(pq)
            tenant = entry.tenant

            used_size = self.tenant_char_count.get(tenant, 0)
            if used_size <= max_size:
                continue

            self._evict_entry(pq, entry)

        logger.debug("After eviction - used size per tenant: %s", dict(self.tenant_char_count))

    def evict_to_global_size(self, global_max: int):
        """Trim the tree to the global character budget.

        The per tenant quota of evict_tenant_by_size() can be bypassed by
        registering many tenants: every one of them keeps a full quota, so
        the memory used by the tree grows with the number of tenants (CWE-770).

        The least recently used leaves are dropped first. A budget of 0
        disables the global limit.
        """
        if not global_max:
            return

        total = sum(self.tenant_char_count.values())
        if total <= global_max:
            return

        pq = self._collect_eviction_entries()
        while pq and total > global_max:
            entry = heappop(pq)
            total -= self._evict_entry(pq, entry)

    def enforce_global_budget(self) -> None:
        """Keep the tree inside its global character budget.

        The periodic eviction thread only runs every eviction_interval_secs.
        Between two of its cycles the tree keeps growing, so the budget was
        not a real limit: a client that sends many requests could fill the
        memory of the router in the seconds between two cycles.

        The tree is evicted down to a low water mark instead of exactly to
        the budget. Evicting down to the budget makes the very next request
        exceed it again and would run the eviction on every request.
        """
        if not self.max_total_chars:
            return

        total = sum(self.tenant_char_count.values())
        if total <= self.max_total_chars:
            return

        low_water = int(self.max_total_chars * GLOBAL_EVICTION_LOW_WATER_RATIO)
        self.evict_to_global_size(low_water)

    def reclaim_node_budget(self) -> int:
        """Free nodes by evicting the least recently used leaves.

        The node budget is a hard limit, but without a reclamation path a
        tree that reached its budget could never accept a new node again,
        not even when all of its tenants moved on to completely different
        prompts. The eviction order is the LRU order that the periodic
        eviction uses.

        Returns the number of nodes that were freed.
        """
        if not self.max_nodes:
            return 0

        freed = 0
        pq = self._collect_eviction_entries()
        while pq and self.get_node_count() >= self.max_nodes:
            entry = heappop(pq)
            before = self.get_node_count()
            self._evict_entry(pq, entry)
            freed += before - self.get_node_count()

        return freed

    def remove_tenant(self, tenant: str):
        # 1. Find all the leaves for the tenant
        stack = [self.root]
        queue = deque()

        while stack:
            curr = stack.pop()
            for child in curr.children.values():
                stack.append(child)

            if tenant in self.leaf_of(curr):
                queue.append(curr)

        # 2. Start from the leaves and traverse up to the root, removing the tenant from each node
        while queue:
            curr = queue.popleft()
            # Remove tenant from node
            if tenant in curr.tenant_last_access_time:
                del curr.tenant_last_access_time[tenant]

            # remove empty nodes
            if not curr.children and not curr.tenant_last_access_time:
                with curr.lock:
                    parent = curr.parent
                    if parent is not None:
                        first_char = next(iter(curr.text), None)
                        if first_char is not None:
                            if parent.children.get(first_char) is curr:
                                del parent.children[first_char]
                                self._release_node()

            # add parent to queue if it becomes a leaf
            with curr.lock:
                parent = curr.parent
                if parent is not None and tenant in self.leaf_of(parent):
                    queue.append(parent)

        # 3. Remove the tenant from the tenant_char_count map
        del self.tenant_char_count[tenant]

    def get_tenant_char_count(self) -> Dict[str, int]:
        with self.lock:
            return self.tenant_char_count

    def get_smallest_tenant(self) -> str:
        # Return a placeholder if there are no tenants
        with self.lock:
            if not self.tenant_char_count:
                return "empty"

            # Find the tenant with minimum char count
            min_tenant = None
            min_count = float("inf")

            for tenant, count in self.tenant_char_count.items():
                if count < min_count:
                    min_count = count
                    min_tenant = tenant
        return min_tenant or "empty"

    def get_used_size_per_tenant(self) -> Dict[str, int]:
        # perform a BFS to traverse all nodes and calculate the total size used by each tenant
        used_size_per_tenant = defaultdict(int)
        stack = deque([self.root])

        while stack:
            curr = stack.pop()
            text_count = len(curr.text)

            for tenant in curr.tenant_last_access_time:
                used_size_per_tenant[tenant] += text_count

            for child in curr.children.values():
                stack.append(child)

        return used_size_per_tenant

    @staticmethod
    def node_to_string(node: Node, prefix: str, is_last: bool, sum_child_count: int) -> tuple[int, str]:
        result = [prefix, "└── " if is_last else "├── ", f"'{node.text}' ["]

        # Add tenant information with timestamps
        tenant_info = []
        for tenant_id, timestamp_us in node.tenant_last_access_time.items():
            # Convert milliseconds to seconds and remaining milliseconds
            seconds = timestamp_us // 10000000
            millis = timestamp_us % 10000000

            system_time = datetime.fromtimestamp(seconds, tz=ZoneInfo("Asia/Shanghai"))
            formatted_time = system_time.strftime("%Y-%m-%d %H:%M:%S") + f".{millis:07d}"
            tenant_info.append(f"{tenant_id} | {formatted_time}")

        result.append(", ".join(tenant_info))
        result.append("]\n")

        # Process children
        children = list(node.children.items())
        child_count = len(children)
        sum_child_count += child_count
        for i, (key, child_node) in enumerate(children):
            is_last_child = i == child_count - 1
            new_prefix = prefix + ("    " if is_last else "│   ")
            sum_child_count, string = Tree.node_to_string(child_node, new_prefix, is_last_child, sum_child_count)
            result.append(string)

        return sum_child_count, "".join(result)

    def pretty_print(self):
        with self.lock:
            if not self.root.children:
                pass

            result = []
            children = list(self.root.children.items())
            child_count = len(children)
            sum_child_count = 0
            for i, (key, child_node) in enumerate(children):
                is_last = i == child_count - 1
                sub_sum_child_count, string = self.node_to_string(child_node, "", is_last, 0)
                result.append(string)
                sum_child_count += sub_sum_child_count
            result = "".join(result)
            sum_child_count = sum_child_count + child_count
            print(result)
            return result, sum_child_count


# 使用示例
if __name__ == "__main__":
    tree = Tree()

    # 插入数据
    tree.insert("hello world", "tenant1")
    tree.pretty_print()
    time.sleep(1)
    tree.insert("hello China", "tenant2")
    time.sleep(1)
    tree.insert("hell", "tenant1")
    time.sleep(1)
    tree.insert("apple", "tenant1")
    tree.pretty_print()

    # 查询匹配
    print(tree.prefix_match_tenant("application", "tenant3"))  # ("appl", "tenant1")
    tree.pretty_print()
    print(tree.leaf_of(tree.root.children["w"]))

    tree.remove_tenant("tenant1")
    tree.pretty_print()
