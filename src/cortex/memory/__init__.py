"""Memory: an incremental index over the brain's files plus remembered facts.

Retrieval design follows Cerebras' knowledge-base write-up
(cerebras.ai/blog/how-we-built-our-knowledge-base):
no single scorer is trusted on its own — full-text and vector rankings are
fused with reciprocal rank fusion, then nudged by recency.
"""

from cortex.memory.chunking import CHUNK_SCHEMA, Chunk, chunk_file
from cortex.memory.store import Store

__all__ = ["CHUNK_SCHEMA", "Chunk", "Store", "chunk_file"]
