"""测试与验收脚本的运行时类型断言；失败时不继续解引用缺失证据。"""

from typing import TypeVar


T = TypeVar('T')


def require_value(value: T | None) -> T:
    """查询/可选字段必须实际存在，保留单次求值而不使用无检查cast。"""

    assert value is not None, '测试前置条件失败：预期非空值'
    return value


def require_instance(value: object, expected: type[T]) -> T:
    """先验证联合类型分支或第三方异常，再读取专属字段。"""

    assert isinstance(value, expected), f'预期类型：{expected.__name__}'
    return value
