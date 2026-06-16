/** Quote-link helpers for message selection in a thread. */

export function normalizeText(text) {
  return (text || "").replace(/\s+/g, " ").trim();
}

/** Find index of the message being quoted, if it appears earlier in the thread. */
export function findQuotedIndex(messages, quote, beforeIndex) {
  if (!quote?.text) return null;
  const qText = normalizeText(quote.text);
  if (!qText) return null;

  for (let i = 0; i < beforeIndex; i++) {
    const m = messages[i];
    const content = normalizeText(m.content);
    if (!content) continue;
    if (quote.sender && m.sender !== quote.sender) continue;
    if (content === qText || content.includes(qText) || qText.includes(content)) {
      return i;
    }
  }
  return null;
}

/**
 * @param {Array<{quote?: {sender?: string, text?: string}}>} messages
 * @returns {{ quotesTarget: Map<number, number>, quotedBy: Map<number, number[]> }}
 */
export function buildQuoteGraph(messages) {
  const quotesTarget = new Map();
  const quotedBy = new Map();

  messages.forEach((msg, index) => {
    if (!msg.quote) return;
    const target = findQuotedIndex(messages, msg.quote, index);
    if (target == null) return;
    quotesTarget.set(index, target);
    if (!quotedBy.has(target)) quotedBy.set(target, []);
    quotedBy.get(target).push(index);
  });

  return { quotesTarget, quotedBy };
}

/** All indices linked by quote chains (ancestors and descendants). */
export function quoteClosureForIndex(index, quotesTarget, quotedBy) {
  const result = new Set([index]);
  const queue = [index];

  while (queue.length) {
    const i = queue.shift();
    const parent = quotesTarget.get(i);
    if (parent != null && !result.has(parent)) {
      result.add(parent);
      queue.push(parent);
    }
    for (const child of quotedBy.get(i) || []) {
      if (!result.has(child)) {
        result.add(child);
        queue.push(child);
      }
    }
  }

  return result;
}

export function expandSelection(indices, quotesTarget, quotedBy) {
  const result = new Set();
  for (const index of indices) {
    for (const linked of quoteClosureForIndex(index, quotesTarget, quotedBy)) {
      result.add(linked);
    }
  }
  return result;
}

/** Empty graph safe for threads with no messages. */
export const EMPTY_QUOTE_GRAPH = {
  quotesTarget: new Map(),
  quotedBy: new Map(),
};
