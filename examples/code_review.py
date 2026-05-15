"""Example: Agent reviews code."""

import asyncio
import httpx

async def main():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:7860/api/v1/tasks",
            json={
                "goal": "List all files in the 'src/' directory. Create a new markdown file named "
                        "'workspace_summary.md' and write down the names of the top 3 files you found.",
            },
        )
        task = response.json()
        print(f"Code review task: {task['task_id']}")

        while True:
            status = await client.get(f"http://localhost:7860/api/v1/tasks/{task['task_id']}")
            result = status.json()
            if result["status"] in ("completed", "failed"):
                for step in result.get("execution_trace", []):
                    print(f"  Step {step.get('step_number')}: {step.get('description')} [{step.get('status')}]")
                break
            await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())
