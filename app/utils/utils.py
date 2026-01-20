import os
import re
import json
import boto3
import logging
import asyncio
from botocore.config import Config
from fastapi import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed
from websockets.legacy.protocol import WebSocketCommonProtocol

from llm.prompts import PromptManager

TIGRIS_ENDPOINT = os.environ.get("TIGRIS_STORAGE_ENDPOINT")
TIGRIS_ACCESS_KEY = os.environ.get("TIGRIS_STORAGE_ACCESS_KEY_ID")
TIGRIS_SECRET_KEY = os.environ.get("TIGRIS_STORAGE_SECRET_ACCESS_KEY")

logger = logging.Logger("logger")

s3_client = boto3.client(
    "s3",
    endpoint_url=TIGRIS_ENDPOINT,
    aws_access_key_id=TIGRIS_ACCESS_KEY,
    aws_secret_access_key=TIGRIS_SECRET_KEY,
    config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
)


def build_voicebot_prompt(concept_name, context, steps_so_far):
    steps_text = "\n".join(
        [f"Step {i+1}: {step}" for i, step in enumerate(steps_so_far)]
    )

    pm = PromptManager(type_="VOICE_AGENT")
    system_prompt = pm.get_sys_prompt()
    if not system_prompt:
        raise Exception("The system prompt couldn't be loaded")

    return system_prompt.format(
        concept=concept_name, context=context, steps_text=steps_text
    )


def parse_code(generated_code: str):
    match = re.search(r"```(?:python)?\n(.*?)```", generated_code, re.DOTALL)
    code = match.group(1).strip() if match else generated_code.strip()
    return code


async def safe_send_ws(
    ws, data: dict, retries: int = 3, initial_delay: float = 0.5
) -> bool:
    """Robustly sends a JSON dictionary to a websocket.

    Args:
        ws: The websocket instance (FastAPI WebSocket or websockets.client).
        data (dict): The data to send.
        retries (int): Maximum number of retry attempts.
        initial_delay (float): Seconds to wait before the first retry.

    Returns:
        bool: True if sent successfully, False otherwise.
    """
    message = json.dumps(data)

    for attempt in range(1, retries + 2):
        try:
            is_fastapi = isinstance(ws, WebSocket)
            is_lib = isinstance(ws, WebSocketCommonProtocol) or hasattr(ws, "open")

            # Check if socket is open
            if is_fastapi:  # check if fastapi ws
                from starlette.websockets import WebSocketState

                if ws.client_state == WebSocketState.DISCONNECTED:
                    logger.warning(
                        f"Attempt {attempt}: FastAPI WebSocket is disconnected."
                    )
                    return False
            elif is_lib:  # check if websockets(library) ws
                if not ws.open:
                    logger.warning(
                        f"Attempt {attempt}: Websockets library socket is closed."
                    )
                    return False

            # attempt to send data
            if is_fastapi:
                await ws.send_text(message)
            else:
                await ws.send(message)

            # If we reach here, success
            return True

        except (WebSocketDisconnect, ConnectionClosed) as e:
            logger.error(f"Socket closed permanently during send: {e}")
            return False

        except Exception as e:
            if attempt > retries:
                logger.error(f"Failed to send after {retries} retries. Error: {e}")
                return False

            wait_time = initial_delay * (2 ** (attempt - 1))  # Exponential backoff
            logger.warning(
                f"Send failed (Attempt {attempt}/{retries}). Retrying in {wait_time}s... Error: {e}"
            )
            await asyncio.sleep(wait_time)

    return False
