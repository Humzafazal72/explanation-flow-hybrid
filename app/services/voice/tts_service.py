from utils import logger
from llm.clients import async_openai_client


async def tts_openai(text: str, snippets: list = None, image_url: str = None):
    """stream tts data from OpenAI

    Args:
        text(str): data to be transcribed.
        snippets(list): snippets to be shown on the board.
        image_url(str): image to be displayed.

    """
    initial_data = {
        "status": "connected",
        "type": "TEXT_FULL",
        "img_url": image_url,
        "text": text,
    }

    if snippets:
        initial_data["snippet"] = [s[1] for s in sorted(snippets)]

    yield initial_data  # send step information in the beginning

    async with async_openai_client.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",
        voice="shimmer",
        input=text,
        instructions="Speak like you are an O-level Maths instructor. You should try to induce curiosity within the student.",
        response_format="pcm",
    ) as response_stream:
        chunk_count = 0
        async for chunk in response_stream.iter_bytes(chunk_size=4096):
            if chunk:
                chunk_count += 1
                try:
                    yield {
                        "status": "connected",
                        "type": "AUDIO_CHUNK",
                        "data": chunk.hex(),
                    }
                except Exception as e:
                    logger.error(f"Error sending chunk: {e}")
                    continue

        # Once streaming has ended
        yield {
            "status": "connected",
            "type": "STREAM_EXIT",
        }
