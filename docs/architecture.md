# Architecture

## System Overview

```mermaid
graph TB
    subgraph "Client"
        A[User] -->|POST /task| B[FastAPI Gateway]
        A -->|WebSocket| B
    end

    subgraph "Core Services"
        B --> C[Orchestrator Engine]
        C --> D[Goal Parser]
        C --> E[Planner]
        C --> F[Executor]
        C --> G[Verifier]
    end

    subgraph "Memory Layer"
        C --> H[CAG Manager]
        C --> I[RAG Engine]
        C --> J[Episodic Memory]
        C --> K[Memory Router]
    end

    subgraph "Tool Execution (MCP)"
        F --> L[MCP Client]
        L --> M[Filesystem]
        L --> N[Shell]
        L --> O[HTTP]
        L --> P[Database]
        L --> Q[Code Exec]
        L --> R[Search]
    end

    subgraph "External Services (Free Tier)"
        S[(Neon Postgres)]
        T[(Qdrant Cloud)]
        U[(Upstash Redis)]
        V[Groq API]
        W[DagsHub MLflow]
    end

    P --> S
    I --> T
    J --> T
    H --> U
    D --> V
    E --> V
    G --> V
    C --> W
```

## Data Flow

1. **User submits a task** via REST API or WebSocket
2. **Goal Parser** decomposes the natural language goal into structured objectives
3. **Memory Router** queries CAG, RAG, and Episodic memory for relevant context
4. **Planner** creates a step-by-step execution plan informed by past experience
5. **Executor** selects and invokes MCP tools for each step
6. **Evidence Collector** gathers multimodal evidence after each action
7. **Verifier** evaluates success using LLM reasoning on the evidence
8. **Recovery Engine** handles failures via retry, rollback, or re-planning
9. **Results** are streamed back via WebSocket and stored in the database

## Design Patterns

| Pattern | Component | Purpose |
|---------|-----------|---------|
| Strategy | LLM Provider | Swap backends without code changes |
| Factory | Perception Layer | Create modality-specific engines |
| Repository | Database Layer | Abstract DB operations |
| State Machine | Task Lifecycle | Enforce valid state transitions |
| Circuit Breaker | LLM/Redis clients | Prevent cascading failures |
| Observer | Event Bus | Decouple event producers/consumers |
| Template Method | MCP Servers | Shared lifecycle management |
| Chain of Responsibility | Memory Router | Priority-based memory querying |
