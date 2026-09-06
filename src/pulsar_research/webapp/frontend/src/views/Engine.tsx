/* Semantic engine — what was built, how well it retrieves, and from what.
 *
 * This is the page that has to survive a sceptical reader, so it states method
 * before result everywhere: which tasks the battery runs, why different channels
 * win different ones, why topic-model selection deliberately ignores
 * reconstruction error, and what the map's stress figure actually licenses.
 */

import { useState } from 'react';
import { useEngine, usePipeline } from '../api/client';
import type { EnginePayload, PipelineStage, StageState } from '../api/types';
import { BarsH, GroupedBars } from '../components/charts';
import { copyText } from '../components/records';
import { Table } from '../components/Table';
import { Banner, Chip, Empty, FieldBlock, Loading, Stat, Tabs, type Tone } from '../components/ui';
import { num, text } from '../lib/format';
import { useViewParams } from '../lib/params';
import { HeaderActions } from '../shell/Shell';

const TABS = ['benchmarks', 'map', 'topics', 'freshness', 'provenance'] as const;
type Tab = typeof TABS[number];

export function Engine() {
  const params = useViewParams();
  const tab = params.pick<Tab>('tab', TABS, 'benchmarks');
  const { data, error, isPending } = useEngine();

  if (isPending) return <Loading />;
  if (error || !data) {
    return <Banner tone="bad"><div>{String((error as Error)?.message || error)}</div></Banner>;
  }

  const stats = data.space.stats || {};

  return (
    <div className="grid">
      <HeaderActions>
        <button type="button" className="btn sm"
                onClick={() => copyText(
                  JSON.stringify({ space: data.space.identity, run: data.run.profile }, null, 2),
                  'Provenance copied')}>
          Copy provenance
        </button>
      </HeaderActions>

      <div className="grid cols-6">
        <Stat label="Space" value={tail(data.space.space_id)}
              foot={text(data.space.created_at).slice(0, 10)} />
        <Stat label="Run" value={tail(data.run.run_id)} foot="current profile" />
        <Stat label="Training docs" value={num(stats.training_documents ?? 0)} />
        <Stat label="Atoms" value={num(stats.atoms ?? 0)} foot="indexed evidence" />
        <Stat label="Channels" value={num((stats.channels || []).length)} />
        <Stat label="Benchmarks"
              value={num(new Set(data.benchmarks.map((b) => b.benchmark)).size)}
              foot="retrieval tasks" />
      </div>

      <section className="panel">
        <Tabs items={[
          ['benchmarks', 'Retrieval battery'],
          ['map', 'Map fidelity'],
          ['topics', 'Topic quality'],
          ['freshness', 'Freshness'],
          ['provenance', 'Provenance'],
        ] as const} active={tab} onChange={(key) => params.set({ tab: key })} />
        <div className="panel-body">
          {tab === 'benchmarks' && <Benchmarks data={data} />}
          {tab === 'map' && <MapFidelity data={data} />}
          {tab === 'topics' && <TopicQuality data={data} />}
          {tab === 'freshness' && <Freshness />}
          {tab === 'provenance' && <Provenance data={data} />}
        </div>
      </section>
    </div>
  );
}

function tail(value: unknown) {
  const raw = text(value, '—');
  return raw.length > 12 ? `…${raw.slice(-10)}` : raw;
}

/* ------------------------------------------------------------ benchmarks */

