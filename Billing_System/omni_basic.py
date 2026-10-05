import asyncio

from google.antigravity import Agent, LocalOpenAIAgentConfig


async def main():
    config = LocalOpenAIAgentConfig(
        model="auto/best-coding",
        base_url="http://localhost:20128/v1",
    )

    print("Connecting...", flush=True)

    async with Agent(config) as agent:
        print("Sending prompt...", flush=True)

        response = await agent.chat(
            "Reply with exactly: BASIC CHAT SUCCESS"
        )

        print("Response received.", flush=True)

        result = await response.text()

        print("RESULT:", result, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
