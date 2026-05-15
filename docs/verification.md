# Verification & Self-Healing

## How It Works

After **every action**, the agent verifies the outcome using collected evidence.

### 1. Evidence Collection
The Evidence Collector gathers proof based on the action type:
- **Browser action** → screenshot analysis via VLM
- **Shell/code action** → stdout, stderr, exit code
- **HTTP action** → response status and body
- **File action** → file contents/diff
- **Database action** → query results

### 2. Verification
The Verifier uses the LLM to evaluate: *"Given action X with expected outcome Y, does the evidence show success?"*

Returns:
- `verified: bool`
- `confidence: 0.0 — 1.0`
- `evidence_summary: str`
- `reasoning: str`

### 3. Decision Matrix

| Confidence | Action |
|-----------|--------|
| > 0.8 | ✅ **Proceed** to next step |
| 0.5 — 0.8 | 🔄 **Retry** with slight modification |
| < 0.5 | ⏪ **Rollback** + re-plan the step |
| 3 consecutive failures | 🆘 **Escalate** to user |

### 4. Self-Healing Patterns
Common failure patterns with automatic fixes:
- **Element not found** → wait + retry
- **Permission denied** → try alternative approach
- **Timeout** → increase timeout or break into smaller steps
- **Parse error** → re-prompt with error context
