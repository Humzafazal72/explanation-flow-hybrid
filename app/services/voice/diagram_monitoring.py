import json
import asyncio
from fastapi import WebSocket

from utils import logger, safe_send_ws
from celery_tasks import generate_diagram


async def handle_diagram_result(
    client_ws: WebSocket,
    agent_ws: WebSocket,
    call_id,
    diagram_state: dict,
    provider: str = "openai",
    fn_name: str = "generate_diagram",
):
    """Continuously Monitors the diagram_generation task & sends the result to client & AI.

    Args:
        client_ws: The websocket instance for the frontend/client.
        agent_ws: The websocket instance for the Agent.
        call_id: id for the function call (OpenAI) or tool call (Gemini).
        diagram_state (dict): information regarding the diagram task state.
        provider (str): "openai" or "gemini".
        fn_name (str): The name of the function called (Required for Gemini).
    """
    task_id = diagram_state["task_id"]
    # Assuming 'generate_diagram' is your Celery app/task object available in scope
    result = generate_diagram.AsyncResult(task_id)

    max_wait = 120
    elapsed = 0
    check_interval = 0.5

    # --- Helper to format and send payload based on provider ---
    async def send_agent_response(success: bool, message: str, data: dict = None):
        payload = None

        # 1. OpenAI Logic
        if provider == "openai":
            output_data = {"success": success, "message": message}
            if data:
                output_data.update(data)

            payload = {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(output_data),  # OpenAI wants stringified JSON
                },
            }
            await safe_send_ws(agent_ws, data=payload)
            await safe_send_ws(agent_ws, {"type": "response.create"})

        # 2. Gemini Logic
        elif provider == "gemini":
            result_content = {"success": success, "message": message}
            if data:
                result_content.update(data)

            payload = {
                "tool_response": {
                    "function_responses": [
                        {
                            "id": call_id,  # Critical: Must match request ID
                            "name": fn_name,  # Critical: Must match function name
                            "response": {
                                "result": result_content  # Gemini wants a raw Dict
                            },
                        }
                    ]
                }
            }
            await safe_send_ws(agent_ws, data=json.dumps(payload))

    try:
        # --- Polling Loop ---
        while not result.ready() and elapsed < max_wait:
            await asyncio.sleep(check_interval)
            elapsed += check_interval

        # --- Timeout Handling ---
        if elapsed >= max_wait:
            logger.error(f"Task {task_id} timed out after {max_wait}s")

            # 1. Notify Client
            await safe_send_ws(
                ws=client_ws,
                data={
                    "type": "DIAGRAM_FAILED",
                    "error": "Diagram generation timed out",
                },
            )

            # 2. Notify Agent (Universal)
            await send_agent_response(
                success=False, message="Diagram generation timed out."
            )
            return

        # --- Result Retrieval ---
        try:
            diagram_result = await asyncio.to_thread(result.get, timeout=1.0)
        except Exception as e:
            logger.error(f"Error getting result from diagram task: {e}")
            diagram_result = {"status": "error", "data": str(e)}

        logger.info(f"Task {task_id} completed: {diagram_result}")

        # --- Success/Failure Handling ---
        if diagram_result.get("status") == "error":
            # 1. Notify Client
            await safe_send_ws(
                ws=client_ws,
                data={
                    "type": "DIAGRAM_FAILED",
                    "error": diagram_result.get("data", "Unknown error"),
                },
            )

            # 2. Notify Agent (Universal)
            await send_agent_response(
                success=False,
                message="Diagram generation failed.",
                data={"error_details": diagram_result.get("data")},
            )

        else:
            # 1. Notify Client
            await safe_send_ws(
                ws=client_ws,
                data={"type": "DIAGRAM_READY", "url": diagram_result.get("data")},
            )

            # 2. Notify Agent (Universal)
            await send_agent_response(
                success=True, message="Diagram generation successful."
            )

            logger.info(f"Successfully sent diagram URL to client")

    except asyncio.CancelledError:
        logger.error(f"Task monitoring cancelled for {task_id}")
        raise

    except Exception as e:
        logger.fatal(f"Unexpected error in handle_diagram_result: {e}")
        import traceback

        traceback.print_exc()

        # Emergency Client Notification
        await safe_send_ws(
            ws=client_ws,
            data={
                "type": "DIAGRAM_FAILED",
                "error": "Internal error monitoring diagram generation",
            },
        )

        # Emergency Agent Notification (Try best effort)
        try:
            await send_agent_response(
                success=False, message=f"Internal server error: {str(e)}"
            )
        except:
            pass

    finally:
        diagram_state["in_progress"] = False
        diagram_state["task_id"] = None
        logger.info(f"Cleaned up state for task {task_id}")
