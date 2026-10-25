import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** 模型回复统一渲染为 Markdown；不执行回复中的原始 HTML。 */
export default function Markdown({ text, compact = false }: { text: string; compact?: boolean }) {
  return (
    <div className={`reply-markdown${compact ? " reply-markdown--compact" : ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          table: ({ children }) => (
            <div className="reply-table-scroll" role="region" aria-label="回复中的表格" tabIndex={0}>
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
