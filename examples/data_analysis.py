"""Example: Agent analyzes a dataset."""

import asyncio
import httpx

async def main():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:7860/api/v1/tasks",
            json={
                "goal": "Write a python script that calculates the 100th Fibonacci number, measures "
                        "the execution time, and saves the output to a file named 'fib_result.txt'. "
                        "Execute the script to create the file.",
            },
        )
        task = response.json()
        print(f"Data analysis task: {task['task_id']}")

        while True:
            status = await client.get(f"http://localhost:7860/api/v1/tasks/{task['task_id']}")
            result = status.json()
            if result["status"] in ("completed", "failed"):
                print(f"Duration: {result.get('duration_ms', 0):.0f}ms")
                print(f"Steps completed: {result.get('completed_steps', 0)}")
                break
            await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())
