# colors = ["blue", "red", "yellow"]
# colors.append("black")

# for index, color in enumerate(colors):
#     print(index, color)

# user = {
#     "name": "wan",
#     "age": 24,
#     "sex": "man"
# }
# user["test"] = "this is test text"
# print(user)
# print("test" in user)
# del user["test"]
# print(user)
# print("test" in user)
# print(user.keys())
# print(user.values())
# print(user.items())
# for key, value in user.items():
#     print(f"{key}: {value}")

# file = open("example.txt", "r")
# contents = file.read()
# print(contents)
# file.close()

# with open("example.txt", "a+") as file:
#     file.write("这是添加进来的内容")
#     file.seek(0)
#     content = file.read()
#     print(content)

# with open("example.txt", "r") as file:
#     for line in file:
#         print(line.strip())

# with open("example.txt", "w+") as file:
#     file.write("hello\n")
#     file.write("world\n")
#     file.seek(0)
#     content = file.read()
#     print(content)

texts = ["this\n", "is\n", "test\n"]
with open("example.txt", "w+") as file:
    file.writelines(texts)
    file.seek(0)
    content = file.read()
    print(content)