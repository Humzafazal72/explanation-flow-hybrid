import os
import json
import copy
import base64
import asyncio
import websockets
from fastapi import WebSocket, WebSocketDisconnect

from llm.config import ConfigManager
from utils import logger, safe_send_ws
from celery_tasks import generate_diagram
from app.services.voice.diagram_monitoring import handle_diagram_result

GEMINI_WS_URL = os.environ.get("GEMINI_WS_URL")


async def handle_voicebot_session_gemini(
    client_ws: WebSocket, voice_prompt: str
) -> None:
    """Bridges the client and gemini Realtime websocket session.

    Args:
        client_ws(fastapi.Websocket): Websocket for the frontend/client
        voice_prompt(str): System prompt for the voice agent.
    """
    diagram_state = {"in_progress": False, "task_id": None}
    cm = ConfigManager(provider="gemini")
    session_cfg = copy.deepcopy(cm.get_config())
    session_cfg["setup"]["systemInstruction"]["parts"][0]["text"] = voice_prompt

    async with websockets.connect(
        GEMINI_WS_URL, additional_headers={"Content-Type": "application/json"}
    ) as gemini_ws:
        await gemini_ws.send(json.dumps(session_cfg))
        await gemini_ws.recv()

        # send data from client/frontent to gemini
        async def client_to_ai():
            try:
                while True:
                    try:
                        client_msg = await client_ws.receive_text()
                        client_data = json.loads(client_msg)

                        # client asks to close the voicebot
                        if client_data.get("type") == "exit_voicebot":
                            await gemini_ws.close()
                            break

                        # clients sends audio chunk
                        elif client_data.get("type") == "audio_chunk":
                            b64_chunk = base64.b64encode(
                                bytes.fromhex(client_data["chunk"])
                            ).decode("utf-8")
                            message = {
                                "realtime_input": {
                                    "media_chunks": [
                                        {"data": b64_chunk, "mime_type": "audio/pcm"}
                                    ]
                                }
                            }
                            await gemini_ws.send(json.dumps(message))

                        else:
                            logger.warning(
                                f"Data type not recognized: {client_data.get('type')}"
                            )
                            continue

                    except WebSocketDisconnect:
                        break
                    except json.JSONDecodeError as e:
                        logger.error(f"Couldn't decode json: {e}")
                        continue
                    except Exception as e:
                        logger.error("Error in client_to_ai:", e)
                        break

            except Exception as outer_e:
                logger.fatal(f"Fatal error in ai_to_client: {outer_e}")
                import traceback

                traceback.print_exc()

            finally:
                logger.info("client_to_ai task exiting")

        # pass data from gemini to client
        async def ai_to_client():
            try:
                while True:
                    try:
                        gemini_msg = await gemini_ws.recv()
                        gemini_response = json.loads(gemini_msg)

                        # for audio/text data from gemini
                        if hasattr(gemini_response, "serverContent"):
                            model_turn = gemini_response["serverContent"].get(
                                "modelTurn", None
                            )
                            if model_turn:
                                for part in model_turn.get("parts", []):
                                    if "inlineData" in part:  # Handle Audio
                                        audio_b64 = part["inlineData"]["data"]
                                        await client_ws.send_text(
                                            json.dumps(
                                                {  # Send to frontend
                                                    "type": "AUDIO_DELTA",
                                                    "delta": audio_b64,
                                                }
                                            )
                                        )
                                    # Handle Text
                                    # if "text" in part:
                                    #     logger.info(f"Gemini Text: {part['text']}")

                        # for function call from gemini
                        elif hasattr(gemini_response, "toolCall"):
                            function_calls = gemini_response["toolCall"][
                                "functionCalls"
                            ]
                            for call in function_calls:
                                fn_name = call["name"]
                                call_id = call["id"]
                                args = call["args"]

                                if fn_name == "show_on_board":
                                    try:
                                        # send function data to client
                                        function_data = {
                                            "type": "FUNCTION_CALL",
                                            "function": fn_name,
                                            "args": json.dumps(args),
                                        }
                                        await safe_send_ws(
                                            client_ws, data=function_data
                                        )

                                        # send acknowledgement to Gemini
                                        gemini_data = {
                                            "tool_response": {
                                                "function_responses": [
                                                    {
                                                        "id": call_id,
                                                        "name": fn_name,
                                                        "response": {
                                                            "result": {
                                                                "success": True,
                                                            }
                                                        },
                                                    }
                                                ]
                                            }
                                        }
                                        await safe_send_ws(gemini_ws, data=gemini_data)

                                    except Exception as e:
                                        logger.error(
                                            f"Failed to handle show_on_board: {e}"
                                        )

                                        # inform Gemini about the error.
                                        error_payload = {
                                            "tool_response": {
                                                "function_responses": [
                                                    {
                                                        "id": call_id,
                                                        "name": fn_name,
                                                        "response": {
                                                            "result": {
                                                                "success": False,
                                                                "error": f"There was an error showing the snippet: {e}",
                                                            }
                                                        },
                                                    }
                                                ]
                                            }
                                        }
                                        await safe_send_ws(
                                            gemini_ws, data=error_payload
                                        )
                                        continue  # Continue the loop, don't break

                                elif fn_name == "generate_diagram":
                                    logger.info(f"DIAGRAM Args: {args}")
                                    prompt = args.get("prompt")

                                    if diagram_state.get(
                                        "in_progress"
                                    ):  # check if a diagram is already being generated
                                        logger.info(
                                            "Already generating, rejecting new request"
                                        )

                                        # inform Gemini that a diagram is being generated already
                                        error_payload = {
                                            "tool_response": {
                                                "function_responses": [
                                                    {
                                                        "id": call_id,
                                                        "name": fn_name,
                                                        "response": {
                                                            "result": {
                                                                "success": False,
                                                                "error": f"A diagram is already being generated.",
                                                            }
                                                        },
                                                    }
                                                ]
                                            }
                                        }
                                        await safe_send_ws(
                                            gemini_ws, data=error_payload
                                        )
                                        continue  # Continue the loop, don't break

                                    # Start diagram generation
                                    try:
                                        task = generate_diagram.delay(prompt)

                                        diagram_state["in_progress"] = True
                                        diagram_state["task_id"] = task.id
                                        logger.info(f"Started celery task {task.id}")

                                        # Monitor celery task result
                                        asyncio.create_task(
                                            handle_diagram_result(
                                                client_ws,
                                                gemini_ws,
                                                call_id,
                                                diagram_state,
                                                provider="gemini",
                                            )
                                        )

                                        # Tell gemini diagram generaion has initiated
                                        gemini_data = {
                                            "tool_response": {
                                                "function_responses": [
                                                    {
                                                        "id": call_id,
                                                        "name": fn_name,
                                                        "response": {
                                                            "result": {
                                                                "success": True,
                                                                "message": "Diagram generation has started",
                                                            }
                                                        },
                                                    }
                                                ]
                                            }
                                        }
                                        await safe_send_ws(gemini_ws, data=gemini_data)

                                        # tell cient has diagram generation has started
                                        await safe_send_ws(
                                            client_ws,
                                            data={"type": "DIAGRAM_INITIATED"},
                                        )
                                        logger.info(
                                            "Continuing to listen for more events"
                                        )

                                    except Exception as e:
                                        diagram_state["in_progress"] = False
                                        diagram_state["task_id"] = None

                                        logger.error(f"Error Generating Diagram: {e}")

                                        # Send error back to Gemini and continue
                                        error_payload = {
                                            "tool_response": {
                                                "function_responses": [
                                                    {
                                                        "id": call_id,
                                                        "name": fn_name,
                                                        "response": {
                                                            "result": {
                                                                "success": False,
                                                                "error": f"Failed to generate diagram: {e}",
                                                            }
                                                        },
                                                    }
                                                ]
                                            }
                                        }
                                        await safe_send_ws(
                                            gemini_ws, data=error_payload
                                        )

                                        # send error info to the client
                                        diagram_error = {
                                            "status": "error",
                                            "data": str(e),
                                        }
                                        await safe_send_ws(
                                            client_ws, data=diagram_error
                                        )

                                        continue

                                    else:  # unknown function
                                        logger.error(
                                            f"Function {fn_name} doesn't exist."
                                        )

                                        # tell Gemini that the function doesn't exist.
                                        error_payload = {
                                            "tool_response": {
                                                "function_responses": [
                                                    {
                                                        "id": call_id,
                                                        "name": fn_name,
                                                        "response": {
                                                            "result": {
                                                                "success": False,
                                                                "error": f"The function {fn_name} doesn't exist.",
                                                            }
                                                        },
                                                    }
                                                ]
                                            }
                                        }
                                        await safe_send_ws(
                                            gemini_ws, data=error_payload
                                        )
                                        continue  # Continue the loop, don't break

                        elif hasattr(gemini_response, "turnComplete"):
                            await safe_send_ws(
                                client_ws, data={"type": "TURN_COMPLETE"}
                            )

                    except WebSocketDisconnect:
                        break
                    except json.JSONDecodeError as e:
                        logger.error(f"Couldn't decode json: {e}")
                        continue
                    except Exception as e:
                        logger.error("Error in client_to_ai:", e)
                        break

            except Exception as outer_e:
                logger.fatal(f"Fatal error in ai_to_client: {outer_e}")
                import traceback

                traceback.print_exc()

            finally:
                logger.info("client_to_ai task exiting")

        # Both functions will run concurrently. If one function ends (loop in one of the
        # function breaks) both functions are stopped.
        recv_task = asyncio.create_task(ai_to_client, "recv_task")
        send_task = asyncio.create_task(client_to_ai, "send_task")

        done, pending = await asyncio.wait(
            [recv_task, send_task], return_when=asyncio.FIRST_COMPLETED
        )

        for task in pending:
            logger.info(
                f"Task: {task.get_name()} was closed later after the other task completed."
            )
            task.cancel()
