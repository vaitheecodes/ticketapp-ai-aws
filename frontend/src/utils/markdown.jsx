/* Minimal markdown renderer for AI chat replies.
 *
 * generate_response (backend/graph.py) writes paragraphs, numbered steps,
 * bullets, bold labels and the occasional inline code span — and nothing
 * else, because we own the prompt. Rendering that exact subset is ~80 lines
 * and keeps the dependency list at react + react-router, instead of pulling
 * a full remark/unified tree in to format five constructs.
 *
 * Everything is built as React elements, never dangerouslySetInnerHTML, so
 * model output cannot inject markup by construction.
 */

const ORDERED_RE = /^\s*(\d+)[.)]\s+(.*)$/;
const BULLET_RE = /^\s*[-*•]\s+(.*)$/;
const HEADING_RE = /^\s*#{1,6}\s+(.*)$/;

// Split on **bold**, `code` and __bold__ while keeping the delimiters.
const INLINE_RE = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`)/g;

function renderInline(text, keyPrefix) {
  return text.split(INLINE_RE).map((part, i) => {
    const key = `${keyPrefix}-${i}`;
    if (!part) return null;
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("__") && part.endsWith("__")) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={key}>{part.slice(1, -1)}</code>;
    }
    return part;
  });
}

/* Group consecutive lines into blocks: list runs stay together so they
 * render as one <ol>/<ul>, everything else accumulates into a paragraph
 * until a blank line closes it. */
function toBlocks(source) {
  const blocks = [];
  let paragraph = [];
  let list = null;

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push({ type: "p", lines: paragraph });
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list) {
      blocks.push(list);
      list = null;
    }
  };

  for (const raw of source.split("\n")) {
    const line = raw.trimEnd();
    const ordered = line.match(ORDERED_RE);
    const bullet = line.match(BULLET_RE);
    const heading = line.match(HEADING_RE);

    if (!line.trim()) {
      flushParagraph();
      flushList();
    } else if (heading) {
      flushParagraph();
      flushList();
      blocks.push({ type: "h", text: heading[1] });
    } else if (ordered) {
      flushParagraph();
      if (list?.type !== "ol") {
        flushList();
        list = { type: "ol", items: [], start: Number(ordered[1]) };
      }
      list.items.push(ordered[2]);
    } else if (bullet) {
      flushParagraph();
      if (list?.type !== "ul") {
        flushList();
        list = { type: "ul", items: [] };
      }
      list.items.push(bullet[1]);
    } else if (list) {
      // a wrapped continuation line of the current list item
      list.items[list.items.length - 1] += ` ${line.trim()}`;
    } else {
      paragraph.push(line);
    }
  }
  flushParagraph();
  flushList();
  return blocks;
}

/* `breaks`: keep single newlines inside a paragraph as line breaks. Email
 * replies (the ticket thread) put the signature and short address lines
 * on their own lines on purpose; chat replies wrap freely. */
export function Markdown({ children, breaks = false }) {
  const source = typeof children === "string" ? children : "";
  if (!source.trim()) return null;

  return (
    <div className="md">
      {toBlocks(source).map((block, i) => {
        if (block.type === "h") {
          return (
            <p className="md-heading" key={i}>
              {renderInline(block.text, `h${i}`)}
            </p>
          );
        }
        if (block.type === "ol" || block.type === "ul") {
          const List = block.type;
          return (
            <List className="md-list" key={i} start={block.start}>
              {block.items.map((item, j) => (
                <li key={j}>{renderInline(item, `l${i}-${j}`)}</li>
              ))}
            </List>
          );
        }
        if (breaks) {
          return (
            <p key={i}>
              {block.lines.map((line, j) => (
                <span key={j}>
                  {j > 0 && <br />}
                  {renderInline(line, `p${i}-${j}`)}
                </span>
              ))}
            </p>
          );
        }
        return <p key={i}>{renderInline(block.lines.join(" "), `p${i}`)}</p>;
      })}
    </div>
  );
}
