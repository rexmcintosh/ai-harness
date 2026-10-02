// Back off one unit when the cut would land between a surrogate pair, which would
// otherwise emit two lone halves and render as replacement characters.
function safeCut(s: string, limit: number): number {
  const code = s.charCodeAt(limit - 1)
  const splitsPair = code >= 0xd800 && code <= 0xdbff
  return splitsPair && limit > 1 ? limit - 1 : limit
}

export function chunkText(text: string, limit = 4096): string[] {
  if (text.length === 0) return []
  if (text.length <= limit) return [text]
  const chunks: string[] = []
  let current = ''
  for (const para of text.split('\n\n')) {
    const candidate = current === '' ? para : `${current}\n\n${para}`
    if (candidate.length <= limit) {
      current = candidate
      continue
    }
    if (current !== '') chunks.push(current)
    let rest = para
    while (rest.length > limit) {
      const cut = safeCut(rest, limit)
      chunks.push(rest.slice(0, cut))
      rest = rest.slice(cut)
    }
    current = rest
  }
  if (current !== '') chunks.push(current)
  return chunks
}
