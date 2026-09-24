'use client';

import { memo } from 'react';
import Markdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

const components: Components = {
    a: ({ href, children }) => href ? (
        <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-4 [overflow-wrap:anywhere]">
            {children}
        </a>
    ) : <span>{children}</span>,
    // 表格和代码块在自身滚动，不能撑开对话列或破坏侧栏调宽。
    pre: ({ children }) => (
        <pre tabIndex={0} className="my-4 max-w-full overflow-x-auto rounded-xl border border-border bg-muted/50 p-4 text-[13px] leading-6 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            {children}
        </pre>
    ),
    table: ({ children }) => (
        <div role="region" aria-label="回复表格" tabIndex={0} className="my-4 max-w-full overflow-x-auto rounded-lg border border-border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <table className="w-full border-collapse text-left">{children}</table>
        </div>
    ),
    // 模型给出的远程图片不自动加载；保留显式打开入口和替代文字。
    img: ({ src, alt }) => typeof src === 'string' && src ? (
        <a href={src} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-4">
            {alt || '查看图片'}
        </a>
    ) : <span>{alt || '图片'}</span>,
};

const plugins = [remarkGfm];

// 历史与流式回复共用组件；保留原始 Markdown 数据，仅在展示层解析。
// 不启用原始 HTML，沿用库默认 URL 校验，禁止脚本链接和 HTML 执行。
export default memo(function MarkdownMessage({ content }: { content: string }) {
    return (
        <div className="min-w-0 max-w-full text-sm leading-6 [overflow-wrap:anywhere] [&>p]:my-3 [&>:first-child]:mt-0 [&>:last-child]:mb-0 [&_h1]:mb-3 [&_h1]:mt-6 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:mb-3 [&_h2]:mt-5 [&_h2]:text-base [&_h2]:font-semibold [&_h3]:mb-2 [&_h3]:mt-4 [&_h3]:font-semibold [&_h4]:font-semibold [&_h5]:font-semibold [&_h6]:font-semibold [&_ul]:my-3 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-3 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-1 [&_li>p]:my-2 [&_blockquote]:my-4 [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-4 [&_blockquote]:text-muted-foreground [&_hr]:my-6 [&_hr]:border-border [&_strong]:font-semibold [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:font-mono [&_code]:text-[0.9em] [&_pre_code]:rounded-none [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:whitespace-pre [&_th]:border-b [&_th]:border-border [&_th]:bg-muted/50 [&_th]:px-3 [&_th]:py-2 [&_td]:border-b [&_td]:border-border/50 [&_td]:px-3 [&_td]:py-2 [&_input]:mr-2">
            <Markdown remarkPlugins={plugins} components={components} skipHtml>
                {content}
            </Markdown>
        </div>
    );
});
