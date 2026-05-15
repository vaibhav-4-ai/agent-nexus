"""Example: Agent researches a topic and writes a report."""

import asyncio
import httpx

async def main():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:7860/api/v1/tasks",
            json={
                "goal": "Fetch the JSON data from 'https://api.github.com/repos/microsoft/autogen'. "
                        "Extract the 'stargazers_count' and 'description' fields, and save them to "
                        "a new file named 'autogen_stats.md'.",
            },
            timeout=10.0,
        )
        task = response.json()
        print(f"Task created: {task['task_id']}")

        while True:
            status = await client.get(f"http://localhost:7860/api/v1/tasks/{task['task_id']}")
            result = status.json()
            print(f"Status: {result['status']} | Steps: {result.get('completed_steps', 0)}/{result.get('total_steps', '?')}")
            if result["status"] in ("completed", "failed"):
                print(f"\nResult:\n{result}")
                break
            await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(main())
