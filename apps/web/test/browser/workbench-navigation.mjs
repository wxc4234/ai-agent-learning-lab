// 历史内部诊断专项的导航帮助器；当前产品已撤下该入口，不用于当前用户流程验收。
// 集中维护高级入口，业务专项不用依赖旧的常驻详情按钮。
export async function openAdvancedDetails(page, { diagnostics = true } = {}) {
    if (!await page.getByRole('complementary', { name: '高级详情', exact: true }).isVisible()) {
        await page.getByLabel('项目更多操作').click();
        await page.getByRole('button', { name: '高级详情', exact: true }).click();
    }
    if (diagnostics) {
        const summary = page.getByText('运行记录与诊断', { exact: true });
        if (!await summary.evaluate(element => element.parentElement.open)) await summary.click();
    }
}
