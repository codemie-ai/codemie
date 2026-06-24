# AgentCore — Non-Streaming Configuration

## Basic (no reasoning)

### `configuration_json`
```json
{
  "request": { "message_path": "input" },
  "response": {
    "streaming": false,
    "body": {
      "text_path": "output"
    }
  }
}
```

### Request sent to AgentCore runtime
```json
{ "input": "user question" }
```

### Expected AgentCore response
```json
{ "output": "final answer" }
```

---

## With reasoning / thoughts

### `configuration_json`
```json
{
  "request": { "message_path": "input" },
  "response": {
    "streaming": false,
    "body": {
      "text_path": "output",
      "reasoning": {
        "text_path": "thinking"
      }
    }
  }
}
```

### Request sent to AgentCore runtime
```json
{ "input": "user question" }
```

### Expected AgentCore response
```json
{
  "output": "final answer",
  "thinking": "step-by-step reasoning here"
}
```

`active_path` is optional and unused for non-streaming — omit it.

---

## Array of thoughts

If the reasoning path traverses an array, one `Thought` is extracted per element.

### `configuration_json`
```json
{
  "request": { "message_path": "input" },
  "response": {
    "streaming": false,
    "body": {
      "text_path": "output",
      "reasoning": {
        "text_path": "thoughts.text",
        "name_path": "thoughts.name",
        "args_path": "thoughts.args"
      }
    }
  }
}
```

### Request sent to AgentCore runtime
```json
{ "input": "user question" }
```

### Expected AgentCore response
```json
{
  "output": "final answer",
  "thoughts": [
    { "text": "step one reasoning", "name": "planner", "args": { "goal": "analyse" } },
    { "text": "step two reasoning", "name": "executor", "args": { "action": "run" } }
  ]
}
```

Produces two `Thought` frames, one per array element.

---

## Nested paths

`message_path` and `text_path` support dot-notation for nested fields.

### `configuration_json`
```json
{
  "request": { "message_path": "body.query" },
  "response": {
    "streaming": false,
    "body": {
      "text_path": "result.answer",
      "reasoning": {
        "text_path": "result.reasoning.text",
      }
    }
  }
}
```

### Request sent to AgentCore runtime
```json
{ "body": { "query": "user question" } }
```

### Expected AgentCore response
```json
{
  "result": {
    "answer": "final answer",
    "reasoning": {
      "text": "step-by-step reasoning",
      "done": true
    }
  }
}
```
