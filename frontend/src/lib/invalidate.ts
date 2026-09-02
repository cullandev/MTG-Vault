import type { QueryClient } from '@tanstack/react-query'

/**
 * Everything that is a view OF the collection.
 *
 * Adding or removing a card changes far more than the library list: the
 * dashboard's totals, the Sets page's completion and value charts, the buy
 * list's needs, and the audit trail all derive from the same rows. Each
 * caller used to invalidate the two or three keys it happened to think of,
 * so a scanning session left Home and Sets showing pre-scan numbers for
 * minutes (both cache for longer than the library does). One list, one
 * import, no more guessing.
 */
const COLLECTION_VIEWS = [
  ['collection'],
  ['dashboard'],
  ['sets'],
  ['value-history'],
  ['buylist'],
  ['audit'],
  ['decks'],
] as const

/** Invalidate every view derived from the collection. */
export function invalidateCollection(queryClient: QueryClient, oracleId?: string): void {
  for (const key of COLLECTION_VIEWS) {
    void queryClient.invalidateQueries({ queryKey: key })
  }
  // The card page shows its own owned-copies list; refresh just that card
  // when we know which one changed, or all of them when we do not.
  void queryClient.invalidateQueries({ queryKey: oracleId ? ['card', oracleId] : ['card'] })
}
