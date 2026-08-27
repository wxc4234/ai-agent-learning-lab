import json

from day2 import analyze_messages


class ConversationManager:
    def __init__(self, filename):
        self.filename = filename
        self.messages = []

    def add_message(self, role, content):
        if role not in ("user", "assistant"):
            raise ValueError("不支持的消息角色")

        self.messages.append({
            "role": role,
            "content": content,
        })

    def save(self):
        try:
            with open(self.filename, "w", encoding="utf-8") as file:
                json.dump(
                    self.messages,
                    file,
                    ensure_ascii=False,
                    indent=4,
                )

        except OSError as error:
            print("写入文件失败：", error)

    def load(self):
        try:
            with open(self.filename, "r", encoding="utf-8") as file:
                self.messages = json.load(file)

        except FileNotFoundError:
            self.messages = []

        except json.JSONDecodeError:
            print("JSON 文件格式错误")
            self.messages = []

    def get_statistics(self):
        return analyze_messages(self.messages)


manager = ConversationManager("conversation.json")
manager.add_message("user", "你好")
manager.add_message("assistant", "你好，有什么可以帮你？")
manager.add_message("user", "什么是 AI Agent？")

manager.save()
print(manager.messages)
print(manager.get_statistics())