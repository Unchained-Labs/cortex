/**
 * Template file text, recomposed.
 *
 * `GET /api/templates` returns a template already parsed — `title` and
 * `target` lifted out of the frontmatter and `body` being everything after
 * it. `PUT /api/templates` takes the whole file back. So saving has to put
 * the frontmatter back on, or the first save of any template would quietly
 * drop the line that says where its notes land.
 */
export function composeTemplate(title: string, target: string, body: string): string {
  const lines: string[] = [];
  if (title.trim()) lines.push(`name: ${title.trim()}`);
  if (target.trim()) lines.push(`target: ${target.trim()}`);
  if (lines.length === 0) return body;
  return `---\n${lines.join("\n")}\n---\n${body}`;
}

/** What a new template starts as — enough to run without being edited. */
export const NEW_TEMPLATE_BODY = `# {{title}}

- Date: {{date}}
- By: {{user}}

## Notes

`;
