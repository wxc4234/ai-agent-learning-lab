"""受限样例执行的进程内运行环境，不提供外部注册入口。"""

from app.services.workspace.samples.task_sample_binding import TaskSampleBindings


# 所有请求复用同一登记表；不能每个请求都新建一个空实例。
# 构造实例不创建目录、不连接数据库。
# fork后的继承实例会由TaskSampleBindings的PID检查拒绝使用。
_sample_bindings = TaskSampleBindings()


def get_sample_bindings() -> TaskSampleBindings:
    """供可信服务端流程与HTTP依赖取得同一实例。"""

    return _sample_bindings
