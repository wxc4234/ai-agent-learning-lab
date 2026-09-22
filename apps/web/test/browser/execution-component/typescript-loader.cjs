// 仅隔离浏览器夹具使用；生产构建仍交给Next.js。
// eslint-disable-next-line @typescript-eslint/no-require-imports -- Webpack同步CommonJS loader入口。
const ts = require("typescript");
module.exports = function transform(source) {
    return ts.transpileModule(source, {
        fileName: this.resourcePath,
        compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
};
