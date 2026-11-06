"""No-profiler adapter.

Registered by ``adapters/__init__.py``; kept in its own module so the null path can be
imported without pulling in a vendor adapter.
"""

from . import NullAdapter, register

register("null", lambda config: NullAdapter())
