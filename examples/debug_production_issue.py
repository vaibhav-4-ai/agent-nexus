"""Example: Agent debugs a production issue."""

import asyncio
import httpx

async def main():
    """Send a debugging task to the agent."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:7860/api/v1/tasks",
            json={
                "goal": "Read the 'agent_metrics' table in the database. Find the total number of metrics "
                        "recorded, write a python script to format that number into a nice sentence, and "
                        "save it to 'metrics_report.txt'.",
                "config": {"max_steps": 10}
            },
            timeout=10.0,
        )
        task = response.json()
        print(f"Task created: {task['task_id']}")

        # Poll for results
        while True:
            status = await client.get(f"http://localhost:7860/api/v1/tasks/{task['task_id']}")
            result = status.json()
            print(f"Status: {result['status']}")
            if result["status"] in ("completed", "failed"):
                print(f"Result: {result}")
                break
            await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())
