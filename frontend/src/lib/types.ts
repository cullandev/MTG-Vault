/** Response shapes mirroring `app/schemas`. Kept in one file so drift is obvious. */

export interface CollectionRow {
  group_key: string
  oracle_id: string
  card_id: number | null
  item_id: number | null
  name: string
  set_code: string | null
  set_name: string | null
  collector_number: string | null
  lang: string | null
  layout: string
  type_line: string | null
  mana_cost: string | null
  mana_value: number
  rarity: string | null
  color_identity: string
  image_url: string | null
  price_cents: number | null
  price_as_of: string | null
  copies: number
  value_cents: number
  finish: string | null
  condition: string | null
  is_proxy: boolean
}

export interface CollectionTotals {
  copies: number
  unique_cards: number
  value_cents: number
  unpriced_copies: number
}

export interface CollectionList {
  items: CollectionRow[]
  next_cursor: string | null
  totals: Partial<CollectionTotals>
  price_note: string
}

export interface OracleCard {
  oracle_id: string
  name: string
  name_front: string
  layout: string
  type_line: string | null
  oracle_text: string | null
  mana_cost: string | null
  mana_value: number
  color_identity: string
  keywords: string[]
  is_legendary: boolean
  is_land: boolean
  reserved: boolean
  game_changer: boolean
  edhrec_rank: number | null
  owned_count: number
  image_url: string | null
}

export interface Printing {
  card_id: number
  scryfall_id: string
  set_code: string
  set_name: string | null
  collector_number: string
  lang: string
  rarity: string | null
  layout: string
  released_at: string | null
  finishes: string[]
  digital: boolean
  image_url: string | null
  price_usd_cents: number | null
  price_usd_foil_cents: number | null
  price_usd_etched_cents: number | null
  price_as_of: string | null
  owned_count: number
}

export interface CardFace {
  face_index: number
  name: string
  mana_cost: string | null
  type_line: string | null
  oracle_text: string | null
  image_url: string | null
}

export interface OwnedCopy {
  item_id: number
  set_code: string
  collector_number: string
  lang: string
  finish: string
  condition: string
  is_proxy: boolean
  created_at: string
}

export interface CardDetail {
  oracle: OracleCard
  faces: CardFace[]
  printings: Printing[]
  legalities: Record<string, string>
  owned: OwnedCopy[]
  price_note: string
}

export interface CardSearchResult {
  items: OracleCard[]
  next_cursor: string | null
}


export interface CsvImportResult {
  dry_run: boolean
  flavour: string
  batch_id: string | null
  rows_seen: number
  matched: number
  added: number
  ambiguous: Array<Record<string, unknown>>
  unmatched: Array<Record<string, unknown>>
  errors: string[]
  preview: Array<Record<string, unknown>>
}

export interface AuditEntry {
  id: number
  ts: string
  action: string
  entity_type: string
  entity_id: string | null
  batch_id: string
  source: string
  note: string | null
  reverted_at: string | null
  summary: Record<string, unknown> | null
}

export interface SystemStatus {
  database: { path: string; bytes: number; wal_bytes: number }
  counts: { printings: number; oracle_cards: number; copies: number }
  image_cache: { bytes: number; cap_bytes: number }
  last_import: {
    kind: string
    status: string
    started_at: string
    finished_at: string | null
    rows_written: number
    source_updated_at: string | null
  } | null
  jobs: Array<{
    name: string
    sub_source: string | null
    status: string
    started_at: string
    finished_at: string | null
  }>
  features: { ai: boolean; edhrec: boolean; spellbook: boolean; meta_sources: string[] }
}

export interface CollectionValue {
  total_cents: number
  foil_cents: number
  nonproxy_count: number
  unique_count: number
  unpriced_count: number
  by_set: Array<{ set_code: string; set_name: string | null; copies: number; value_cents: number }>
  by_rarity: Array<{ rarity: string; copies: number; value_cents: number }>
  top_cards: Array<{
    card_id: number
    name: string
    set_code: string
    collector_number: string
    finish: string
    value_cents: number
  }>
}

export interface ValuePoint {
  date: string
  total_cents: number
  foil_cents: number
  copies: number
  unpriced: number
}

export interface Mover {
  card_id: number
  name: string
  set_code: string
  collector_number: string
  pct_change: number
  from_cents: number
  to_cents: number
  /** Which earlier snapshot the move was measured against -- not always yesterday. */
  compared_to_date: string
  snapshot_date: string
}

