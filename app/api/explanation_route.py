import asyncio
from aiosqlite import Connection
from fastapi import APIRouter, WebSocket, Depends, WebSocketDisconnect, Path

from Database import get_db
from utils import s3_client, build_voicebot_prompt, safe_send_ws, logger
from services.voice import (
    tts_openai,
    handle_voicebot_session_openai,
    handle_voicebot_session_gemini,
)

router = APIRouter()


@router.websocket("/ws/explanation/{concept_id}")
async def get_explanation(
    websocket: WebSocket, concept_id: int = Path(), db: Connection = Depends(get_db)
):
    await websocket.accept()

    try:

        # get data from the db
        concept_name_obj = await db.execute(
            "SELECT name FROM lessons WHERE ID=?", (concept_id,)
        )
        conclusion_obj = await db.execute(
            f"SELECT conclusion_text FROM conclusions WHERE lesson_id=?", (concept_id,)
        )
        context_obj = await db.execute(
            f"SELECT context_text FROM contexts WHERE lesson_id=?", (concept_id,)
        )
        explanation_steps_obj = await db.execute(
            f"""SELECT step_num,step_text FROM explanation_steps 
                            WHERE lesson_id=?ORDER BY step_num ASC""",
            (concept_id,),
        )
        snippet_obj = await db.execute(
            f"""SELECT step_num, snippet_num, snippet_text FROM snippets 
                            WHERE lesson_id=? ORDER BY step_num ASC""",
            (concept_id,),
        )
        tts_steps_obj = await db.execute(
            f"""SELECT step_num,tts_text FROM tts_steps 
                            WHERE lesson_id=? ORDER BY step_num ASC""",
            (concept_id,),
        )

        concept_name = concept_name_obj[0][0]
        conclusion = conclusion_obj[0][0]
        context = context_obj[0][0]

        # get all the steps in a list in sorted order
        explanation_steps = {}
        for step_num, step_text in tts_steps_obj.rows:
            explanation_steps.setdefault(step_num, {})["text"] = step_text

        # get all snippets in a list in sorted order
        for snippet_row in snippet_obj.rows:
            step_num = snippet_row[0]
            snippet_num = snippet_row[1]
            snippet_text = snippet_row[2]
            explanation_steps[step_num].setdefault("snippets", []).append(
                (snippet_num, snippet_text)
            )

        # get diagram data from object storage
        prefix = f"{concept_id}/"

        response = s3_client.list_objects_v2(Bucket="explanation-dev", Prefix=prefix)
        url_data = {}

        for obj in response["Contents"]:
            if obj["Key"] == prefix or "metadata" in obj["Key"]:
                continue
            fig_name = obj["Key"].split("/")[-1].split(".")[0]
            url_data[fig_name] = s3_client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": "explanation-dev", "Key": obj["Key"]},
                ExpiresIn=7200,
            )

        # send initial metadata
        data = {
            "status": "Connected",
            "type": "METADATA",
            "name": concept_name,
            "num_steps": len(explanation_steps),
        }
        await safe_send_ws(ws=websocket, data=data)

        # Main Event Loop
        while True:
            state_data = (
                await websocket.receive_json()
            )  # get the data(explanation part and index) from the frontend

            if "part" not in state_data:
                continue

            match state_data[
                "part"
            ]:  # check what part of the explanation needs to be streamed i.e context, conlusion or one of the explanation steps
                case "CONTEXT":
                    async for chunk in tts_openai(context):
                        await websocket.send_json(chunk)

                case "CONCLUSION":
                    async for chunk in tts_openai(conclusion):
                        await websocket.send_json(chunk)

                case "EXPLANATION_STEP":
                    index = state_data["index"]
                    async for chunk in tts_openai(
                        snippets=explanation_steps[index].get("snippets", None),
                        text=explanation_steps[index]["text"],
                        image_url=url_data.get(f"fig_{index}", None),
                    ):
                        await websocket.send_json(chunk)

                case "VOICEBOT":
                    index = state_data["index"]
                    explained_steps = [
                        explanation_steps[i]["text"] for i in range(index + 1)
                    ]  # [r for r in explained_steps[: index + 1]["text"]]
                    voice_prompt = build_voicebot_prompt(
                        concept_name, context, explained_steps
                    )

                    data = {
                        "type": "VOICEBOT_INIT",
                        "status": "starting",
                        "message": "Initializing interactive tutor...",
                    }
                    await safe_send_ws(ws=websocket, data=data)

                    # start the voicebot flow
                    await handle_voicebot_session_openai(websocket, voice_prompt)
                    #await handle_voicebot_session_gemini(websocket, voice_prompt)

                    data = {
                        "type": "VOICEBOT_EXIT",
                        "status": "ended",
                        "message": "Voicebot session ended",
                    }
                    await safe_send_ws(ws=websocket, data=data)

    except WebSocketDisconnect:
        logger.warning("Client Websocket closed/Disconnected.")

    except Exception as e:
        print(e)
        success = await safe_send_ws(
            ws=websocket, data={"status": "error", "data": str(e)}
        )
        if success:
            await websocket.close()
        return


############################################################################
# @router.websocket("/ws/dummy")
# async def dummy(
#     websocket: WebSocket,
# ):
#     await websocket.accept()
#     diagram_state = {
#             "in_progress": False,
#             "task_id": None
#     }
#     try:
#         await websocket.send_json({
#                                 "status": "Connected",
#                         })
#         while True:
#             data = await websocket.receive_json()
#             if data['start'] == "yes":
#                 task = generate_diagram.delay(data["prompt"])
#                 diagram_state["task_id"] = task.id
#                 asyncio.create_task(handle_diagram_result(websocket, diagram_state=diagram_state))

#     except WebSocketDisconnect:
#         await websocket.close()

#     except Exception as e:
#         print(e)
#         await websocket.send_json({"status": "error","data":str(e)})
#         await websocket.close()
#         return
