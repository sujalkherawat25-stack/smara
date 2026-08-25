import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github-dark.css";

interface Props {
  children: string;
}

/**
 * Markdown renderer tuned for chat output.
 * - GFM (tables, strikethrough, task lists)
 * - Syntax highlighting via highlight.js
 * - All colors come from CSS theme vars so light + dark both look correct.
 *   Previously this file hardcoded `text-gray-100` / `text-white` / `bg-white/10`
 *   which became invisible on the parchment light theme.
 */
// Strip model-emitted citation markers before rendering.
// Patterns removed:
//   【N†label】  — OpenAI / Claude Sonnet footnote style
//   [N†label]   — bracket variant
//   【N】        — bare number variant
function cleanText(s: string): string {
  return s
    .replace(/【\d+†[^\】]*】/g, "")   // 【1†L1-L3】
    .replace(/\[\d+†[^\]]*\]/g, "")    // [1†source]
    .replace(/【\d+】/g, "");           // 【1】
}

export default function Markdown({ children }: Props) {
  return (
    <div className="prose-chat">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          p: ({ children }) => (
            <p
              className="my-2 leading-7 text-[15px]"
              style={{ color: "var(--text-primary)" }}
            >
              {children}
            </p>
          ),
          h1: ({ children }) => (
            <h1
              className="text-xl font-bold mt-5 mb-2"
              style={{ color: "var(--text-primary)" }}
            >
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2
              className="text-lg font-bold mt-4 mb-2"
              style={{ color: "var(--text-primary)" }}
            >
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3
              className="text-base font-semibold mt-3 mb-1.5"
              style={{ color: "var(--text-primary)" }}
            >
              {children}
            </h3>
          ),
          ul: ({ children }) => (
            <ul className="list-disc pl-6 my-2 space-y-1">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="list-decimal pl-6 my-2 space-y-1">{children}</ol>
          ),
          li: ({ children }) => (
            <li
              className="leading-7 text-[15px]"
              style={{ color: "var(--text-primary)" }}
            >
              {children}
            </li>
          ),
          strong: ({ children }) => (
            <strong
              className="font-semibold"
              style={{ color: "var(--text-primary)" }}
            >
              {children}
            </strong>
          ),
          em: ({ children }) => (
            <em className="italic" style={{ color: "var(--text-secondary)" }}>
              {children}
            </em>
          ),
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="underline underline-offset-2 transition-colors"
              style={{ color: "var(--accent)" }}
            >
              {children}
            </a>
          ),
          blockquote: ({ children }) => (
            <blockquote
              className="pl-3 my-3 italic"
              style={{
                borderLeft: "2px solid var(--border-default)",
                color: "var(--text-secondary)",
              }}
            >
              {children}
            </blockquote>
          ),
          hr: () => (
            <hr
              className="my-4"
              style={{ borderColor: "var(--border-dim)" }}
            />
          ),
          code: ({ className, children, ...rest }) => {
            const isBlock = (className || "").includes("language-");
            if (isBlock) {
              return (
                <code className={className} {...rest}>
                  {children}
                </code>
              );
            }
            // Inline code — sit on a soft tinted background so it pops without
            // being loud. Accent color reads as "highlighted but not a link".
            return (
              <code
                className="px-1.5 py-0.5 rounded text-[13.5px] font-mono"
                style={{
                  background: "var(--accent-soft)",
                  color: "var(--accent)",
                  border: "1px solid var(--border-dim)",
                }}
              >
                {children}
              </code>
            );
          },
          pre: ({ children }) => (
            // Code blocks keep the github-dark theme for syntax colors — that
            // looks fine on both light and dark UI because the block has its
            // own dark surface. Border + bg from theme so it docks visually.
            <pre
              className="my-3 rounded-lg p-3 overflow-x-auto text-[13px] leading-6 scrollbar-thin"
              style={{
                background: "#0d1117",
                border: "1px solid var(--border-default)",
              }}
            >
              {children}
            </pre>
          ),
          table: ({ children }) => (
            <div className="data-table-wrap my-3 overflow-x-auto">
              <table
                className="data-table min-w-full text-sm"
                style={{
                  border: "1px solid var(--border-default)",
                  color: "var(--text-primary)",
                }}
              >
                {children}
              </table>
            </div>
          ),
          th: ({ children }) => (
            <th
              className="data-table-head px-3 py-1.5 text-left font-semibold"
              style={{
                background: "var(--bg-elevated)",
                border: "1px solid var(--border-default)",
                color: "var(--text-primary)",
              }}
            >
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td
              className="data-table-cell px-3 py-1.5"
              style={{
                border: "1px solid var(--border-dim)",
                color: "var(--text-primary)",
              }}
            >
              {children}
            </td>
          ),
        }}
      >
        {cleanText(children)}
      </ReactMarkdown>
    </div>
  );
}
