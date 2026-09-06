# PULSAR

A local platform for modelling an academic research ecosystem — currently
UFAL/SIGAA. It acquires opportunities and professor portfolios, builds an
inspectable semantic model over them, projects everything into a typed entity
graph, measures that graph's structure, and keeps the whole derivation honest
about what is stale.

Everything runs on one machine. No data leaves it; the only network calls are to
SIGAA (to read what you can already read while logged in), to a local embedding
server, and to your own SMTP relay when you explicitly send a campaign.

`PLAN.md` is the canonical specification, architecture document, decision log and
known-problems register. Read it before changing anything substantial.

```text
┌─ PLATFORM ────────────────────────────────────────────────────────────┐
│  ACQUISITION → NORMALIZATION + PROVENANCE → THE CORPUS                │
│       ↓                                                               │
│  SEMANTIC SPACE → ranking, evidence, topics, skills, landscape        │
│       ↓                                                               │
│  ENTITY GRAPH → centrality, communities, bridging, reach, paths       │
│       ↓                                                               │
│  PIPELINE → what is stale, what would run, what re-running breaks     │
└───────────────────────────────────────────────────────────────────────┘
                                   ↓
┌─ APPLICATIONS ────────────────────────────────────────────────────────┐
│  the console  ·  outreach (audience → drafts → review → explicit send)│
└───────────────────────────────────────────────────────────────────────┘
```

The split is enforced, not aspirational: nothing above `apps/` may import
anything inside it. The platform does not know that anybody is ever written to.

## Install

```powershell
conda env create -f environment.yml
conda activate pegasus
pip install -e .
playwright install chromium
pulsar init
```

Credentials live in the environment, never in the repository:

```powershell
$env:UFAL_SIGAA_USERNAME="..."
$env:UFAL_SIGAA_PASSWORD="..."
```

`config/default.toml` declares the *names* of those variables and everything else
that is not a secret. `config/local.toml` is a git-ignored overlay for personal,
machine-specific settings — the address you send from, an alternate embedding
host — deep-merged over the defaults, so the tracked file stays generic. Copy
`config/local.toml.example` to start. `config/profile.yaml` is your research
profile: what you work on, how you work, and what you can already do.

To send mail, set the address in `config/local.toml` and the credentials in the
environment:

```powershell
$env:PULSAR_SMTP_USER="your.name@famed.ufal.br"
$env:PULSAR_SMTP_PASSWORD="<16-character Google App Password>"
```

UFAL is on Google Workspace, so submission goes through `smtp.gmail.com:587`
with your institutional identity and an App Password — an account password will
be rejected. `pulsar doctor` names whichever piece is still missing.

## Semantic engine, in one paragraph

PULSAR keeps three kinds of semantic machinery apart, because they answer
different questions. **Representations** (word/char TF-IDF, SPPMI+SVD, LSA,
Qwen3-Embedding-4B) induce a geometry and support both retrieval and structure.
**Retrieval operators** (BM25F) answer query relevance without any geometry.
**Interpretability models** (NMF topic hierarchy, the skill gazetteer) expose
structure for navigation. Scores are reported per channel — `lexical_word`,
`lexical_char`, `bm25f`, `latent`, `neural`, `fused`, `skill_match` — never
collapsed into a single unexplained number, because the benchmark shows that
literal recall and thematic affinity have different winners.

## Daily use

```powershell
pulsar doctor                       # config, credentials, corpus freshness, embedding service
pulsar sync all                     # acquire everything, then rebuild semantics
pulsar semantics build              # fit the semantic space + benchmark + score
pulsar semantics profile            # re-score after editing config/profile.yaml
pulsar semantics status             # active space/run, benchmarks, map fidelity, topics
pulsar pipeline status              # every stage: current, stale, blocked, or never run
pulsar pipeline run                 # rebuild whatever is stale, in dependency order
pulsar dashboard                    # the operator console, at localhost:8787
```

`pipeline status` is the honest picture of the derived store. Each stage declares
what it needs, what it produces, and how to tell whether its output still
reflects its inputs — so `blocked` means "fresh on its own terms, but sitting
below something that is not", which is the state worth catching. Acquisition
never runs unless asked:

```powershell
pulsar pipeline status --why        # plus what each stage is for
pulsar pipeline explain graph.project   # its inputs, and its blast radius
pulsar pipeline run --dry-run       # exactly what a real run would do
pulsar pipeline run --acquire       # include the scrapes (minutes; someone else's server)
```

Ad-hoc retrieval, on the fitted space (a query never refits anything):

```powershell
pulsar semantics search "epidemiologia espacial com dados do DATASUS"
pulsar semantics search "inferência quantitativa em saúde populacional" --entity professor
pulsar semantics search "citometria de fluxo" --entity opportunity --channel lexical_word
```

Ranked views:

```powershell
pulsar opportunities list --funded --limit 25
pulsar professors list --scope current      # who can supervise this right now
pulsar professors list --scope trajectory   # who thinks about the same problems
pulsar professors show 1157495              # portfolio + the evidence behind the rank
```

## The network

