# 用 TypeScript 可辨识联合建模不同 SSE 数据包

> 主题：TypeScript / 建模 | 频率：高

## 考点（面试官在考察什么）

- 类型建模、穷尽检查与运行时边界。

## 核心答案（能直接讲出口的版本）

## 实现思路

为每类数据包定义独立结构，并使用统一的 `type` 字段形成可辨识联合。业务代码通过 `switch` 自动收窄类型，最后使用 `assertNever` 做穷尽性检查；以后新增包类型但忘记处理时，编译阶段就会报错。

```ts
type SSEPacket =
    | {
        type: 'markdown';
        content: string;
    }
    | {
        type: 'finish';
        reason: 'completed' | 'stopped';
    }
    | {
        type: 'reconnect';
        checkpoint: {
            qid: string;
            nextSeqId: number;
        };
    };

function handlePacket(packet: SSEPacket): void {
    switch (packet.type) {
        case 'markdown':
            console.log(packet.content);
            break;
        case 'finish':
            console.log(packet.reason);
            break;
        case 'reconnect':
            console.log(packet.checkpoint);
            break;
        default:
            assertNever(packet);
    }
}

function assertNever(value: never): never {
    throw new Error(`未处理的数据包：${JSON.stringify(value)}`);
}
```

## 边界说明

- 可辨识联合只能保证编译期类型安全；服务端数据仍需在解析层进行运行时校验。
- “结束后又收到内容”等跨数据包时序非法状态，应由请求状态机处理，不能只依赖单包类型。
- 业务组件还可基于组件名称继续细分 `data` 类型，避免使用全字段可选的大接口。
