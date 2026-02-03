# Without Docker:

- Install requirements
    ```
    pip install -r requirement.txt

- Start the server:
    ``` 
    cd app
    uvicorn main:app

- In another terminal:
    ``` 
    cd app
    celery -A celery_tasks.celery_ worker --loglevel=info

- Start the Frontend by running dummy_client/index_openai.html

# With Docker:

- Build the dockerfile
    ```
    docker build -t explanation_hybrid .

- Run the image
    ```
    docker run --env-file .env  -p 8000:8000 explanation_hybrid

- Change the url in index_openai from 127.0.0.1 to 0.0.0.0.