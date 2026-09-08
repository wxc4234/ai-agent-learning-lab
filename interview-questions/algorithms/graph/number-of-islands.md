# 岛屿数量（LeetCode 200）

> 主题：网格图 / DFS-BFS | 难度：Medium | 频率：高

## 考点（面试官在考察什么）

- 把二维网格看成图，正确处理访问标记和边界；常见追问是并查集。

## 核心答案（能直接讲出口的版本）

遇到未访问的陆地就发现一个新岛屿，然后用 DFS 把与它相连的所有陆地标记掉。这样每块陆地只访问一次。

```ts
function numIslands(grid: string[][]): number {
    if (grid.length === 0) {
        return 0;
    }

    const rows = grid.length;
    const columns = grid[0].length;
    let islands = 0;
    const directions = [[1, 0], [-1, 0], [0, 1], [0, -1]];

    function flood(row: number, column: number): void {
        if (
            row < 0 || row >= rows ||
            column < 0 || column >= columns ||
            grid[row][column] !== '1'
        ) {
            return;
        }

        grid[row][column] = '0';
        for (const [dr, dc] of directions) {
            flood(row + dr, column + dc);
        }
    }

    for (let row = 0; row < rows; row++) {
        for (let column = 0; column < columns; column++) {
            if (grid[row][column] === '1') {
                islands++;
                flood(row, column);
            }
        }
    }

    return islands;
}
```

## 复杂度分析

- 时间复杂度：O(rows × columns)。
- 空间复杂度：递归栈最坏 O(rows × columns)；若不能修改输入，可额外使用 `visited` 集合。

## 易错点 / 相关题

- 题目是否允许修改 `grid` 要先确认；不允许时不能用原地改 `0` 的写法。
- 相关题：腐烂的橘子、被围绕的区域、单词搜索。
