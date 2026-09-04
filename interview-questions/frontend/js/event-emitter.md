# 用 TypeScript 实现支持 `on/off/once/emit` 的 EventEmitter

> 主题：JavaScript / 发布订阅 | 频率：高

## 考点（面试官在考察什么）

- JavaScript 运行机制、异步时序与可运行实现。

## 核心答案（能直接讲出口的版本）

## 语义约定

- `emit` 同步执行；监听器抛错时直接向调用方抛出，后续监听器停止执行。
- 同一函数允许重复订阅，`off` 删除最近一次匹配。
- `emit` 使用监听器快照：本轮新增下轮生效，本轮删除不影响当前快照。
- `once` 在调用监听器前取消订阅，避免重入或异常导致重复执行。

```ts
type Listener = (...args: any[]) => void;

type StoredListener = Listener & {
    original?: Listener;
};

class EventEmitter {
    private events = new Map<string, StoredListener[]>();

    on(event: string, listener: Listener): this {
        const listeners = this.events.get(event) || [];
        listeners.push(listener);
        this.events.set(event, listeners);
        return this;
    }

    off(event: string, listener: Listener): this {
        const listeners = this.events.get(event);
        if (!listeners) {
            return this;
        }

        for (let i = listeners.length - 1; i >= 0; i--) {
            const current = listeners[i];
            if (current === listener || current.original === listener) {
                listeners.splice(i, 1);
                break;
            }
        }

        if (listeners.length === 0) {
            this.events.delete(event);
        }
        return this;
    }

    once(event: string, listener: Listener): this {
        let fired = false;

        const wrapper: StoredListener = (...args: any[]) => {
            if (fired) {
                return;
            }

            fired = true;
            this.off(event, wrapper);
            listener(...args);
        };

        wrapper.original = listener;
        return this.on(event, wrapper);
    }

    emit(event: string, ...args: any[]): boolean {
        const listeners = this.events.get(event);
        if (!listeners?.length) {
            return false;
        }

        const snapshot = [...listeners];
        for (const listener of snapshot) {
            listener(...args);
        }
        return true;
    }
}
```

## 边界说明

- `off` 不存在的事件时不报错；`emit` 无监听器时返回 `false`。
- 包装函数保存 `original`，因此 `off(event, originalListener)` 可以移除 `once` 订阅。
- 快照语义避免遍历过程中增删数组造成跳过或重复执行。
