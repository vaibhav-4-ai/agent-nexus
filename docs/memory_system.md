# Memory System

## Three Types of Memory

### CAG (Context-Augmented Generation) — "Short-Term Memory"
- **What:** A sliding window of recent actions, results, and plan state
- **Speed:** Instant (already in LLM context)
- **When used:** Every single LLM call includes CAG context
- **Implementation:** Token-counted FIFO queue in `src/memory/cag_manager.py`

### RAG (Retrieval-Augmented Generation) — "Knowledge Base"
- **What:** Indexed documents, tool docs, domain knowledge
- **Speed:** ~100ms (vector similarity search)
- **When used:** When a step requires domain knowledge or documentation
- **Implementation:** Qdrant + sentence-transformers in `src/memory/rag_engine.py`

### Episodic Memory — "Experience"
- **What:** Records of past task executions (what worked, what failed)
- **Speed:** ~100ms (vector similarity search)
- **When used:** When the current goal resembles a past task
- **Implementation:** Qdrant collection in `src/memory/episodic.py`

## Memory Router Decision Logic

```
For every step:
1. ✅ Always include CAG (free, already loaded)
2. 🔍 Check if step needs RAG (keyword heuristic: "search", "find", "documentation", etc.)
3. 📚 Check if goal matches past tasks (similarity search on episodic memory)
4. 🗺️ Include knowledge graph summary (entities/relations discovered)
5. 🔗 Combine and deduplicate all context
```
