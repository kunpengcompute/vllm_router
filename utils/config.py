#  Copyright 2025 Huawei Technologies Co., Ltd.
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

"""The resource limits of the router, collected in one place.

The router bounds four independent resources. Before this module existed
every limit lived in a different layer (a command line option, a field of the
request model, a default value of the tree) and the layers did not agree with
each other: the text length of a request was truncated with a hard coded
value in the request path while the tree used a configurable one, and the
global budget was only enforced by the periodic eviction thread.

The limits and their meaning:

=====================  ===============================================  ==============
Limit                  Meaning                                         Default
=====================  ===============================================  ==============
``max_tree_size``      characters that a single tenant (worker) may     2**24
                       keep in the tree
``max_tree_total_size``  characters that all tenants together may keep   2**24
                       in the tree
``max_tree_nodes``     nodes that the tree may hold                     2**24
``max_cache_depth``    length of a chain of nodes                       64
``max_cache_text_      characters of a request that are stored in the    4096
length``               tree
``max_workers``        workers that may be registered at the same time  256
=====================  ===============================================  ==============

Every limit can be set on the command line and every limit can additionally
be set with an environment variable. The command line wins over the
environment variable, the default is used when neither of them is given.

Disabling a limit
-----------------

A limit of ``0`` disables the limit, the corresponding resource is then
unbounded. This is the migration path for a deployment that hits one of the
new limits: the limit is raised first and the behaviour is observed, the
limit is only disabled when the resource is really needed.

===========================  =========================================
Environment variable         Command line option
===========================  =========================================
``VLLM_ROUTER_MAX_TREE_``    ``--max-tree-size``
``SIZE``
``VLLM_ROUTER_MAX_TREE_``    ``--max-tree-total-size``
``TOTAL_SIZE``
``VLLM_ROUTER_MAX_TREE_``    ``--max-tree-nodes``
``NODES``
``VLLM_ROUTER_MAX_CACHE_``   ``--max-cache-depth``
``DEPTH``
``VLLM_ROUTER_MAX_CACHE_``   ``--max-cache-text-length``
``TEXT_LENGTH``
``VLLM_ROUTER_MAX_WORKERS``  ``--max-workers``
===========================  =========================================
"""

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from utils.logger import logger

# Environment variable of every limit.
MAX_TREE_SIZE_ENV = "VLLM_ROUTER_MAX_TREE_SIZE"
MAX_TREE_TOTAL_SIZE_ENV = "VLLM_ROUTER_MAX_TREE_TOTAL_SIZE"
MAX_TREE_NODES_ENV = "VLLM_ROUTER_MAX_TREE_NODES"
MAX_CACHE_DEPTH_ENV = "VLLM_ROUTER_MAX_CACHE_DEPTH"
MAX_CACHE_TEXT_LENGTH_ENV = "VLLM_ROUTER_MAX_CACHE_TEXT_LENGTH"
MAX_WORKERS_ENV = "VLLM_ROUTER_MAX_WORKERS"

# A limit of 0 disables the limit.
UNLIMITED = 0


def _env_limit(name: str, default: int) -> int:
    """Read a limit from the environment.

    An invalid or negative value falls back to the default: a
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
    if value < 0:
        logger.warning("Ignoring negative value %r for %s, using %s", raw, name, default)
        return default
    return value


def _limit(argument: Optional[int], env_name: str, default: int) -> int:
    """Combine the command line value and the environment variable.

    The command line wins when it was given, the environment variable is used
    when it was not.
    """
    if argument is not None:
        return argument
    return _env_limit(env_name, default)


@dataclass(frozen=True)
class CapacityConfig:
    """The resource limits of the router.

    A limit of ``0`` disables the limit, the resource is then unbounded.
    """

    max_tree_size: int = 2**24
    max_tree_total_size: int = 2**24
    max_tree_nodes: int = 2**24
    max_cache_depth: int = 64
    max_cache_text_length: int = 4096
    max_workers: int = 256

    @classmethod
    def from_namespace(cls, args: Optional[Mapping[str, Any]] = None) -> "CapacityConfig":
        """Build the configuration from the parsed command line arguments.

        Every limit falls back to its environment variable and then to its
        default, so that a limit can be tuned without changing the command
        line of an existing deployment.
        """
        args = args or {}

        def value(name: str, env_name: str, default: int) -> int:
            return _limit(args.get(name), env_name, default)

        return cls(
            max_tree_size=value("max_tree_size", MAX_TREE_SIZE_ENV, cls.max_tree_size),
            max_tree_total_size=value("max_tree_total_size", MAX_TREE_TOTAL_SIZE_ENV, cls.max_tree_total_size),
            max_tree_nodes=value("max_tree_nodes", MAX_TREE_NODES_ENV, cls.max_tree_nodes),
            max_cache_depth=value("max_cache_depth", MAX_CACHE_DEPTH_ENV, cls.max_cache_depth),
            max_cache_text_length=value("max_cache_text_length", MAX_CACHE_TEXT_LENGTH_ENV, cls.max_cache_text_length),
            max_workers=value("max_workers", MAX_WORKERS_ENV, cls.max_workers),
        )

    def describe(self) -> str:
        """Return a human readable description of the limits."""
        return (
            f"max_tree_size={self.max_tree_size}, max_tree_total_size={self.max_tree_total_size}, "
            f"max_tree_nodes={self.max_tree_nodes}, max_cache_depth={self.max_cache_depth}, "
            f"max_cache_text_length={self.max_cache_text_length}, max_workers={self.max_workers}"
        )
