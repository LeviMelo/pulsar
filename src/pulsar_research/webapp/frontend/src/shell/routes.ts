/* The seven screens, and what each of them is for.
 *
 * The subtitle is not decoration: someone arriving on a screen should be able
 * to tell from one line whether it answers the question they came with, without
 * reverse-engineering it from the controls.
 */

export interface RouteSpec {
  id: string;
  label: string;
  title: string;
  sub: string;
  /** Which app-state count, if any, belongs in the sidebar badge. */
  badge?: 'opportunities' | 'professors' | 'campaigns';
}

export const ROUTES: readonly RouteSpec[] = [
  {
    id: 'overview', label: 'Overview', title: 'Overview',
    sub: 'The corpus, where the funded work sits, and what still needs a decision.',
  },
  {
    id: 'opportunities', label: 'Work plans', title: 'Work plans',
    sub: 'Every plan in the cycle, ranked against the profile and openable in full.',
    badge: 'opportunities',
  },
  {
    id: 'professors', label: 'Professors', title: 'Professors',
    sub: 'Supervision capacity, research trajectory, and the evidence behind each rank.',
    badge: 'professors',
  },
  {
    id: 'landscape', label: 'Landscape', title: 'Research landscape',
    sub: 'What this ecosystem studies, how it is organised, and how the map should be read.',
  },
  {
    id: 'network', label: 'Network', title: 'Academic network',
    sub: 'Who sits next to whom — by co-authorship, by shared technique, by subject.',
  },
  {
    id: 'campaigns', label: 'Outreach', title: 'Outreach',
    sub: 'Audiences, individual drafts, and where every conversation now stands.',
    badge: 'campaigns',
  },
  {
    id: 'engine', label: 'Engine', title: 'Semantic engine',
    sub: 'Provenance, the retrieval battery, map fidelity and topic-model quality.',
  },
];
