/* Data access.
 *
 * The console reads a corpus of a couple of hundred rows out of a local DuckDB
 * file. Everything except campaign outcomes is immutable for the lifetime of a
 * session, so the caching policy is simply "fetch once" — React Query holds the
 * payloads, and the two mutating endpoints invalidate the keys they touch
 * rather than the whole cache.
 *
 * The hooks below are the only place a URL is written. A view asks for
 * `useProfessor(siape)`, not for a string, so a payload that moves cannot leave
 * a stale path buried three files deep.
 */

import {
  useMutation, useQuery, useQueryClient, type UseQueryResult,
} from '@tanstack/react-query';
import type {
  AppState, CampaignDetail, EnginePayload, LandscapePayload, NetworkPayload,
  OpportunitiesPayload, OutcomeResult, PipelinePayload, PlanDetail, ProfessorDetail,
  ProfessorsPayload, ThreadState,
} from './types';

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: 'application/json' } });
  const payload = await response.json().catch(() => ({ error: 'malformed response' }));
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload as T;
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({ error: 'malformed response' }));
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload as T;
}

/** Corpus payloads never change while the page is open; outcomes do. */
const STATIC_QUERY = { staleTime: Infinity, gcTime: Infinity } as const;

/* Freshness is the one payload whose point is to have changed since the page
 * opened: somebody runs `pulsar pipeline run` in another window and the numbers
 * on every other screen quietly become the previous answer. So it is not
 * STATIC_QUERY — it re-fetches when the tab is looked at again. */
export function usePipeline() {
  return useQuery({
    queryKey: ['pipeline'],
    queryFn: () => fetchJson<PipelinePayload>('/api/pipeline'),
    staleTime: 30_000,
  });
}

export function useAppState(): UseQueryResult<AppState> {
  return useQuery({ queryKey: ['state'], queryFn: () => fetchJson<AppState>('/api/state') });
}

export function useOpportunities() {
  return useQuery({
    queryKey: ['opportunities'],
    queryFn: () => fetchJson<OpportunitiesPayload>('/api/opportunities'),
    ...STATIC_QUERY,
  });
}

export function usePlan(id: string | null) {
  return useQuery({
    queryKey: ['plan', id],
    queryFn: () => fetchJson<PlanDetail>(`/api/opportunity/${encodeURIComponent(id!)}`),
    enabled: Boolean(id),
    ...STATIC_QUERY,
  });
}

export function useProfessors() {
  return useQuery({
    queryKey: ['professors'],
    queryFn: () => fetchJson<ProfessorsPayload>('/api/professors'),
    ...STATIC_QUERY,
  });
}

export function useProfessor(siape: string | null) {
  return useQuery({
    queryKey: ['professor', siape],
    queryFn: () => fetchJson<ProfessorDetail>(`/api/professor/${encodeURIComponent(siape!)}`),
    enabled: Boolean(siape),
    ...STATIC_QUERY,
  });
}

export function useLandscape(facet: 'domain' | 'methods') {
  return useQuery({
    queryKey: ['landscape', facet],
    queryFn: () => fetchJson<LandscapePayload>(`/api/landscape?facet=${facet}`),
    ...STATIC_QUERY,
  });
}

export function useNetwork(mode: string) {
  return useQuery({
    queryKey: ['network', mode],
    queryFn: () => fetchJson<NetworkPayload>(`/api/network?mode=${encodeURIComponent(mode)}`),
    ...STATIC_QUERY,
  });
}

export function useCampaign(id: string | null) {
  return useQuery({
    queryKey: ['campaign', id],
    queryFn: () => fetchJson<CampaignDetail>(`/api/campaign/${encodeURIComponent(id!)}`),
    enabled: Boolean(id),
  });
}

export function useEngine() {
  return useQuery({
    queryKey: ['engine'],
    queryFn: () => fetchJson<EnginePayload>('/api/engine'),
    ...STATIC_QUERY,
  });
}

/**
 * Move a conversation, or annotate one.
 *
 * The funnel counts and the sidebar badge both derive from campaign rows, so a
 * write invalidates the campaign and the app state together — the alternative
 * is a funnel that disagrees with the table directly beneath it.
 */
export function useSaveOutcome(campaignId: string | null) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (patch: { siape: string; state?: ThreadState; note?: string }) =>
      postJson<OutcomeResult>('/api/outcome', { campaign_id: campaignId, ...patch }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['campaign', campaignId] });
      client.invalidateQueries({ queryKey: ['state'] });
    },
  });
}

/* ----------------------------------------------------- the store, opened */

import type {
  EntityPayload, FacetsPayload, PortfolioPayload, RecordDetail, RecordsPayload,
  SearchPayload, SourcesPayload,
} from './types';

export type RecordsQuery = Partial<Record<
  'q' | 'family' | 'form' | 'siape' | 'since' | 'until' | 'org' | 'person' | 'limit' | 'offset',
  string | number>>;

function queryString(query: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== '' && value !== null) params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : '';
}

export function useRecords(query: RecordsQuery, enabled = true) {
  return useQuery({
    queryKey: ['records', query],
    queryFn: () => fetchJson<RecordsPayload>(`/api/records${queryString(query)}`),
    enabled,
    placeholderData: (previous) => previous,
    ...STATIC_QUERY,
  });
}

export function useRecordFacets(siape = '') {
  return useQuery({
    queryKey: ['records-facets', siape],
    queryFn: () => fetchJson<FacetsPayload>(`/api/records/facets${queryString({ siape })}`),
    ...STATIC_QUERY,
  });
}

export function useRecord(id: string | null) {
  return useQuery({
    queryKey: ['record', id],
    queryFn: () => fetchJson<RecordDetail>(`/api/record/${encodeURIComponent(id!)}`),
    enabled: Boolean(id),
    ...STATIC_QUERY,
  });
}

export function usePortfolio(siape: string | null) {
  return useQuery({
    queryKey: ['portfolio', siape],
    queryFn: () => fetchJson<PortfolioPayload>(`/api/professor/${encodeURIComponent(siape!)}/portfolio`),
    enabled: Boolean(siape),
    ...STATIC_QUERY,
  });
}

export function useEntity(id: string | null) {
  return useQuery({
    queryKey: ['entity', id],
    queryFn: () => fetchJson<EntityPayload>(`/api/entity/${encodeURIComponent(id!)}`),
    enabled: Boolean(id),
    ...STATIC_QUERY,
  });
}

/** The palette's server-side search; a short query returns nothing, cheaply. */
export function useSearchAll(q: string) {
  const needle = q.trim();
  return useQuery({
    queryKey: ['search', needle],
    queryFn: () => fetchJson<SearchPayload>(`/api/search${queryString({ q: needle })}`),
    enabled: needle.length >= 2,
    placeholderData: (previous) => previous,
    staleTime: 60_000,
  });
}

export function useSources() {
  return useQuery({
    queryKey: ['sources'],
    queryFn: () => fetchJson<SourcesPayload>('/api/sources'),
    staleTime: 30_000,
  });
}
