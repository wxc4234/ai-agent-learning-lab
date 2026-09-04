# 实现一个带过期时间的 LRU 缓存

> 主题：缓存 / LRU | 频率：高

## 考点（面试官在考察什么）

- 数据结构、淘汰策略、过期语义与复杂度。

## 核心答案（能直接讲出口的版本）

## 题目范围

实现固定容量的 LRU 缓存，支持 `get` 和 `put`。数据带有 TTL，过期后不可读取；不要求现场实现小顶堆等复杂优化。

## 实现思路

利用 `Map` 的插入顺序维护 LRU：Map 头部是最久未使用的数据，访问命中后删除并重新插入，使其移动到尾部。每项保存 `expiresAt`，读取时检查过期时间；代码额外使用单一定时器清理最近到期的数据。

```ts
type Entry<V> = {
    value: V;
    expiresAt: number;
};

class LRUCache<K, V> {
    private cache = new Map<K, Entry<V>>();
    private timer: ReturnType<typeof setTimeout> | null = null;

    constructor(
        private capacity: number,
        private defaultTTL: number
    ) {
        if (capacity <= 0 || defaultTTL < 0) {
            throw new Error('invalid arguments');
        }
    }

    get(key: K): V | undefined {
        const entry = this.cache.get(key);
        if (!entry) {
            return undefined;
        }

        if (entry.expiresAt <= Date.now()) {
            this.cache.delete(key);
            this.schedule();
            return undefined;
        }

        this.cache.delete(key);
        this.cache.set(key, entry);
        return entry.value;
    }

    put(key: K, value: V, ttl = this.defaultTTL): void {
        if (ttl < 0) {
            throw new Error('invalid ttl');
        }

        this.clearExpired();
        this.cache.delete(key);

        if (ttl === 0) {
            this.schedule();
            return;
        }

        this.cache.set(key, {
            value,
            expiresAt: Date.now() + ttl
        });

        if (this.cache.size > this.capacity) {
            const oldestKey = this.cache.keys().next().value as K;
            this.cache.delete(oldestKey);
        }

        this.schedule();
    }

    private clearExpired(): void {
        const now = Date.now();
        for (const [key, entry] of this.cache) {
            if (entry.expiresAt <= now) {
                this.cache.delete(key);
            }
        }
    }

    private schedule(): void {
        if (this.timer) {
            clearTimeout(this.timer);
            this.timer = null;
        }

        let nearest = Infinity;
        for (const entry of this.cache.values()) {
            nearest = Math.min(nearest, entry.expiresAt);
        }

        if (nearest === Infinity) {
            return;
        }

        this.timer = setTimeout(() => {
            this.timer = null;
            this.clearExpired();
            this.schedule();
        }, Math.max(0, nearest - Date.now()));
    }
}
```

## 复杂度与边界

- `get` 命中时平均为 O(1)；过期删除后重新调度需要 O(n) 扫描。
- `put` 中的过期清理和定时器重排都需要扫描 Map，因此为 O(n)。
- 单一定时器属于额外扩展；若题目严格要求 `get/put` 都为 O(1)，可以只实现惰性过期，或将定时清理优化作为口头讨论。
- 现场面试到这里即可，不要求继续手写小顶堆版本。
