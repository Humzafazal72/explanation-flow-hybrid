import os
import io
import time
import random
import base64
from e2b_code_interpreter import Sandbox
from celery import Celery
from dotenv import load_dotenv

from llm.prompts import PromptManager
from llm.clients import google_client
from utils import parse_code, s3_client

load_dotenv()

REDIS_ENDPOINT = os.environ.get("REDIS_ENDPOINT")
S3_BUCKET = "explanation-dev"

celery_ = Celery("worker", broker=REDIS_ENDPOINT, backend=REDIS_ENDPOINT)
celery_.conf.task_always_eager = False


CODE = """
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os

# Create figure and axis
fig, ax = plt.subplots(figsize=(6, 6))
ax.set_aspect('equal')

# Triangle dimensions
base = 4  # side b
height = 3 # side a

# Triangle vertices
A = (0, 0)
B = (base, 0)
C = (0, height)

# Plot the triangle sides
# Draw from (0,0) -> (base,0) -> (0,height) -> (0,0)
ax.plot([0, base, 0, 0], [0, 0, height, 0], 'k-', linewidth=2)

# Add right angle marker at (0,0)
mark_size = 0.3
ax.plot([mark_size, mark_size, 0], [0, mark_size, mark_size], 'k-', linewidth=1)

# Label the sides
# Side b (bottom)
ax.text(base / 2, -0.3, 'b', ha='center', va='top', fontsize=16, style='italic')

# Side a (left vertical)
ax.text(-0.3, height / 2, 'a', ha='right', va='center', fontsize=16, style='italic')

# Side c (hypotenuse)
ax.text(base / 2 + 0.2, height / 2 + 0.2, 'c', ha='left', va='bottom', fontsize=16, style='italic')

# Remove axes for clean geometrical look
ax.axis('off')

# Set limits to ensure labels are not cut off
ax.set_xlim(-1, base + 1)
ax.set_ylim(-1, height + 1)

# Save the figure
plt.savefig("./9830.png", bbox_inches='tight', dpi=300)
plt.close(fig)"""


@celery_.task
def generate_diagram(prompt: str) -> dict:
    try:
        pm = PromptManager(type_="CODER")
        system_prompt = pm.get_sys_prompt(version="1.0")

        diag_name = random.randint(0, 10000) + random.randint(99, 1000)
        s3_path = f"temp/diagrams/fig_{diag_name}.png"

        response = google_client.models.generate_content(
            model="gemini-3-pro-preview",
            contents=prompt + f"\n Figure_name: fig_{diag_name}",
            config={"system_instruction": system_prompt},
        )
        generated_code = response.text
        parsed_code = "import matplotlib\nmatplotlib.use('Agg')\n" + parse_code(
            generated_code=generated_code
        )

        sbx = Sandbox.create(template="diag-gen", allow_internet_access=False)
        execution = sbx.run_code(code=parsed_code, language="python")

        content = sbx.files.read(f"/home/user/fig_{diag_name}.png", format="bytes")
        buffer = io.BytesIO(content)
        buffer.seek(0)

        s3_client.upload_fileobj(
            buffer,
            Bucket=S3_BUCKET,
            Key=s3_path,
            ExtraArgs={"ContentType": "image/png"},
        )

        presigned_url = s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": S3_BUCKET, "Key": s3_path},
            ExpiresIn=30,
        )

        print(presigned_url)
        return {"status": "success", "data": presigned_url}

    except Exception as e:
        return {"status": "error", "data": str(e)}
