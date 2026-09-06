/* Overview — what needs deciding, and nothing else.
 *
 * An earlier version of this screen opened with a comment saying that counters
 * are what a dashboard shows when it does not know what its reader is trying to
 * do, and then showed six counters, a duplicate of the Landscape map, a Lorenz
 * curve and a bar chart of technique supply. Four of those five things are the
 * Landscape screen's job, and none of them is a decision.
 *
 * The reader is one applicant inside one application cycle. At any moment there
 * is exactly one live question — which conversations are still open, and which
 * funded plan deserves the next move — so that is the whole page. Everything
 * here is clickable, and everything opens in the rail rather than routing away.
 */

import { useNavigate } from 'react-router-dom';
import { useAppState, useCampaign, useOpportunities } from '../api/client';
import type { CampaignDetail, CampaignSummary, PlanRow, ThreadState } from '../api/types';
import { useRail, type RailStack } from '../components/Rail';
import { RailPane } from '../components/RailPane';
import { STATE_LABEL, stateTone } from '../components/records';
import { Table } from '../components/Table';
import { Banner, Chip, Empty, FieldBlock, Loading, Meter, Panel } from '../components/ui';
import { ago, date, num, short, shortName, text } from '../lib/format';
import { pathTo } from '../lib/params';

const LIVE: ThreadState[] = ['awaiting', 'replied', 'open'];

export function Overview() {
  const rail = useRail();
  const { data: state } = useAppState();
  const { data: opps, isPending: oppsPending } = useOpportunities();
  const latest = (state?.campaigns || [])[0] ?? null;
  const { data: campaign, isPending: campaignPending } = useCampaign(latest?.campaign_id ?? null);

  if (oppsPending || (latest && campaignPending)) return <Loading />;
  if (!opps) return <Banner tone="bad"><div>No corpus payload.</div></Banner>;

  return (
    <div className="split wide">
      <div>
        {campaign && latest
          ? <LiveThreads campaign={campaign} meta={latest} rail={rail} />
          : <NothingSent />}
        <NextMoves rows={opps.rows} campaign={campaign ?? null} rail={rail} />
      </div>
      <RailPane stack={rail} rootLabel="Where things stand"
                root={<Briefing rows={opps.rows} campaign={campaign ?? null} meta={latest} />} />
    </div>
  );
}

/* ------------------------------------------------------------- in flight */

function LiveThreads({ campaign, meta, rail }: {
  campaign: CampaignDetail; meta: CampaignSummary; rail: RailStack;
}) {
  const navigate = useNavigate();
  const funnel = campaign.funnel || {};
  const live = campaign.rows.filter((r) => r.state && LIVE.includes(r.state));
  const indicated = campaign.rows.find((r) => r.state === 'indicated');
  const sent = campaign.summary?.by_status?.sent ?? 0;

  return (
    <Panel
      style={{ marginBottom: '14px' }}
      title={<>Conversations in flight <Chip tone="accent">{meta.name}</Chip></>}
      note={indicated
        ? `The SIGAA indication has gone to ${indicated.professor_name}. Everything else in `
          + 'this campaign is now history.'
        : `${num(sent)} messages sent, ${num(live.length)} still live. The indication is `
          + 'unique, so at most one of these can end in one.'}>
      <div className="funnel">
        {campaign.states.map(({ key, label }) => (
          <button key={key} type="button"
                  title={`Open the Outreach screen filtered to ${label}`}
                  onClick={() => navigate(pathTo('campaigns', {
                    c: campaign.campaign_id, state: key,
                  }))}>
            <span className="n">{num(funnel[key] ?? 0)}</span>
            <span className="k">{label}</span>
          </button>
        ))}
      </div>
      {live.length > 0 && (
        <div style={{ marginTop: '14px' }}>
          <Table rows={live} rowKey={(r) => r.siape} maxHeight="340px"
                 emptyMessage="Every conversation is closed."
                 // Reading the thread is going deeper on this page, not leaving it.
                 onSelect={(r) => rail.replace({
                   kind: 'message', campaign: campaign.campaign_id, siape: r.siape,
                   label: shortName(r.professor_name),
                 })}
                 columns={[
                   { key: 'professor_name', label: 'Professor', truncate: true,
                     render: (r) => <span>{text(r.professor_name)}</span> },
                   { key: 'state', label: 'State', width: '150px',
                     render: (r) => (
                       <Chip tone={stateTone(r.state)}>
                         {r.state ? STATE_LABEL[r.state] || r.state : '—'}
                       </Chip>
                     ) },
                   { key: 'sent_at', label: 'Sent', width: '90px',
                     render: (r) => (
                       <span className="faint" title={date(r.sent_at)}>{ago(r.sent_at)}</span>
                     ) },
                   { key: 'primary_title', label: 'About', truncate: true,
                     render: (r) => <span className="muted">{short(r.primary_title, 56)}</span> },
                   { key: 'opportunity_percentile', label: 'Fit', align: 'right', width: '110px',
                     render: (r) => <Meter value={r.opportunity_percentile} /> },
                 ]} />
        </div>
      )}
    </Panel>
  );
}

