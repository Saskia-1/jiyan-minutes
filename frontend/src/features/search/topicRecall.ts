import type { LocalApiClient } from "../../api/client";
import type { ChatSearchRequest, ChatSearchResult } from "../../types";

const MAX_RECALLED_MESSAGES = 2_000;

export async function recallTopicMessages(
  api: Pick<LocalApiClient, "searchChats">,
  request: ChatSearchRequest,
  terms: string[],
): Promise<ChatSearchResult[]> {
  if (!terms.length || terms.length > 10) throw new Error("invalid_topic_terms");
  const byId = new Map<string, ChatSearchResult>();
  for (const term of new Set(terms)) {
    let cursor: string | null = null;
    const seenCursors = new Set<string>();
    do {
      const page = await api.searchChats({ ...request, query: term, limit: 100, cursor });
      for (const item of page.results) byId.set(item.message_id, item);
      if (byId.size > MAX_RECALLED_MESSAGES) throw new Error("topic_recall_limit");
      cursor = page.next_cursor;
      if (cursor && seenCursors.has(cursor)) throw new Error("topic_search_cursor_loop");
      if (cursor) seenCursors.add(cursor);
    } while (cursor);
  }
  return [...byId.values()].sort((left, right) => request.include_undated
    ? (left.source_order ?? Number.MAX_SAFE_INTEGER) - (right.source_order ?? Number.MAX_SAFE_INTEGER) || left.message_id.localeCompare(right.message_id)
    : Date.parse(left.timestamp || "") - Date.parse(right.timestamp || "") || left.message_id.localeCompare(right.message_id));
}