export interface Dashboard {
  value: CollectionValue
  value_history: ValuePoint[]
  change: { since: string; from_cents: number; to_cents: number; delta_cents: number } | null
  movers: Mover[]
  recent_additions: Array<{
    item_id: number
    card_id: number
    name: string
    set_code: string
    collector_number: string
    finish: string
    added_at: string
  }>
  unread_notifications: number
  move_threshold_pct: number
}

export interface AppNotification {
  id: number
  kind: string
  title: string
  body: string | null
  link: string | null
  created_at: string
  read_at: string | null
}

// --- decks (Phase 4) -------------------------------------------------------

export interface Deck {
  id: number
  name: string
  format: string
  is_built: boolean
  colors: string
  commander_oracle_id: string | null
  partner_oracle_id: string | null
  companion_oracle_id: string | null
  commander_name: string | null
  source: string
  goal_text: string | null
  summary: DeckSummary | null
  archived: boolean
  card_count: number
  allocated_count: number
  is_legal: boolean | null
  created_at: string
  updated_at: string
}

/** The generator's mechanics-and-why summary, attached to machine-built decks. */
export interface DeckSummary {
  provenance: 'synergy' | 'meta'
  headline: string
  game_plan: string
  mechanics: Array<{ tag: string; label: string; count: number; examples: string[] }>
  key_cards: Array<{ name: string; why: string }>
  why_picked: string[]
}

export interface DeckCardRow {
  oracle_id: string
  name: string
  board: 'main' | 'side' | 'commander' | 'companion' | 'maybe'
  quantity: number
  category: string | null
  type_line: string | null
  mana_cost: string | null
  cmc: number
  color_identity: string
  is_proxy_intent: boolean
  preferred_set_code: string | null
  preferred_collector_number: string | null
  card_id: number | null
  image_normal_url: string | null
  price_cents: number | null
  owned: number
  free: number
  allocated_here: number
}

export interface DeckCards {
  boards: Record<string, DeckCardRow[]>
  price_note: string
}

export interface RuleIssue {
  code: string
  message: string
  oracle_ids: string[]
}

export interface DeckValidation {
  is_legal: boolean
  errors: RuleIssue[]
  warnings: RuleIssue[]
}

export interface DeckStats {
  card_count: number
  curve: Record<string, number>
  x_spells: number
  pips: Record<string, number>
  types: Record<string, number>
  avg_mv: number
  lands: number
  mdfc_lands: number
  recommended_lands: number
}

export interface BuildOutcome {
  allocated: number
  conflicts: Array<{
    oracle_id: string
    name: string
    needed: number
    available: number
    blocking_decks: string[]
  }>
  batch_id: string | null
  assumed_basics: number
}

export interface MissingList {
  rows: Array<{
    oracle_id: string
    name: string
    needed: number
    owned_free: number
    missing: number
    cheapest_cents: number | null
    subtotal_cents: number | null
  }>
  total_cents: number
  price_note: string
}

export interface GoldfishOutcome {
  hands: number
  turns: number
  kept_hand_sizes: Record<string, number>
  lands_in_kept_hands: Record<string, number>
  land_drop_rate: number[]
}

// --- rating (Phase 5) ------------------------------------------------------

export interface DeckScores {
  consistency: number
  speed: number
  interaction: number
  resilience: number
  signals: Record<string, unknown>
  heuristic_version: number
  computed_at?: string
  bracket?: BracketVerdict | null
}

export interface BracketVerdict {
  bracket: number
  signals: {
    game_changers: string[]
    extra_turns: string[]
    mass_land_denial: string[]
    two_card_combos: string[]
    tutors: string[]
  }
  rationale: string[]
}

export interface ComboInfo {
  combo_id: string
  cards: string[]
  result: string
  colors: string
  missing?: string[]
  owned?: string[]
}

export interface DeckCombos {
  present: ComboInfo[]
  completable_from_vault: ComboInfo[]
  stale: boolean
}

export interface EdhrecCardRec {
  name: string
  inclusion_pct: number
  synergy: number
  status?: 'in_deck' | 'available' | 'owned_allocated' | 'missing'
}

export interface EdhrecPanelData {
  available: boolean
  reason?: string
  commander?: string
  stale?: boolean
  fetched_at?: string
  themes?: string[]
  lists?: Array<{ header: string; cards: EdhrecCardRec[] }>
}

export interface AiReview {
  archetype: string
  strengths: string[]
  weaknesses: string[]
  swaps: Array<{ out: string; in: string; why: string; owned?: boolean }>
  estimated_bracket: number
  source: string
  model?: string
  generated_at?: string
}

// --- meta / build-for-me (Phase 7) -----------------------------------------

