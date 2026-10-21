"""Model access for the crew, through Microsoft Agent Framework.

Today the agents reach the Foundry resource's model deployment with its key, through
the Azure OpenAI route. With an Entra identity (Container Apps managed identity), set
RUNDOWN_FOUNDRY_CLIENT=1 to use FoundryChatClient on the Foundry project instead.
Traces go to Application Insights (and so to Foundry tracing) when
APPLICATIONINSIGHTS_CONNECTION_STRING is set.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
from typing import Any

from pydantic import BaseModel

ENV = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")


class AgentDown(Exception):
    """The agent is switched off (the kill switch) or unreachable."""


RATE_LIMITED = re.compile(r"\b429\b|rate limit", re.I)
RETRY_AFTER = re.compile(r"retry after (\d+) second", re.I)


def backoff_s(err: Exception, attempt: int) -> float | None:
    """Seconds to wait before resending a rate-limited call, or None if it wasn't rate limited."""
    msg = str(err)
    if not RATE_LIMITED.search(msg):
        return None
    m = RETRY_AFTER.search(msg)
    base = float(m.group(1)) if m else 2.0 * 2 ** attempt
    return min(base, 30.0) + random.uniform(0, 1.0)


def configured() -> bool:
    return all(os.environ.get(k) for k in ENV)


_tracing = False


def _enable_tracing():
    global _tracing
    conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if _tracing or not conn:
        return
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        from agent_framework.observability import enable_instrumentation
        configure_azure_monitor(connection_string=conn)
        enable_instrumentation()
        _tracing = True
    except ImportError:
        pass


class Models:
    """One Agent Framework agent per crew role, on a shared chat client. Use it from one
    event loop only (agents.crew.crew_loop); `max_calls` caps calls in flight across every
    crew, since the deployment's quota is shared."""

    def __init__(self, max_calls: int = 8):
        from agent_framework import Agent
        _enable_tracing()
        model = os.environ["AZURE_OPENAI_DEPLOYMENT"]
        if os.environ.get("RUNDOWN_FOUNDRY_CLIENT") == "1":
            from agent_framework.foundry import FoundryChatClient
            from azure.identity import DefaultAzureCredential
            client = FoundryChatClient(project_endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"], model=model,
                                       credential=DefaultAzureCredential())
        else:
            from agent_framework.openai import OpenAIChatCompletionClient
            client = OpenAIChatCompletionClient(model=model, azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                                                api_key=os.environ["AZURE_OPENAI_API_KEY"])
        self.model = model
        self.max_calls = max_calls
        self._sem: asyncio.Semaphore | None = None
        self._Agent = Agent
        self._client = client
        self._agents: dict[str, Any] = {}

    def agent(self, role: str, instructions: str):
        if role not in self._agents:
            self._agents[role] = self._Agent(client=self._client, name=role, instructions=instructions)
        return self._agents[role]

    def session(self, role: str, instructions: str):
        return self.agent(role, instructions).create_session()

    async def ask(self, role: str, instructions: str, session, text: str, schema: type[BaseModel]) -> tuple[BaseModel, dict]:
        """One answer. Rate limits (429) are waited out here, up to four times; they are the
        deployment's quota, not the agent's mistake, so they never use up a Verifier retry."""
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.max_calls)
        for attempt in range(5):
            try:
                async with self._sem:
                    r = await self.agent(role, instructions).run(text, session=session, options={"response_format": schema})
                break
            except Exception as e:
                wait = backoff_s(e, attempt)
                if wait is None or attempt == 4:
                    raise
                await asyncio.sleep(wait)
        if r.value is None:
            raise ValueError("no structured output")
        u = r.usage_details or {}
        return r.value, dict(in_tokens=u.get("input_token_count"), out_tokens=u.get("output_token_count"))
