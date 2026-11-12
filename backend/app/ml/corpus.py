"""Synthetic-but-realistic labeled corpus used to train and evaluate the ML
failure classifier.

Every example is a short textual "error signal" built from the fields an
analyst would look at (error messages, tool names, finish reasons). The labels
are the ten failure categories. The corpus is deliberately balanced so that the
baseline model is not skewed; see ml/train.py for the evaluation that is
actually reported in the UI (we never claim accuracy we did not measure).
"""

from __future__ import annotations

from app.core.models import FailureCategory as F

CORPUS: list[tuple[str, str]] = [
    # ---- reasoning_failure
    ("agent received correct tool output but answered with a different value, ignored observation", F.REASONING.value),
    ("model reasoning contradicted the retrieved document and produced wrong conclusion", F.REASONING.value),
    ("agent misinterpreted the user request and solved a different task", F.REASONING.value),
    ("chain of thought skipped a required check and concluded prematurely", F.REASONING.value),
    ("agent used the wrong field from the tool result in its final answer", F.REASONING.value),
    ("assistant stated the opposite of what the search results showed", F.REASONING.value),
    ("agent failed to combine two intermediate results into the final answer", F.REASONING.value),
    ("model ignored the constraint given in the system prompt and reasoned from stale assumptions", F.REASONING.value),
    ("agent answered the question about the wrong entity despite correct context", F.REASONING.value),
    ("llm response does not follow from the observations it collected", F.REASONING.value),
    # ---- hallucination
    ("final answer cites a source that does not exist in the retrieved documents fabricated citation", F.HALLUCINATION.value),
    ("agent invented an order id that never appeared in the database output", F.HALLUCINATION.value),
    ("answer contains facts not supported by any tool result, unsupported claim", F.HALLUCINATION.value),
    ("model made up a phone number and address for the customer", F.HALLUCINATION.value),
    ("agent hallucinated a function name that is not in the available tools list", F.HALLUCINATION.value),
    ("response included a fake statistic with no evidence in context", F.HALLUCINATION.value),
    ("agent claimed the file was uploaded but no upload tool was called", F.HALLUCINATION.value),
    ("answer references a document title absent from the vector search results", F.HALLUCINATION.value),
    ("model fabricated an email thread that was never in memory", F.HALLUCINATION.value),
    ("agent asserted the api returned success although the tool returned an error", F.HALLUCINATION.value),
    # ---- tool_execution_failure
    ("tool error: invalid arguments passed to get_weather, schema validation failed", F.TOOL_EXECUTION.value),
    ("function call failed with TypeError unexpected keyword argument", F.TOOL_EXECUTION.value),
    ("unknown tool search_flights requested by model, tool not found", F.TOOL_EXECUTION.value),
    ("tool raised KeyError while executing the sql query", F.TOOL_EXECUTION.value),
    ("mcp tools/call returned isError true, tool execution failed", F.TOOL_EXECUTION.value),
    ("argument parsing failed for calculator, missing required parameter expression", F.TOOL_EXECUTION.value),
    ("tool response status 400 bad request for create_ticket", F.TOOL_EXECUTION.value),
    ("python_repl tool raised SyntaxError in generated code", F.TOOL_EXECUTION.value),
    ("browser tool failed to click element, selector not found", F.TOOL_EXECUTION.value),
    ("tool call raised permission error writing to the file system", F.TOOL_EXECUTION.value),
    # ---- api_failure
    ("openai api returned 401 unauthorized invalid api key", F.AUTH.value),
    ("provider returned 429 rate limit exceeded, too many requests", F.API.value),
    ("anthropic api overloaded 529 error, service unavailable", F.API.value),
    ("openai 500 internal server error while generating completion", F.API.value),
    ("insufficient_quota: you exceeded your current quota", F.API.value),
    # ---- authentication_failure
    ("request rejected with 403 forbidden, the api key lacks the required scope", F.AUTH.value),
    ("invalid_api_key: incorrect api key provided", F.AUTH.value),
    ("authentication failed, the bearer token has expired", F.AUTH.value),
    ("permission denied for this resource, access denied to the workspace", F.AUTH.value),
    ("oauth token expired, refresh required before calling the provider", F.AUTH.value),
    # ---- database_failure
    ("psycopg operationalerror could not connect to server connection refused", F.DATABASE.value),
    ("sqlalchemy integrityerror duplicate key value violates unique constraint", F.DATABASE.value),
    ("deadlock detected while updating the orders table, transaction rolled back", F.DATABASE.value),
    ("database is locked: no such table users in the sqlite database", F.DATABASE.value),
    ("connection pool exhausted, timed out waiting for a free database connection", F.DATABASE.value),
    ("relation customers does not exist, sqlstate 42p01", F.DATABASE.value),
    ("mongodb server selection timeout while inserting the document", F.DATABASE.value),
    ("too many connections for the postgres server, try again later", F.DATABASE.value),
    ("apiconnectionerror: connection error to api.openai.com", F.API.value),
    ("bad gateway 502 from the embedding provider", F.API.value),
    ("403 forbidden calling the model endpoint", F.API.value),
    ("api error: 503 model is currently overloaded, retry later", F.API.value),
    ("anthropic invalid_request_error: max_tokens is too large for model", F.API.value),
    # ---- memory_context_failure
    ("context length exceeded, maximum context window is 128000 tokens", F.MEMORY_CONTEXT.value),
    ("conversation history truncated and earlier instructions were lost", F.MEMORY_CONTEXT.value),
    ("agent forgot the user's name between turns, memory not persisted", F.MEMORY_CONTEXT.value),
    ("token limit reached, message too long for the model", F.MEMORY_CONTEXT.value),
    ("agent repeated a question the user already answered, no memory retrieval", F.MEMORY_CONTEXT.value),
    ("vector store returned empty results, missing context for the answer", F.MEMORY_CONTEXT.value),
    ("context overflow after 40 tool responses were appended", F.MEMORY_CONTEXT.value),
    ("summarisation dropped the constraint the task depended on", F.MEMORY_CONTEXT.value),
    ("memory write failed so the next step had no prior state", F.MEMORY_CONTEXT.value),
    ("retrieved documents were from the wrong namespace, irrelevant context", F.MEMORY_CONTEXT.value),
    # ---- planning_failure
    ("agent could not decompose the task into steps, no plan produced", F.PLANNING.value),
    ("planner selected a tool that cannot satisfy the goal", F.PLANNING.value),
    ("execution stopped during planning, plan validation error", F.PLANNING.value),
    ("graph routed to a node that does not exist", F.PLANNING.value),
    ("agent skipped the required verification step in its plan", F.PLANNING.value),
    ("multi step task aborted after the first subtask with no follow up", F.PLANNING.value),
    ("invalid plan: dependency cycle between two subtasks", F.PLANNING.value),
    ("agent never used any tool, gave up before planning the search", F.PLANNING.value),
    ("task decomposition produced steps that duplicate each other", F.PLANNING.value),
    ("planner chose the wrong order of operations and the task became impossible", F.PLANNING.value),
    # ---- infinite_loop
    ("agent called search with identical arguments 6 times with no progress", F.INFINITE_LOOP.value),
    ("execution loop exceeded max iterations, same tool called repeatedly", F.INFINITE_LOOP.value),
    ("agent retried the same failing action forever without changing strategy", F.INFINITE_LOOP.value),
    ("loop detected: get_page fetched the same url four times in a row", F.INFINITE_LOOP.value),
    ("agent oscillated between two tools with no state change", F.INFINITE_LOOP.value),
    ("stopping condition never met, recursion depth exceeded", F.INFINITE_LOOP.value),
    ("repeated identical tool calls with identical parameters, no new information", F.INFINITE_LOOP.value),
    ("agent kept re planning the same step without executing it", F.INFINITE_LOOP.value),
    ("maximum steps reached while looping on the same function call", F.INFINITE_LOOP.value),
    ("self reflection loop never terminated, output unchanged each turn", F.INFINITE_LOOP.value),
    # ---- incorrect_output
    ("final response is malformed json, could not parse output", F.INCORRECT_OUTPUT.value),
    ("answer does not match the requested schema, validation error", F.INCORRECT_OUTPUT.value),
    ("agent returned an empty final message instead of the report", F.INCORRECT_OUTPUT.value),
    ("output truncated because finish_reason was length", F.INCORRECT_OUTPUT.value),
    ("returned a string where the caller expected a list of items", F.INCORRECT_OUTPUT.value),
    ("final answer missed half of the requested fields", F.INCORRECT_OUTPUT.value),
    ("pydantic validation error on the structured output", F.INCORRECT_OUTPUT.value),
    ("agent produced the answer in the wrong language and format", F.INCORRECT_OUTPUT.value),
    ("response included the reasoning trace instead of the final result", F.INCORRECT_OUTPUT.value),
    ("output contained a broken markdown table that failed downstream parsing", F.INCORRECT_OUTPUT.value),
    # ---- safety_policy_violation
    ("content filter blocked the response, content_policy_violation", F.SAFETY.value),
    ("guardrail triggered: agent attempted to reveal the system prompt", F.SAFETY.value),
    ("moderation flagged the generated text as harmful", F.SAFETY.value),
    ("agent tried to execute a destructive command and was blocked", F.SAFETY.value),
    ("policy violation: agent attempted to access another user's data", F.SAFETY.value),
    ("model refused the request due to safety guidelines", F.SAFETY.value),
    ("jailbreak attempt detected, response stopped by safety layer", F.SAFETY.value),
    ("agent attempted to send an email to an external address without approval", F.SAFETY.value),
    ("pii redaction policy blocked the output containing card numbers", F.SAFETY.value),
    ("guardrail failure: answer contained disallowed medical advice", F.SAFETY.value),
    # ---- timeout_failure
    ("tool call timed out after 30 seconds, deadline exceeded", F.TIMEOUT.value),
    ("read timeout while waiting for the search api response", F.TIMEOUT.value),
    ("execution exceeded the global time budget and was killed", F.TIMEOUT.value),
    ("504 gateway timeout from the upstream tool", F.TIMEOUT.value),
    ("request timed out: ETIMEDOUT connecting to the database", F.TIMEOUT.value),
    ("agent stopped after the per-step timeout of 60s was reached", F.TIMEOUT.value),
    ("tool invocation did not return within the configured timeout", F.TIMEOUT.value),
    ("streaming response stalled and the deadline was exceeded", F.TIMEOUT.value),
    ("timeout waiting for the sandbox to start", F.TIMEOUT.value),
    ("the run was cancelled because the request timeout expired", F.TIMEOUT.value),
]


def training_texts_and_labels() -> tuple[list[str], list[str]]:
    texts = [text for text, _label in CORPUS]
    labels = [label for _text, label in CORPUS]
    return texts, labels
