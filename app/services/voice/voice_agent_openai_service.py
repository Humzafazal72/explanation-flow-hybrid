import os
import json
import base64
import asyncio
import websockets
from fastapi import WebSocket, WebSocketDisconnect

from app.services.voice.diagram_monitoring import safe_send_ws
from celery_tasks import generate_diagram
from app.services.voice.diagram_monitoring import handle_diagram_result
from llm.config import ConfigManager
from utils import logger

OPENAI_WS_URL = os.environ.get("OPENAI_WS_URL")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")


async def handle_voicebot_session_openai(
    client_ws: WebSocket, voice_prompt: str
) -> None:
    """Bridges the client and OpenAI Realtime websocket session.

    Args:
        client_ws(fastapi.Websocket): Websocket for the frontend/client
        voice_prompt(str): System prompt for the voice agent.
    """
    diagram_state = {"in_progress": False, "task_id": None}
    cm = ConfigManager(provider="openai")
    session_cfg = cm.get_config()
    session_cfg["session"]["instructions"] = voice_prompt

    async with websockets.connect(
        OPENAI_WS_URL,
        additional_headers={
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "realtime=v1",
        },
    ) as openai_ws:
        # send session config to OpenAI
        await openai_ws.send(json.dumps(session_cfg))

        # Forward client audio to OpenAI
        async def client_to_ai():
            try:
                while True:
                    try:
                        msg = await client_ws.receive_text()
                        data = json.loads(msg)

                        if (
                            data.get("type") == "exit_voicebot"
                        ):  # client asks to close the voicebot.
                            await openai_ws.close()
                            break

                        elif (
                            data.get("type") == "audio_chunk"
                        ):  # client is sending data(audio_chunks).
                            b64 = base64.b64encode(bytes.fromhex(data["chunk"])).decode(
                                "utf-8"
                            )
                            try:
                                await openai_ws.send(
                                    json.dumps(
                                        {
                                            "type": "input_audio_buffer.append",
                                            "audio": b64,
                                        }
                                    )
                                )
                            except Exception as e:
                                logger.error(
                                    f"Couldn't send audio chunks from the client to openai"
                                )
                                continue

                        else:
                            logger.warning(
                                f"Data type not recognized: {data.get('type')}"
                            )
                            continue

                    except WebSocketDisconnect:
                        break
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON decode error: {e}")
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

        # Forward OpenAI to client
        async def ai_to_client():
            try:
                while True:
                    try:
                        # recive msg from OpenAI and decode it to json obj.
                        msg = await openai_ws.recv()
                        event = json.loads(msg)
                        event_type = event.get("type")

                        if (
                            event_type == "input_audio_buffer.speech_started"
                        ):  # user has started speaking
                            await safe_send_ws(
                                ws=client_ws,
                                data={"type": "INTERRUPT_PLAYBACK"},
                                initial_delay=0.2,
                            )

                        elif (
                            event_type == "response.function_call_arguments.done"
                        ):  # OpenAI has made a function call
                            args = event.get("arguments", "{}")
                            fn_name = event.get("name", "")
                            call_id = event.get("call_id")
                            logger.info(f"Function call: {fn_name}")

                            if fn_name == "show_on_board":
                                try:
                                    function_data = {
                                        "type": "FUNCTION_CALL",
                                        "function": fn_name,
                                        "args": args,  # is a json str
                                    }
                                    # send function data to client
                                    await safe_send_ws(client_ws, data=function_data)

                                    # iform OpneAI that a successfull function call has been made.
                                    openai_data = {
                                        "type": "conversation.item.create",
                                        "item": {
                                            "type": "function_call_output",
                                            "call_id": call_id,
                                            "output": json.dumps({"success": True}),
                                        },
                                    }
                                    await safe_send_ws(openai_ws, data=openai_data)
                                    await safe_send_ws(
                                        openai_ws, data={"type": "response.create"}
                                    )

                                except Exception as e:
                                    logger.error(f"Failed to handle show_on_board: {e}")

                                    # inform OpenAI about the error.
                                    error_payload = {
                                        "type": "conversation.item.create",
                                        "item": {
                                            "type": "function_call_output",
                                            "call_id": call_id,
                                            "output": json.dumps(
                                                {
                                                    "success": False,
                                                    "message": f"There was an error showing the snippet: {e}",
                                                }
                                            ),
                                        },
                                    }
                                    await safe_send_ws(openai_ws, data=error_payload)
                                    await safe_send_ws(
                                        openai_ws, {"type": "response.create"}
                                    )
                                    continue  # Continue the loop, don't break

                            elif fn_name == "generate_diagram":
                                logger.info(f"DIAGRAM Args: {args}")
                                try:
                                    args_json = json.loads(args)
                                    prompt = args_json["prompt"]

                                except Exception as e:
                                    logger.error(f"Failed to parse args: {e}")

                                    # Inform OpenAI about the error.
                                    error_payload = {
                                        "type": "conversation.item.create",
                                        "item": {
                                            "type": "function_call_output",
                                            "call_id": call_id,
                                            "output": json.dumps(
                                                {
                                                    "success": False,
                                                    "error": f"Failed to parse arguments: {e}",
                                                }
                                            ),
                                        },
                                    }
                                    await safe_send_ws(openai_ws, data=error_payload)
                                    await safe_send_ws(
                                        openai_ws, {"type": "response.create"}
                                    )
                                    continue

                                if diagram_state.get(
                                    "in_progress"
                                ):  # check if a diagram is already being generated
                                    logger.info(
                                        "Already generating, rejecting new request"
                                    )

                                    # inform OpenAI that a diagram is being generated
                                    error_payload = {
                                        "type": "conversation.item.create",
                                        "item": {
                                            "type": "function_call_output",
                                            "call_id": call_id,
                                            "output": json.dumps(
                                                {
                                                    "success": False,
                                                    "message": "Already generating a diagram. Please wait.",
                                                }
                                            ),
                                        },
                                    }
                                    await safe_send_ws(openai_ws, data=error_payload)
                                    await safe_send_ws(
                                        openai_ws, {"type": "response.create"}
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
                                            client_ws, openai_ws, call_id, diagram_state
                                        )
                                    )

                                    # send acknowledgement to openai that diagram generation has started.
                                    openai_data = {
                                        "type": "conversation.item.create",
                                        "item": {
                                            "type": "function_call_output",
                                            "call_id": call_id,
                                            "output": json.dumps(
                                                {
                                                    "success": False,
                                                    "message": "Diagram generation has started.",
                                                }
                                            ),
                                        },
                                    }
                                    await safe_send_ws(openai_ws, data=openai_data)
                                    await safe_send_ws(
                                        openai_ws, {"type": "response.create"}
                                    )

                                    # tell cient has diagram generation has started
                                    await safe_send_ws(
                                        client_ws, data={"type": "DIAGRAM_INITIATED"}
                                    )
                                    logger.info("Continuing to listen for more events")

                                except Exception as e:
                                    logger.error(f"Error Generating Diagram: {e}")

                                    # Send error back to OpenAI and continue
                                    error_payload = {
                                        "type": "conversation.item.create",
                                        "item": {
                                            "type": "function_call_output",
                                            "call_id": call_id,
                                            "output": json.dumps(
                                                {
                                                    "success": False,
                                                    "error": f"Failed to generate diagram: {e}",
                                                }
                                            ),
                                        },
                                    }
                                    await safe_send_ws(openai_ws, data=error_payload)
                                    await safe_send_ws(
                                        openai_ws, {"type": "response.create"}
                                    )

                                    # send error info to the client
                                    diagram_error = {"status": "error", "data": str(e)}
                                    await safe_send_ws(client_ws, data=diagram_error)
                                    continue

                            else:
                                logger.warning(f"Unknown function: {fn_name}")
                                # tell openai about the unknown function
                                openai_data = {
                                    "type": "conversation.item.create",
                                    "item": {
                                        "type": "function_call_output",
                                        "call_id": call_id,
                                        "output": json.dumps(
                                            {
                                                "success": False,
                                                "error": f"Unknown function: {fn_name}",
                                            }
                                        ),
                                    },
                                }
                                await safe_send_ws(openai_ws, data=openai_data)
                                await safe_send_ws(
                                    openai_ws, {"type": "response.create"}
                                )

                        else:
                            # Forward all other messages(i.e audio chunks from OpenAI) to client
                            try:
                                await client_ws.send_text(msg)
                            except Exception as e:
                                print(f"Failed to forward message: {e}")
                                continue

                    except websockets.exceptions.ConnectionClosed:
                        logger.fatal("OpenAI WebSocket closed")
                        break
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON decode error: {e}")
                        continue
                    except Exception as e:
                        logger.fatal(f"Error in ai_to_client loop iteration: {e}")
                        import traceback

                        traceback.print_exc()
                        break

            except Exception as outer_e:
                logger.fatal(f"Fatal error in ai_to_client: {outer_e}")
                import traceback

                traceback.print_exc()

            finally:
                logger.info("ai_to_client task exiting")

        # Both functions will run concurrently. If one function ends (loop in one of the
        # function breaks) both functions are stopped.
        send_task = asyncio.create_task(client_to_ai(), name="client_to_ai")
        recv_task = asyncio.create_task(ai_to_client(), name="ai_to_client")

        done, pending = await asyncio.wait(
            [send_task, recv_task], return_when=asyncio.FIRST_COMPLETED
        )

        for task in pending:
            logger.info(
                f"Task: {task.get_name()} was closed later after the other task completed."
            )
            task.cancel()