function Benchmarks({ data }: { data: EnginePayload }) {
  const metrics = [...new Set(data.benchmarks.map((b) => b.metric))].sort();
  const [metric, setMetric] = useState(metrics.includes('mrr') ? 'mrr' : metrics[0]);
  const rows = data.benchmarks.filter((b) => b.metric === metric);
  const notes = [...new Map(rows.map((r) => [r.benchmark, r.note])).entries()]
    .map(([benchmark, note]) => ({ benchmark, note }));

  return (
    <div>
      <p className="panel-note" style={{ marginBottom: '12px' }}>
        Deterministic, self-supervised retrieval tasks built from PULSAR’s own corpus, so
        no external labels and no hand tuning are involved. Different tasks have different
        winners on purpose: literal recall and thematic affinity are different problems, and
        a channel that wins both is unlikely to exist. The fused channel is drawn solid.
      </p>
      <label className="field" style={{ maxWidth: '180px', marginBottom: '14px' }}>
        Metric
        <select value={metric} onChange={(e) => setMetric(e.target.value)}>
          {metrics.map((m) => <option key={m} value={m}>{m.toUpperCase()}</option>)}
        </select>
      </label>
      <GroupedBars
        rows={rows.map((r) => ({
          group: r.benchmark, series: r.channel, value: r.value, note: r.note,
        }))}
        format={(v) => num(v, 3)} />
      <FieldBlock title="What each task measures" style={{ marginTop: '18px' }}>
        <Table rows={notes} rowKey={(r) => r.benchmark} columns={[
          { key: 'benchmark', label: 'Task', width: '210px',
            render: (r) => <span className="mono">{String(r.benchmark).replace(/_/g, ' ')}</span> },
          { key: 'note', label: 'Meaning',
            render: (r) => <span className="muted">{text(r.note, 'No description recorded.')}</span> },
        ]} />
      </FieldBlock>
    </div>
  );
}

/* ---------------------------------------------------------------- map */

function MapFidelity({ data }: { data: EnginePayload }) {
  const rows = Object.entries(data.map).map(([metric, value]) => ({ metric, value }));
  const stress = data.map.stress;
  return (
    <div>
      <p className="panel-note" style={{ marginBottom: '14px' }}>
        Stress is Kruskal stress-1 after optimal rescaling: 0 is perfect, 0.05–0.15 is a good
        map, and above 0.2 the two-dimensional plot should be read as neighbourhoods only.
        The correlations compare plotted distances against true distances in the full space.
      </p>
      {stress !== undefined && (
        <Banner tone={stress < 0.15 ? 'info' : ''}>
          <div>
            <b>Stress {stress.toFixed(3)}. </b>
            {stress < 0.15
              ? 'Positions on the landscape map are trustworthy as relative structure.'
              : 'Treat the landscape map as a neighbourhood sketch, not as a metric space.'}
          </div>
        </Banner>
      )}
      <Table rows={rows} rowKey={(r) => r.metric} columns={[
        { key: 'metric', label: 'Metric', render: (r) => <span className="mono">{r.metric}</span> },
        { key: 'value', label: 'Value', align: 'right', render: (r) => num(r.value, 4) },
      ]} />
    </div>
  );
}

/* -------------------------------------------------------------- topics */

