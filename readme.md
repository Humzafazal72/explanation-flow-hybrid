- Install requirements
    ```
    pip install -r requirement.txt

- Start the server:
    ``` cd app
        uvicorn main:app

- In another terminal:
    ``` cd app
        celery -A celery_tasks.celery_ worker --loglevel=info

- Start the Frontend by running dummy_client/index_openai.html