function NothingSent() {
  return (
    <Panel style={{ marginBottom: '14px' }}>
      <Empty>
        No campaign has been sent from this database. Build an audience with
        {' '}<code className="mono">pulsar campaign new</code>, review the drafts on the
        Outreach screen, and send from a terminal.
      </Empty>
    </Panel>
  );
}

/* ------------------------------------------------------------ next moves */

/**
 * Funded, well-matched plans whose supervisor has heard nothing.
 *
 * The only table on the page that proposes an action rather than reporting one,
 * which is why it sits directly under the live threads: everything the campaign
 * did not cover, best fit first.
 */
function NextMoves({ rows, campaign, rail }: {
  rows: readonly PlanRow[]; campaign: CampaignDetail | null; rail: RailStack;
}) {
  const contacted = new Set((campaign?.rows || []).map((r) => r.siape));
  const candidates = rows
    .filter((r) => r.has_funding && !contacted.has(r.professor_siape || ''))
    .sort((a, b) => (b.rank_pct ?? -1) - (a.rank_pct ?? -1));

  return (
    <Panel
      flush={candidates.length > 0}
      title={`Funded, and nobody has written to them (${num(candidates.length)})`}
      note={candidates.length
        ? 'Funded plans whose supervisor received nothing in the campaign above, best fit '
          + 'first. Open one to read it without leaving this page.'
        : 'Every funded plan in the corpus belongs to a supervisor already written to.'}>
      {candidates.length
        ? (
          <Table rows={candidates} rowKey={(r) => r.id_opportunity} maxHeight="460px"
                 onSelect={(r) => rail.replace({
                   kind: 'plan', id: r.id_opportunity,
                   label: short(r.plan_title || r.project_title, 26),
                 })}
                 columns={[
                   { key: 'rank_pct', label: 'Fit', align: 'right', width: '110px',
                     render: (r) => <Meter value={r.rank_pct} /> },
                   { key: 'plan_title', label: 'Work plan', truncate: true,
                     render: (r) => short(r.plan_title || r.project_title, 84) },
                   { key: 'professor_name', label: 'Professor', truncate: true, width: '200px',
                     render: (r) => text(r.professor_name) },
                   { key: 'funded_slots', label: 'Slots', align: 'right', width: '70px',
                     render: (r) => num(r.funded_slots) },
                   { key: 'center', label: 'Centre', width: '100px',
                     render: (r) => <span className="faint">{text(r.center)}</span> },
                 ]} />
        )
        : <Empty>Nothing outstanding.</Empty>}
    </Panel>
  );
}

/* -------------------------------------------------------------- briefing */

/**
 * What the rail says before anything is clicked.
 *
 * Not an instruction to click something — the state of the cycle in sentences,
 * which is what someone opening this console after two days away actually
 * wants: what went out, what came back, what is still unanswered.
 */
function Briefing({ rows, campaign, meta }: {
  rows: readonly PlanRow[]; campaign: CampaignDetail | null; meta: CampaignSummary | null;
}) {
  const navigate = useNavigate();
  const funded = rows.filter((r) => r.has_funding);
  const slots = funded.reduce((sum, r) => sum + (Number(r.funded_slots) || 0), 0);
  const applied = rows.filter((r) => /inscrit/i.test(String(r.application_status || ''))
    && !/não/i.test(String(r.application_status || '')));
  const live = (campaign?.rows || []).filter((r) => r.state && LIVE.includes(r.state));
  const replied = (campaign?.rows || []).filter((r) => r.state && r.state !== 'awaiting');
  const sent = campaign?.summary?.by_status?.sent ?? 0;

  return (
    <div>
      <div className="detail-head">
        <h2>Where things stand</h2>
        <p className="sub">
          {meta ? `${meta.name} · ${date(meta.created_at)}` : 'No campaign yet'}
        </p>
      </div>
      <div className="panel-body">
        <div className="prose">
          {num(rows.length)} work plans are open in this cycle, {num(funded.length)} of them
          {' '}funded, carrying {num(slots)} slots between them. You have registered interest in
          {' '}{num(applied.length)}.
        </div>
        {campaign && (
          <div className="prose" style={{ marginTop: '12px' }}>
            {num(sent)} of those supervisors were written to.{' '}
            {replied.length
              ? `${num(replied.length)} have moved off the default state; `
              : 'None has been marked as replying yet; '}
            {num(live.length)} conversations are still live.
          </div>
        )}
        <p className="panel-note" style={{ marginTop: '14px' }}>
          Only one SIGAA indication can be accepted, so every open thread is competing with
          the others. The table on the left is what has not been asked at all.
        </p>
        <FieldBlock title="Where to look next" style={{ marginTop: '16px' }}>
          <div className="chips">
            <Chip onClick={() => navigate(pathTo('network', { ask: 'reach' }))}>
              Who a live thread could reach
            </Chip>
            <Chip onClick={() => navigate(pathTo('landscape', { lens: 'funded-unapplied' }))}>
              Funded work nobody answered
            </Chip>
            <Chip onClick={() => navigate(pathTo('opportunities', { funded: 1, min: 90 }))}>
              Top-decile funded plans
            </Chip>
          </div>
        </FieldBlock>
      </div>
    </div>
  );
}
