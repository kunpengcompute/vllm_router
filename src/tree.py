# Adapted from: https://github.com/sgl-project/sglang/blob/v0.5.1/sgl-router/src/tree.rs
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from heapq import heappush, heappop
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from dataclasses import dataclass


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

    """

    def __init__(self):
        self.root = Node()
        self.tenant_char_count: Dict[str, int] = defaultdict(int)
        self.lock = threading.RLock()

    def insert(self, text: str, tenant: str):
        # 插入字符串到树中
        curr = self.root
        curr_idx = 0
        timestamp_us = int(time.time() * 10000000)

        with self.lock:
            curr.tenant_last_access_time[tenant] = timestamp_us
            self.tenant_char_count[tenant] += 0

        prev = self.root
        text_count = len(text)

        while curr_idx < text_count:
            first_char = text[curr_idx]
            curr = prev

            with curr.lock:
                if first_char not in curr.children:
                    # 创建新节点
                    curr_text = text[curr_idx: text_count]

                    curr_text_count = len(curr_text)
                    new_node = Node()
                    new_node.text = curr_text
                    new_node.parent = curr
                    new_node.tenant_last_access_time[tenant] = timestamp_us
                    curr.children[first_char] = new_node
                    self.tenant_char_count[tenant] += curr_text_count

                    prev = new_node
                    curr_idx = text_count
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
                        matched_text = matched_node_text[: shared_count]
                        contracted_text = matched_node_text[shared_count: matched_node_text_count]
                        matched_text_count = len(matched_text)
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
                    else:
                        # move to next node
                        prev = matched_node

                        # Increment char count when adding tenant to existing node
                        if tenant not in prev.tenant_last_access_time:
                            self.tenant_char_count[tenant] += matched_node_text_count

                        prev.tenant_last_access_time[tenant] = timestamp_us
                        curr_idx += shared_count

    def prefix_match(self, text) -> (str, str):
        curr_idx = 0

        prev = self.root
        text_count = len(text)

        while curr_idx < text_count:
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
                else:
                    # Partial match, stop here
                    curr_idx += shared_count
                    prev = matched_node
                    break
            else:
                break

        curr = prev

        # Select the first tenant (key in the map)
        tenant = next(iter(curr.tenant_last_access_time.keys()), "empty")

        timestamp_us = int(time.time() * 10000000)

        if tenant != "empty":
            current_node = curr
            while current_node:
                with current_node.lock:
                    current_node.tenant_last_access_time[tenant] = timestamp_us
                current_node = current_node.parent

        ret_text = text[0: curr_idx]
        return ret_text, tenant

    def prefix_match_tenant(self, text: str, tenant: str):
        curr_idx = 0

        matched_flag = 0

        prev = self.root
        text_count = len(text)

        while curr_idx < text_count:
            first_char = text[curr_idx]
            curr_text = text[curr_idx: text_count]

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
                else:
                    # Partial match, stop here
                    curr_idx += shared_count
                    prev = matched_node
                    break
            else:
                # No match found, stop here
                break

        curr = prev

        # Only update timestamp if we found a match for the specified tenant
        if tenant in curr.tenant_last_access_time:
            timestamp_us = int(time.time() * 10000000)

            current_node = curr
            while current_node:
                with current_node.lock:
                    current_node.tenant_last_access_time[tenant] = timestamp_us
                current_node = current_node.parent

        return text[0: curr_idx]

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

    def evict_tenant_by_size(self, max_size: int):
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

        print("Before eviction - Used size per tenant:")
        for tenant, size in self.tenant_char_count.items():
            print(f"Tenant: {tenant}, Size: {size}")

        # Process eviction
        while pq:
            entry = heappop(pq)
            tenant = entry.tenant
            node = entry.node

            used_size = self.tenant_char_count.get(tenant, 0)
            if used_size <= max_size:
                continue

            # Decrement when removing tenant from node
            if tenant in node.tenant_last_access_time:
                self.tenant_char_count[tenant] -= len(node.text)

            # Remove tenant from node
            del node.tenant_last_access_time[tenant]

            # Remove empty nodes
            if not node.children and not node.tenant_last_access_time:
                if node.parent:
                    first_char = node.text[0] if node.text else None
                    if first_char:
                        del node.parent.children[first_char]

            # Add parent to queue if it becomes a leaf
            if node.parent:
                if tenant in self.leaf_of(node.parent):
                    if tenant in node.parent.tenant_last_access_time:
                        timestamp = node.parent.tenant_last_access_time[tenant]
                        heappush(pq, EvictionEntry(timestamp, tenant, node.parent))

        print("After eviction - Used size per tenant:")
        for tenant, size in self.tenant_char_count.items():
            print(f"Tenant: {tenant}, Size: {size}")

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
                            del parent.children[first_char]

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