Ranking answers "who fits". The graph answers the questions a ranked table
cannot: who bridges two groups that otherwise do not talk, which clusters the
faculty actually decomposes into, whose collaboration reaches outside the
institution, and how you would get an introduction.

```powershell
pulsar graph build                          # project acquired facts into the graph
pulsar graph measure                        # centrality, communities, bridging, reach
pulsar graph status                         # size, composition, freshness
pulsar graph find nobrega                   # name → entity id
pulsar graph show 1157495                   # one entity: measures + neighbourhood
pulsar graph top bridging --min-degree 8    # who routes work outside their own cluster
pulsar graph path 1157495 3882870           # the strongest short chain between two people
```

Every command that takes an entity accepts a SIAPE, an entity id or a name
fragment, and refuses rather than guessing when a fragment is ambiguous.

The console reads the same graph. Its Network screen gains a fifth question —
*where the faculty actually splits* — which colours by co-authorship cluster and
sizes by who sits between them; putting that next to *centre* is the whole point,
since one is who publishes with whom and the other is who is filed under whom.

Structural measures are computed over co-authorship alone — shared technique and
shared subject are inferred similarities, not observed ties, and mixing them into
one centrality gives a number that means neither. Shares like `bridging` are
trivially 1.0 on a vertex with two neighbours, so `graph top` applies a degree
floor and prints the degree beside the value.

## Acquisition

```powershell
pulsar sync opportunities   # authenticated SIGAA discovery + detail backfill (never applies)
pulsar sync professors      # public faculty pages + embedded Lattes, archived and imported
pulsar sync applications    # authoritative "Meus Registros de Interesse" state
```

Reading and mutating SIGAA are separate operations. Discovery cannot apply to
anything. Applying is one opportunity at a time and requires `--confirm`:

```powershell
pulsar applications apply 98379721 --confirm
```

The Playwright navigation, JSF action beans, selectors and Windows-1252 detail
fetch in `acquisition/sigaa_authenticated.py` are a conservative port of the
working prototype and are pinned by a contract test. Do not refactor them without
live regression testing.

## Campaigns

Creating a campaign is not sending it. Creation freezes the audience, the exact
qualifying evidence, and the semantic provenance that produced the ranking, then
generates one independently editable draft per recipient.

```powershell
pulsar campaign audience --funded --min-opportunity-percentile 70 --skill datasus
pulsar campaign create "PIBIC epidemiologia" --funded --min-opportunity-percentile 70
pulsar campaign show <id>
pulsar campaign preview <id>          # writes an HTML preview file
pulsar campaign select <id> <siape> --deselect
pulsar campaign send <id>             # dry run: prints what WOULD be sent
pulsar campaign send <id> --confirm   # actually sends the selected drafts
```

Messages are sent exactly as stored — templates are never re-rendered at send
time — and anything already marked `sent` is never sent twice.

## Data layout

```text
data/pulsar.duckdb              canonical analytical store
data/public_sigaa/              raw archived public SIGAA + Lattes provenance (expensive; keep)
data/state/                     ledger journal + professor seed CSV
data/cache/semantic_spaces/     fitted model artifacts (regenerable)
data/exports/                   campaign exports and previews
```

`data/` is git-ignored in full. The raw archives under `data/public_sigaa/` are
expensive acquired provenance, not build output — back them up, never delete them
to reclaim space. Everything under `data/cache/` is regenerable.

## The console

`pulsar dashboard` serves a React single-page app from `http.server` on
localhost:8787. The source is a Vite project at
`src/pulsar_research/webapp/frontend/`; its build output is
`src/pulsar_research/webapp/static/`, which is what the server hands to the
browser and what the wheel ships. That output is committed, so running the
console needs Python only — Node is needed only to change the front end.

```powershell
cd src/pulsar_research/webapp/frontend
npm install
npm run dev        # localhost:5173, hot reload, /api proxied to the console
npm run build      # rebuild ../static — commit the result
```

Run `pulsar dashboard` in another terminal while `npm run dev` is up: the dev
server proxies the API to it, so both halves are live at once.

The **Semantic engine** screen has a *Freshness* tab: every pipeline stage, its
state, and what re-running it would invalidate — the same table `pulsar pipeline
status` prints. The console shows it and never runs it; acquisition stays at a
terminal behind `--acquire`.

### As a window rather than a tab

```powershell
pip install "pulsar[desktop]"
pulsar dashboard --window
```

Same server, same pages, drawn by the operating system's own webview instead of
a browser tab — WebView2 on Windows, WebKit on macOS. Closing the window stops
the server.

The browser stays the default for one reason: every screen's state lives in the
URL, and an address bar is how a view gets copied to someone else. The window's
**View → Open this view in a browser** hands the current URL back when a link is
what you want.

## Tests

```powershell
pytest
```

The console's force layout has its own check, because its failure mode is
silent — a graph always looks like a graph, even when the frame rather than the
data is deciding where nodes sit:

```powershell
cd src/pulsar_research/webapp/frontend
npm run layout:check
```

The suite covers the corpus model, representation determinism, fusion algebra,
the skill taxonomy, provenance identity separation, staleness detection, and a
contract test pinning the fragile SIGAA literals.
