import json

# user_content = [
#     {"role": "user", "content": "你好"},
#     {"role": "assistant", "content": "有什么我能帮助你的"},
#     {"role": "user", "content": "什么是agent"}
# ]

# def user_message(user_content):
#     user_cont = 0
#     for item in user_content:
#         if item["role"] == "user":
#             user_cont += 1
#     return user_cont

# result = user_message(user_content)
# print("用户发送了", result, "条消息")

messages = [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好，有什么可以帮你？"},
    {"role": "user", "content": "什么是 AI Agent？"},
    {"role": "system"},
]


def analyze_messages(messages):
    total = 0
    user_count = 0
    assistant_count = 0
    invalid_count = 0
    longest_user_message = ""
    max_length = 0

    for item in messages:
        total += 1

        try:
            role = item["role"]
            content = item["content"]
        except (KeyError, TypeError):
            invalid_count += 1
            continue

        if role == "user":
            user_count += 1

            content_length = len(content)

            if content_length > max_length:
                max_length = content_length
                longest_user_message = content

        elif role == "assistant":
            assistant_count += 1

        else:
            invalid_count += 1

    return {
        "total": total,
        "user_count": user_count,
        "assistant_count": assistant_count,
        "invalid_count": invalid_count,
        "longest_user_message": longest_user_message,
    }


def save_messages(messages, filename):
    try:
        with open(filename, "w", encoding="utf-8") as file:
            json.dump(
                messages,
                file,
                ensure_ascii=False,
                indent=4,
            )

        print(f"消息成功保存到 {filename}")
        return True

    except OSError as error:
        print("文件写入失败：", error)
        return False


def load_messages(filename):
    try:
        with open(filename, "r", encoding="utf-8") as file:
            return json.load(file)

    except FileNotFoundError:
        print("文件不存在")
        return None

    except json.JSONDecodeError:
        print("JSON 文件格式错误")
        return None

    except OSError as error:
        print("文件读取失败：", error)
        return None


save_success = save_messages(messages, "messages.json")

if save_success:
    data = load_messages("messages.json")

    if data is not None:
        result = analyze_messages(data)
        print(result)
