# 搜索旋转排序数组（LeetCode 33）

> 主题：二分查找 | 难度：Medium | 频率：高

## 考点（面试官在考察什么）

- 旋转后仍有一半区间保持有序，能否在每轮二分中正确判断目标所在侧。

## 核心答案（能直接讲出口的版本）

取 `mid` 后，`left..mid` 或 `mid..right` 至少有一段有序。先判断有序段，再检查目标是否落在该段的闭区间内；否则舍弃这一段。

```ts
function searchRotated(nums: number[], target: number): number {
    let left = 0;
    let right = nums.length - 1;

    while (left <= right) {
        const mid = left + Math.floor((right - left) / 2);
        if (nums[mid] === target) {
            return mid;
        }

        if (nums[left] <= nums[mid]) {
            if (nums[left] <= target && target < nums[mid]) {
                right = mid - 1;
            }
            else {
                left = mid + 1;
            }
        }
        else if (nums[mid] < target && target <= nums[right]) {
            left = mid + 1;
        }
        else {
            right = mid - 1;
        }
    }

    return -1;
}
```

## 复杂度分析

- 时间复杂度：O(log n)。
- 空间复杂度：O(1)。

## 易错点 / 相关题

- 区间边界必须和条件保持一致，这里使用 `[left, right]` 闭区间。
- 本题假设无重复值；有重复值时需要在 `nums[left] === nums[mid] === nums[right]` 时收缩边界。
