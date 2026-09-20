"""Provider-neutral public contract.

Concrete adapters live in their named modules and are imported only by the
application composition root or by adapter-specific tests.
"""

from chat_to_howto.providers.base import LLMProvider

__all__ = ["LLMProvider"]