export interface MetaArchetypeRow {
  archetype_key: string
  name: string
  meta_share_pct: number
  placement_count: number
  colors: string | null
}

export interface MetaListing {
  snapshot: {
    id: number
    source: string
    measurement: string
    snapshot_date: string
    is_stale: boolean
  } | null
  archetypes: MetaArchetypeRow[]
}

export interface BuildProposal {
  archetype_key: string
  archetype: string
  format: string
  colors: string | null
  meta_share_pct: number
  commander_owned: boolean
  measurement: string
  snapshot_date: string
  is_stale: boolean
  coverage_pct: number
  core_coverage_pct: number
  missing_count: number
  cost_to_complete_cents: number
  conflicts: number
  rank_score: number
  missing: Array<{ oracle_id: string; name: string; tier: string; cheapest_cents: number | null }>
}

export interface GeneratedDeck {
  deck: Array<{
    oracle_id: string
    name: string
    quantity: number
    board: string
    tier: string | null
    reason: string
  }>
  substitutions: Array<{ out: string; in: string; reason: string; score: number }>
  buy_list: Array<{ oracle_id: string; name: string; quantity: number; cheapest_cents: number | null }>
  is_legal: boolean
  score?: Record<string, unknown>
  summary?: DeckSummary
  deck_id?: number
}

export interface ArchetypeTemplateView {
  archetype_key: string
  format: string
  list_count: number
  computed_at: string
  tiers: Record<'CORE' | 'COMMON' | 'FLEX', Array<{
    oracle_id: string
    name: string
    presence_pct: number
    typical_count: number
  }>>
}

export interface MatchupResult {
  decks: Array<{
    ref: string
    name: string
    speed: number
    interaction: number
    interaction_density: number
    bracket: number
    wincon_kinds: string[]
    hate_pieces: string[]
  }>
  pairwise: Array<{ a: string; b: string; favoured: string | null; margin: number; reasons: string[] }>
  pod_notes: string[]
  bracket_mismatch: boolean
}

// --- battles (Forge sidecar) -----------------------------------------------

export interface PriceAlert {
  id: number
  scope: 'owned' | 'card'
  card_id: number | null
  direction: 'above' | 'below' | 'pct_up' | 'pct_down'
  threshold_cents: number | null
  threshold_pct: number | null
  cooldown_days: number
  active: boolean
  last_fired_at: string | null
  created_at: string
}

export interface GauntletCandidate {
  deck_id: number
  name: string
  theme: string
  structure: 'commander' | 'sixty'
  colors: string
  wins: number
  games: number
  win_rate: number | null
  delta?: number
  /** Set on experiment runs: the champion build vs its handicapped challenger. */
  role?: 'champion' | 'challenger'
}

export interface GauntletRun {
  id: number
  started_at: string
  finished_at: string | null
  status: 'running' | 'ok' | 'failed'
  vault_distinct: number
  games_played: number
  candidates: GauntletCandidate[]
  opponents: Array<{ deck_id: number; name: string; archetype: string; meta_share_pct: number }>
  /** Present only while status is "running": what is at the table right now. */
  live?: {
    playing: { candidate: string; opponent: string } | null
    pairings_done: number
    pairings_total: number
    candidates: Array<{
      deck_id: number
      theme: string
      wins: number
      games: number
      role?: 'champion' | 'challenger'
    }>
  } | null
  error: string | null
}

export interface Battle {
  id: number
  ran_at: string
  engine: string
  engine_version: string | null
  format: string
  games_requested: number
  games_completed: number
  status: 'running' | 'ok' | 'failed'
  duration_ms: number | null
  decks: Array<{ deck_id: number; name: string; wins: number }>
  draws: number
  unknown_cards: string[]
  error: string | null
}

// --- synergy / hidden decks (Phase 8) --------------------------------------

export interface SynergyCoreSummary {
  core_id: number
  theme: string
  colors: string
  card_count: number
  density: number
  buildability: number
  combined_score: number
  computed_at: string
  suggested_commanders: Array<{
    oracle_id: string
    name: string
    owned: boolean
    score: number
    reasons: string[]
  }>
}

export interface SynergyCoreDetail {
  core_id: number
  theme: string
  colors: string
  cards: Array<{ oracle_id: string; name: string; centrality: number }>
  edges: Array<{ a: string; b: string; weight: number; reasons: string[] }>
}

export interface AssembledDeck {
  deck: Array<{ oracle_id: string; name: string; quantity: number; board: string; reason: string }>
  synergy_map: Record<string, string[]>
  quota_report: Array<{ name: string; target: number; have: number }>
  is_legal: boolean
  summary?: DeckSummary
  deck_id?: number
}
