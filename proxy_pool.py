"""Compatibility surface for the proxy pool implementation."""
import proxy_pool_v3 as _impl
from proxy_pool_v3 import *  # noqa: F401,F403

# Historical private hooks retained for tests and isolated worker adapters.
_TLS = _impl._TLS
_MANAGER_LOCK = _impl._MANAGER_LOCK
_node_id = _impl._node_id
