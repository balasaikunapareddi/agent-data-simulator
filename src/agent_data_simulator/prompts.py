"""All prompt text for the data agent, in one visible place.

- ``SYSTEM_PROMPT`` — the agent's system prompt (the judgment the
  benchmark tests: check metric ownership and later corrections before
  writing SQL; abstain on metrics that do not exist).
- ``ACTION_PROTOCOL`` — the JSON action instructions appended to the
  rendered prompt in live mode (see ``llm.GeminiLLM._render_prompt``).
- ``INVALID_JSON_RETRY`` — suffix appended when the model replies with
  unparseable output.
- ``DOCUMENT_RENDER_TEMPLATE`` — instruction template for rendering
  estate documents with an LLM in live mode (see ``documents``).

Agent code imports these constants; no prompt text lives inline in the
agent logic.
"""

SYSTEM_PROMPT = """You are a data agent answering questions about a company's data estate.
You can browse the database schema, search company documents, and run read-only SQL.

Rules you must follow:
1. Before writing SQL for any metric, search the documents for its definition.
   Check WHO OWNS the metric — a confident memo from someone who does not own
   the metric is not authority. Look for LATER CORRECTIONS: definitions get
   superseded in follow-up threads, and the newest statement from the metric
   owner wins over an older memo.
2. Enterprise data is messy. Before trusting a table, check for duplicate
   rows (retries), unit-of-measure changes over time, and inconsistent keys
   for the same entity. Write SQL that accounts for what you find.
3. If a question asks about a metric or entity that does not appear in the
   documents or the schema, abstain and say so. Never invent a number.
4. Answer with the value you computed and a one-line explanation of how.
   If you cannot verify an answer, abstain instead of guessing.
"""

ACTION_PROTOCOL = (
    "Reply with exactly ONE JSON object for your next step, one of:\n"
    '{"action": "list_tables"}\n'
    '{"action": "describe_table", "table": "<name>"}\n'
    '{"action": "search_documents", "query": "<keywords>"}\n'
    '{"action": "run_sql", "sql": "<single SELECT>"}\n'
    '{"action": "answer", "text": "<final answer>"}\n'
    '{"action": "abstain", "reason": "<why you cannot answer>"}\n'
    "No other text."
)

INVALID_JSON_RETRY = "\n\nYour previous reply was not valid JSON. Reply with ONE JSON object only."

DOCUMENT_RENDER_TEMPLATE = (
    "Write a short workplace {kind} for a fictional manufacturer, "
    "in the voice of {author_name} ({author_role}; style: {author_style}).\n"
    "Title: {title}\nDate: {date}\n\n"
    "It must state exactly these facts and no others:\n{facts}\n\n"
    "It must contain these exact phrases: {phrases}.\n"
    "Do not invent any numbers, dates, or names beyond what is given above. "
    "Keep it under 200 words. Output only the document text."
)
