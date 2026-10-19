import asyncio
import sys

from google.antigravity import Agent, LocalOpenAIAgentConfig
from google.antigravity.types import CapabilitiesConfig, BuiltinTools

PROJECT = r"H:\Git hub\Billing-system\Billing_System"


async def main():
    print("Starting Antigravity agent...", flush=True)

    config = LocalOpenAIAgentConfig(
        model="auto/best-coding",
        base_url="http://localhost:20128/v1",
        workspaces=[PROJECT],

        capabilities=CapabilitiesConfig(
            enable_subagents=False,
            enabled_tools=[
                BuiltinTools.LIST_DIR,
                BuiltinTools.SEARCH_DIR,
                BuiltinTools.FIND_FILE,
                BuiltinTools.VIEW_FILE,
                BuiltinTools.FINISH,
            ],
        ),
    )

    print("Connecting to OmniRoute...", flush=True)

    async with Agent(config) as agent:
        print("Agent connected. Inspecting project...", flush=True)

        response = await agent.chat(
            """Inspect the current Billing System project.

Do NOT modify, create, delete, or execute anything.

Use the available filesystem tools to inspect the project and report:
1. Framework/technology
2. Frontend
3. Backend
4. Database
5. Important folders/files
6. How the project is normally started
7. Any obvious issues

Give a concise report when finished."""
        )

        print("\n--- AGENT RESPONSE ---", flush=True)

        async for token in response:
            sys.stdout.write(token)
            sys.stdout.flush()

        print("\n--- DONE ---", flush=True)


if __name__ == "__main__":
    asyncio.run(main())