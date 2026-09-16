import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readTaskLocation, writeTaskLocation } from '../../../src/features/workbench/task-url.ts';

const workspace = 'a'.repeat(32);
const task = 'b'.repeat(32);

test('empty URL preserves unrelated parameters as non-task navigation', () => {
    assert.deepEqual(readTaskLocation(new URL('http://localhost/?view=wide#chat')), { kind: 'empty' });
});

test('valid task URL yields only resource identifiers', () => {
    assert.deepEqual(readTaskLocation(new URL(`http://localhost/?task=${task}&workspace=${workspace}&title=untrusted`)), {
        kind: 'task', workspaceId: workspace, taskId: task,
    });
});

for (const query of [
    `workspace=${workspace}`,
    `task=${task}`,
    `workspace=&task=${task}`,
    `workspace=${workspace}&task=`,
    `workspace=${workspace.toUpperCase()}&task=${task}`,
    `workspace=${workspace}&task=${task.toUpperCase()}`,
    `workspace=${workspace}&task=../messages`,
    `workspace=${workspace}&task=${task}&task=${task}`,
    `workspace=${workspace}&workspace=${workspace}&task=${task}`,
    `workspace=${workspace}&task=${task}0`,
]) {
    test(`reject ambiguous or invalid URL: ${query}`, () => {
        assert.deepEqual(readTaskLocation(new URL(`http://localhost/?${query}`)), { kind: 'invalid' });
    });
}

test('replace task and clear draft preserve framework state, other parameters and hash', () => {
    const previous = Object.getOwnPropertyDescriptor(globalThis, 'window');
    const state = { __NA: true, tree: ['framework-state'] };
    const calls: string[] = [];
    const location = { href: 'http://localhost/?view=wide&task=old&task=duplicate&workspace=old#chat' };
    Object.defineProperty(globalThis, 'window', {
        configurable: true,
        value: {
            location,
            history: {
                state,
                replaceState(nextState: unknown, title: string, address: string) {
                    assert.equal(nextState, state);
                    assert.equal(title, '');
                    calls.push(address);
                    location.href = new URL(address, location.href).href;
                },
                pushState() { assert.fail('task selection must not add a history entry'); },
            },
        },
    });
    try {
        writeTaskLocation({ workspace_id: workspace, external_id: task });
        assert.equal(calls[0], `/?view=wide&workspace=${workspace}&task=${task}#chat`);
        assert.deepEqual(readTaskLocation(new URL(location.href)), { kind: 'task', workspaceId: workspace, taskId: task });
        writeTaskLocation(null);
        assert.equal(calls[1], '/?view=wide#chat');
        assert.equal(calls.length, 2);
    } finally {
        if (previous) Object.defineProperty(globalThis, 'window', previous);
        else Reflect.deleteProperty(globalThis, 'window');
    }
});
