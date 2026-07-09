import { useMemo, type ReactNode } from 'react';
import { marked, type Token, type Tokens } from 'marked';

interface MarkdownContentProps {
  children: string;
  className?: string;
}

export function MarkdownContent({ children, className }: MarkdownContentProps) {
  const tokens = useMemo(
    () => marked.lexer(children || '', { gfm: true, breaks: true }),
    [children],
  );
  const classes = ['markdown-content', className].filter(Boolean).join(' ');

  return (
    <div className={classes}>
      {tokens.map((token, index) => renderBlock(token, `md-${index}`))}
    </div>
  );
}

function renderBlock(token: Token, key: string): ReactNode {
  switch (token.type) {
    case 'space':
      return null;
    case 'heading': {
      const heading = token as Tokens.Heading;
      const content = renderInlineTokens(heading.tokens, heading.text, key);
      if (heading.depth === 1) return <h1 key={key}>{content}</h1>;
      if (heading.depth === 2) return <h2 key={key}>{content}</h2>;
      if (heading.depth === 3) return <h3 key={key}>{content}</h3>;
      return <h4 key={key}>{content}</h4>;
    }
    case 'paragraph': {
      const paragraph = token as Tokens.Paragraph;
      return <p key={key}>{renderInlineTokens(paragraph.tokens, paragraph.text, key)}</p>;
    }
    case 'text': {
      const text = token as Tokens.Text;
      return <p key={key}>{renderInlineTokens(text.tokens, text.text, key)}</p>;
    }
    case 'list': {
      const list = token as Tokens.List;
      const Tag = list.ordered ? 'ol' : 'ul';
      const start = typeof list.start === 'number' ? list.start : undefined;
      return (
        <Tag key={key} start={list.ordered ? start : undefined}>
          {list.items.map((item, index) => renderListItem(item, `${key}-li-${index}`))}
        </Tag>
      );
    }
    case 'blockquote': {
      const quote = token as Tokens.Blockquote;
      return (
        <blockquote key={key}>
          {quote.tokens.map((child, index) => renderBlock(child, `${key}-quote-${index}`))}
        </blockquote>
      );
    }
    case 'code': {
      const code = token as Tokens.Code;
      return (
        <pre key={key}>
          <code>{code.text}</code>
        </pre>
      );
    }
    case 'table':
      return renderTable(token as Tokens.Table, key);
    case 'hr':
      return <hr key={key} />;
    case 'html':
      return <p key={key}>{token.raw}</p>;
    default:
      return token.raw ? <p key={key}>{token.raw}</p> : null;
  }
}

function renderListItem(item: Tokens.ListItem, key: string): ReactNode {
  return (
    <li key={key}>
      {item.tokens.map((token, index) => renderListItemBlock(token, `${key}-${index}`))}
    </li>
  );
}

function renderListItemBlock(token: Token, key: string): ReactNode {
  if (token.type === 'text') {
    const text = token as Tokens.Text;
    return <span key={key}>{renderInlineTokens(text.tokens, text.text, key)}</span>;
  }
  return renderBlock(token, key);
}

function renderTable(table: Tokens.Table, key: string): ReactNode {
  return (
    <div key={key} className="markdown-table-wrap">
      <table>
        <thead>
          <tr>
            {table.header.map((cell, index) => (
              <th key={`${key}-h-${index}`}>{renderInlineTokens(cell.tokens, cell.text, `${key}-h-${index}`)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, rowIndex) => (
            <tr key={`${key}-r-${rowIndex}`}>
              {row.map((cell, cellIndex) => (
                <td key={`${key}-r-${rowIndex}-${cellIndex}`}>
                  {renderInlineTokens(cell.tokens, cell.text, `${key}-r-${rowIndex}-${cellIndex}`)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderInlineTokens(tokens: Token[] | undefined, fallback: string | undefined, key: string): ReactNode[] {
  if (!tokens || tokens.length === 0) {
    return renderTextWithBreaks(fallback || '', key);
  }
  return tokens.flatMap((token, index) => renderInline(token, `${key}-in-${index}`));
}

function renderInline(token: Token, key: string): ReactNode[] {
  switch (token.type) {
    case 'text': {
      const text = token as Tokens.Text;
      if (text.tokens?.length) return renderInlineTokens(text.tokens, text.text, key);
      return renderTextWithBreaks(text.text, key);
    }
    case 'strong': {
      const strong = token as Tokens.Strong;
      return [<strong key={key}>{renderInlineTokens(strong.tokens, strong.text, key)}</strong>];
    }
    case 'em': {
      const em = token as Tokens.Em;
      return [<em key={key}>{renderInlineTokens(em.tokens, em.text, key)}</em>];
    }
    case 'codespan': {
      const code = token as Tokens.Codespan;
      return [<code key={key}>{code.text}</code>];
    }
    case 'br':
      return [<br key={key} />];
    case 'del': {
      const deleted = token as Tokens.Del;
      return [<del key={key}>{renderInlineTokens(deleted.tokens, deleted.text, key)}</del>];
    }
    case 'link': {
      const link = token as Tokens.Link;
      const href = safeHref(link.href);
      const content = renderInlineTokens(link.tokens, link.text, key);
      if (!href) return [<span key={key}>{content}</span>];
      return [
        <a key={key} href={href} target="_blank" rel="noreferrer">
          {content}
        </a>,
      ];
    }
    case 'image': {
      const image = token as Tokens.Image;
      const href = safeHref(image.href);
      const label = image.text || image.href;
      if (!href) return renderTextWithBreaks(label, key);
      return [
        <a key={key} href={href} target="_blank" rel="noreferrer">
          {label}
        </a>,
      ];
    }
    case 'html':
      return renderTextWithBreaks(token.raw, key);
    default:
      return renderTextWithBreaks(token.raw || '', key);
  }
}

function renderTextWithBreaks(text: string, key: string): ReactNode[] {
  const parts = text.split('\n');
  return parts.flatMap((part, index) => {
    if (index === 0) return [part];
    return [<br key={`${key}-br-${index}`} />, part];
  });
}

function safeHref(href: string): string | null {
  const trimmed = href.trim();
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  if (/^mailto:/i.test(trimmed)) return trimmed;
  return null;
}