function TopicQuality({ data }: { data: EnginePayload }) {
  const blocks = Object.entries(data.topic_quality || {})
    .filter(([, v]) => v && Object.keys(v).length);
  if (!blocks.length) return <Empty>No topic diagnostics were recorded for this space.</Empty>;
  return (
    <div>
      <p className="panel-note" style={{ marginBottom: '14px' }}>
        Model selection weights coherence, stability under deterministic perturbation,
        independent support and exclusivity. It never weights reconstruction error, which
        always improves as factors are added and therefore measures capacity rather than
        quality.
      </p>
      {blocks.map(([facet, quality]) => (
        <FieldBlock key={facet} title={`${facet} model`} note={quality.selection_reason}>
          <div className="grid cols-4" style={{ marginBottom: '10px' }}>
            <Stat label="Factors" value={num(quality.selected_k ?? 0)} />
            <Stat label="NPMI coherence" value={num(quality.npmi ?? 0, 3)} />
            <Stat label="Stability" value={num(quality.stability ?? 0, 3)} />
            <Stat label="Exclusivity" value={num(quality.exclusivity ?? 0, 3)} />
          </div>
          {(quality.candidates || []).length > 0 && (
            <BarsH rows={(quality.candidates || []).map((c) => ({
              label: `k = ${c.k}`, value: c.score,
            }))} format={(v) => num(v, 3)} meta={() => 'combined selection score'} />
          )}
        </FieldBlock>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------- provenance */

function Provenance({ data }: { data: EnginePayload }) {
  return (
    <div className="grid cols-2">
      <FieldBlock title="Semantic space identity">
        <p className="panel-note" style={{ marginBottom: '8px' }}>
          Corpus, preprocessing, architecture, embedding model and library versions. Changing
          any of these produces a different space id, so a stale number can never be quoted
          as a current one.
        </p>
        <pre className="pre">{JSON.stringify(data.space.identity, null, 2)}</pre>
        <p className="panel-note" style={{ marginTop: '8px' }}>
          built {text(data.space.created_at)} · corpus fingerprint{' '}
          <span className="mono">{String(data.space.fingerprint ?? '').slice(0, 24)}</span>
        </p>
      </FieldBlock>
      <FieldBlock title="Profile run">
        <p className="panel-note" style={{ marginBottom: '8px' }}>
          The space plus the operator’s declared interests. Editing config/profile.yaml
          creates a new run and leaves topics, geometry and benchmarks untouched.
        </p>
        <pre className="pre">{JSON.stringify(data.run.profile, null, 2)}</pre>
      </FieldBlock>
    </div>
  );
}

/* ------------------------------------------------------------- freshness */

/* Six screens report numbers derived from a store that somebody scraped at some
 * point. Until now nothing said whether that derivation was still current, so
 * "is this the ranking from before or after the last sync" was a question you
 * answered by remembering. */

const STAGE_TONE: Record<StageState, Tone> = {
  ok: 'good',
  stale: 'bad',
  never: 'warn',
  blocked: 'warn',
  unknown: '',
};

const STAGE_MEANING: Record<StageState, string> = {
  ok: 'current against its inputs',
  stale: 'its inputs have moved since it ran',
  never: 'has not run',
  blocked: 'current on its own terms, but sitting below something that is not',
  unknown: 'the check could not decide',
};

function Freshness() {
  const { data, error, isPending } = usePipeline();
  if (isPending) return <Loading />;
  if (error || !data) {
    return <Banner tone="bad"><div>{String((error as Error)?.message || error)}</div></Banner>;
  }
  const stages = data.stages;
  if (!stages.length) return <Empty>No pipeline stages are declared.</Empty>;
  const behind = stages.filter((s) => s.state === 'stale' || s.state === 'never');

  return (
    <div>
      <p className="panel-note" style={{ marginBottom: '12px' }}>
        Each stage declares what it needs, what it produces, and how to tell whether its
        output still reflects its inputs — read from the store, never from a timestamp
        file. <b>Blocked</b> is the state worth catching: the stage is fine on its own
        terms, and re-running it alone would still answer confidently from stale inputs.
        The console never runs any of this; acquisition costs minutes on somebody else’s
        server, and a scrape a browser tab can start is an accident waiting to happen.
      </p>
      {behind.length > 0 && (
        <Banner tone="warn">
          <div>
            {behind.length === 1 ? 'One stage is' : `${behind.length} stages are`} behind.
            Run <span className="mono">pulsar pipeline run</span> to rebuild what is
            derived, or <span className="mono">pulsar pipeline run --acquire</span> to
            include the scrapes.
          </div>
        </Banner>
      )}
      <Table rows={stages} rowKey={(r) => r.stage} columns={[
        { key: 'stage', label: 'Stage', width: '190px',
          render: (r: PipelineStage) => (
            <span>
              <span className="mono">{r.stage}</span>
              {r.acquires && <span className="muted"> · network</span>}
            </span>
          ) },
        { key: 'state', label: 'State', width: '110px',
          render: (r: PipelineStage) => (
            <Chip tone={STAGE_TONE[r.state]} title={STAGE_MEANING[r.state]}>{r.state}</Chip>
          ) },
        { key: 'detail', label: 'Why', render: (r: PipelineStage) => (
          <span>
            {text(r.detail, '—')}
            <div className="muted" style={{ marginTop: '2px' }}>{r.title}</div>
          </span>
        ) },
        { key: 'blocks', label: 'Re-running this invalidates', width: '220px',
          render: (r: PipelineStage) => (
            <span className="muted mono">{r.blocks.join(', ') || '—'}</span>
          ) },
      ]} />
    </div>
  );
}
