from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

messages = []


class Message(BaseModel):
    role: str
    content: str


@app.put("/messages/{message_id}")
def update_message(message_id: int, message: Message):
    if message_id < 0 or message_id >= len(messages):
        raise HTTPException(status_code=404, detail="消息不存在")
    messages[message_id] = message
    return {"status": "update", "message": message}


# @app.get("/messages/{message_id}")
# def get_message(message_id: int):
#     if message_id < 0 or message_id >= len(messages):
#         raise HTTPException(
#             status_code=404,
#             detail="消息不存在"
#         )
#     return messages[message_id]


@app.post("/messages")
def create_message(message: Message):
    messages.append(message)
    return {"status": "create", "message": message}


@app.get("/messages")
def get_message():
    return {"total": len(messages), "messages": messages}


# @app.post("/message")
# def create_message(message: Message):
#     return {
#         "status": "create",
#         "message": message
#     }

# @app.get("/search")
# def search(keyword: str, limit: int = 10):
#     return {
#         "keyword": keyword,
#         "limit": limit
#     }

# @app.get("/hello/{name}")
# def hello(name: str):
#     return {"message": f"你好，{name}"}

# @app.get("/health")
# def home():
#     return {"status": "OK"}
