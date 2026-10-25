import ReactMarkdown from "react-markdown";

/** 瑞士风紧凑 Markdown 渲染：标题压平、mono 代码、信号红链接。
 * react-markdown 默认不执行 HTML（安全），文件内容/模型输出均安全渲染。 */
export default function Markdown({ text }: { text: string }) {
  return (
    <div className="text-[13.5px] leading-relaxed">
      <ReactMarkdown
        components={{
          p: ({ children }) => <p className="my-1 first:mt-0 last:mb-0">{children}</p>,
          ul: ({ children }) => (
            <ul className="my-1 list-disc pl-4 first:mt-0 last:mb-0">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="my-1 list-decimal pl-4 first:mt-0 last:mb-0">{children}</ol>
          ),
          li: ({ children }) => <li className="my-0.5">{children}</li>,
          h1: ({ children }) => (
            <h1 className="my-1.5 text-[13.5px] font-semibold first:mt-0">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="my-1.5 text-[13.5px] font-semibold first:mt-0">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="my-1 text-[13px] font-semibold first:mt-0">{children}</h3>
          ),
          strong: ({ children }) => (
            <strong className="font-semibold">{children}</strong>
          ),
          code: ({ children }) => (
            <code className="bg-ink/[0.06] px-1 font-mono text-[12px]">{children}</code>
          ),
          pre: ({ children }) => (
            <pre className="my-1.5 overflow-x-auto bg-ink/[0.04] p-2 font-mono text-[11px] leading-relaxed">
              {children}
            </pre>
          ),
          a: ({ children, href }) => (
            <a href={href} className="text-signal underline" target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          blockquote: ({ children }) => (
            <blockquote className="my-1 border-l-2 border-rule-strong pl-2 text-graphite">
              {children}
            </blockquote>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
