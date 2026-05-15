# API Reference

## Base URL
```
http://localhost:7860/api/v1
```

## Endpoints

### POST /tasks
Create a new task.

**Request:**
```json
{
    "goal": "Find all Python files and count lines of code",
    "attachments": [],
    "config": {}
}
```

**Response:**
```json
{
    "task_id": "abc-123",
    "status": "queued",
    "message": "Task created successfully"
}
```

### GET /tasks/{task_id}
Get task status and result.

**Response:**
```json
{
    "task_id": "abc-123",
    "status": "completed",
    "goal": "Find all Python files...",
    "total_steps": 3,
    "completed_steps": 3,
    "execution_trace": [...],
    "duration_ms": 4523.45
}
```

### WebSocket /tasks/{task_id}/stream
Stream real-time execution updates.

### GET /tasks/{task_id}/evidence
Get the full evidence chain.

### POST /tasks/{task_id}/feedback
Submit human-in-the-loop feedback.

### GET /mcp/servers
List all available MCP servers and tools.

### GET /health
Health check for all components.

### GET /metrics
Get agent performance metrics.
