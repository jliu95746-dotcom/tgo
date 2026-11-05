const PREVIEW_LENGTH = 160;

/** Keep resource cards compact without changing the searchable source. */
export function knowledgeBasePreview(content: string): string {
  let body = content;
  const metadata = body.match(/^\uFEFF?---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/);
  if (metadata && /^[ \t]*[A-Za-z_][\w-]*[ \t]*:/m.test(metadata[1] ?? '')) {
    body = body.slice(metadata[0].length);
  }
  const text = body.replace(/^[ \t]{0,3}(?:#{1,6}[ \t]+|>[ \t]?)/gm, '')
    .replace(/\s+/g, ' ').trim();
  const characters = Array.from(text);
  return characters.length > PREVIEW_LENGTH ? `${characters.slice(0, PREVIEW_LENGTH).join('')}…` : text;
}
