# PULSAR — Canonical Project Ledger

> **Product:** PULSAR — local research-intelligence and opportunity-prospecting system
> **Operator:** Levi de Melo Amorim (FAMED/UFAL)
> **Repository:** `C:\Users\Galaxy\LEVI\projects\pulsar`
> **Environment:** conda env `pegasus` (Python 3.11), Windows 11, RTX 4060 6 GB
> **Engine version:** semantic engine 3.0.0 · DuckDB schema 3
> **Last substantial revision:** 2026-09-04 (takeover, audit, semantic redesign)

This is the single source of truth for what PULSAR is, how it is built, why it is
built that way, what is known to be wrong with it, and what to do next. It
replaces the former `ARCHITECTURE.md`, which has been merged here and retired.

---

## 0. How to read this document

Sections 1–3 are **goals and constraints**: preserve them. Sections 4–17 are the
**current design**: change them whenever evidence says something better exists,
but record the change in §24. Sections 19–21 are **live state**: keep them
current as work lands.

Two rules govern every change:

1. **Product goals outrank the existing architecture.** If a design here proves
   inferior under careful evaluation, replace it and write down why.
2. **Semantic quality is settled empirically**, against PULSAR's own tasks, never
   by benchmark prestige or aesthetic preference. A more complicated method
   survives only if it earns its complexity on §9's battery.

---

## 1. What PULSAR is

PULSAR is not a SIGAA scraper. Scraping is the cheapest part.

It is a local system that models an academic research ecosystem well enough to
*act* inside it. Two layers, and the boundary between them is load-bearing:

```text
┌─ PLATFORM ─────────────────────────────────────────────────┐
│  DATA ACQUISITION                                          │
│        ↓                                                   │
│  NORMALIZATION + PROVENANCE                                │
│        ↓                                                   │
│  THE CORPUS  ──→  SEMANTIC SPACE  ──→  RANKING / EVIDENCE  │
│        ↓                ↓                                  │
│  THE ENTITY GRAPH  ←────┘                                  │
│        ↓                                                   │
│  STRUCTURAL MEASURES: centrality, communities, bridging,   │
│                       external reach, introduction paths   │
│        ↓                                                   │
│  THE PIPELINE: what is stale, what would run, what breaks  │
└────────────────────────────────────────────────────────────┘
                        ↓
┌─ APPLICATIONS ─────────────────────────────────────────────┐
│  the console — read the ecosystem                          │
│  outreach — audience, drafts, review, explicit send        │
└────────────────────────────────────────────────────────────┘
```

The platform knows nothing about writing to anybody. Outreach was the first
application built on it and for a while was indistinguishable from the project
itself — the ranking existed to pick recipients, and the console's serving layer
imported the email package to find out how to spell someone's name. Both halves
still matter: analysis without action is a dashboard, action without analysis is
spam. But an ecosystem model that can only be used to send letters is a letter
generator with extra steps, and the next application built here should not have
to import the first one.

### The questions it must answer

- Which current opportunities are actually interesting to me?
- Which professors are intellectually compatible with my trajectory?
- Which professors have funded opportunities *right now*?
- Which opportunities use methods or technical skills I already possess?
- Which research groups are adjacent to my interests even when their vocabulary differs?
- Why did PULSAR rank this professor or opportunity highly?
- Which exact project, publication or work plan supports that judgment?
- What does the research landscape itself look like? Which methods, domains and
  skills dominate it?
- Who should I contact, why, have I already contacted them, what did I send, what
  is still a draft, and what did the evidence look like at the time?

And, since §13, the ones no ranked table can answer:

- Which clusters does this faculty actually decompose into, as opposed to the
  units it is administratively divided into?
- Who sits between two groups that otherwise do not talk?
- Whose collaboration reaches outside the institution, and whose does not?
- How would I get an introduction to this person?

Both halves matter. Analysis without action is a dashboard; action without
analysis is spam.

---

## 2. Why PULSAR exists

The operator is one medical student choosing where to invest years of research
effort inside a university with hundreds of researchers, whose public information
is scattered across an authenticated SIGAA portal, seven public professor tabs and
an embedded Lattes JSON blob. The manual version of this work — read every work
plan, cross-reference every Lattes CV, remember who was contacted — does not scale
past about a dozen professors and is not reproducible.

The system's value is therefore *judgment support with receipts*: not "here are
187 rows", but "these six professors, ranked, and here is the exact 2026 funded
work plan and 2023 publication behind each ranking."

---

## 3. Operating principles

### 3.1 Local-first

Everything runs on one machine. DuckDB is the analytical store; raw acquired
bytes stay on disk. Embeddings come from a local LM Studio server. No corpus text
is sent to a third party.

### 3.2 DuckDB is the canonical analytical store

Two layers, never mixed:

- **Acquired facts** — `opportunities`, `professors`, `applications`,
  `sigaa_public_*`. Written only by acquisition. These are expensive and, for the
  authenticated corpus, sometimes unrepeatable.
- **Derived intelligence** — everything keyed by `space_id` or `run_id`.
  Disposable and always rebuildable from acquired facts plus configuration.

If a derived table cannot be regenerated by `pulsar semantics build`, it is a bug.

### 3.3 Explicit mutations only

**SIGAA.** Reading and mutating are separate operations in separate code paths.
`sync opportunities` and `sync applications` cannot submit an application; there
is no code path from discovery to `apply_one`. `applications apply` takes exactly
one opportunity id, prints the record, and refuses without `--confirm`.

**Email.** Creating a campaign never opens an SMTP connection. Delivery reads
persisted, individually reviewed drafts; only recipients marked `selected` are
sent; a message marked `sent` is never re-sent; `campaign send` without
`--confirm` is a dry run that prints what would go out.

**Secrets** never enter source control. `config/default.toml` declares the *names*
of environment variables; values live in the environment or in a git-ignored
`.env`, which `AppConfig.load` reads from the project root (a variable already
set in the shell always wins over the file). Personal but
non-secret settings — the address you send from — go in the git-ignored
`config/local.toml`, which is deep-merged over the tracked defaults so a shared
file never carries one operator's identity.

### 3.4 Explainability is a first-class feature

Every ranking must decompose into records a human can check. A professor's score
is an aggregate over *atoms* — one project, one article, one work plan — and the
top atoms are persisted with the run. A campaign recipient carries the exact
qualifying opportunity, its funded slots, its edital, the matched skills and the
top portfolio evidence, frozen at creation time.

### 3.5 Semantic scores are rankings, not probabilities

Cosine similarities and rank-fusion outputs are not calibrated. The UI shows
**within-corpus percentiles** and says so. Nothing in PULSAR should be read as
"73% likely to be a good fit".

---

## 4. Repository layout

The directory structure carries an argument. Everything above `apps/` models an
academic network and keeps it current; nothing in it knows that anybody is ever
written to. `apps/` holds programs built on that. **Nothing under `apps/` may be
imported by anything above it.**

```text
config/
  default.toml                 non-secret configuration; every semantics key is hashed into space_id
  local.toml                   git-ignored personal overlay, deep-merged over default.toml
  profile.yaml                 the operator profile (domain / methods / skills / skill_ids)
src/pulsar_research/
  cli.py                       Typer CLI, grouped by subsystem
  config.py                    paths + config sections; secrets by env-var NAME only
  db.py                        DuckDB schema, forward-only migration, query helpers
  acquisition/
    sigaa_authenticated.py     Playwright/JSF automation (fragile; pinned by contract test)
    sigaa_public.py            orchestrates the public archiver + import
    sigaa_public_scraper.py    exhaustive public-page archiver (raw bytes → JSONL → DuckDB)
    public_dataset.py          imports the archived corpus into the canonical store
    ledger.py                  crash-safe sync journal, CSV robustness, SIAPE resolution
  semantics/
    normalize.py               stopwords, citation scrubbing, folding, deterministic joins
    corpus.py                  atoms, field views, project aggregation, corpus fingerprint
    representations.py         TF-IDF, LSA, SPPMI+SVD, neural, block/joint-SVD fusion
    retrieval.py               BM25F, RRF, percentile ranks
    embedding.py               local embedding provider + DuckDB-backed vector cache
    topics.py                  NMF hierarchy, model selection, stability diagnostics
    skills.py                  deterministic multi-label skill gazetteer + discovery
    landscape.py               PCoA → SMACOF maps with fidelity diagnostics
    evidence.py                professor portfolio scoring (current vs trajectory)
    benchmark.py               the semantic regression battery
    provenance.py              semantic_space_id / profile_run_id / staleness
    engine.py                  orchestration: build_space, run_profile, persistence
  graph/                       §13
    model.py                   kinds, relations, endpoint rules, id minting
    identity.py                name folding, accented-name recovery, alias resolution
    project.py                 acquired facts → entities and edges
    store.py                   wholesale write, neighbourhoods, adjacency, rankings
  intelligence/
    metrics.py                 scientometrics: concentration, portfolio counts, collaboration
    network.py                 centrality, communities, bridging, reach, introduction paths
  pipeline/                    §14
    registry.py                the stage DAG, freshness probes, blast radius
    runner.py                  planning and execution, journalled into sync_runs
  webapp/
    server.py                  the console's HTTP layer; serves the built frontend
    payloads.py                every console read, as JSON
    network.py                 the three faculty graphs the network view draws
    desktop.py                 the same console in a native window (pywebview)
    frontend/                  Vite + React + TypeScript sources
    static/                    the frontend build output (generated)
  dashboard/queries.py         console SQL, reusable from a notebook
  apps/                        programs built on the platform, never imported by it
    outreach/
      selectors.py             AudienceQuery → recipients with evidence and rationale
      campaigns.py             frozen snapshots, individual drafts, previews, exports
      render.py                Jinja2 → plaintext + HTML theme
      caixa.py, saudacao.py    the text craft: de-shouting, salutation, name handling
      outcomes.py              what came of each thread
      mailer.py                the only module that can send email
      templates/               subject/body Jinja2 + the HTML email theme
tests/                         corpus, representations, skills, provenance, graph,
                               measures, pipeline, SIGAA contract
data/                          git-ignored; see §20
```

---

## 5. Data sources

### 5.1 Authenticated SIGAA

Playwright drives a real logged-in session. Three operations:

1. **Opportunity discovery + detail backfill.** Menu action
   `agregadorBolsas.iniciarBuscar`, per-center search, table scrape, then a
   browser-side `fetch()` of each work plan's wizard page decoded as
   **Windows-1252** (the server lies about its charset).
2. **Application-state synchronization.** Menu action
   `interessadoBolsa.acompanharInscricoes`, filtered to `PESQUISA` rows.
3. **Explicit single-opportunity application.** Fills `form:qualificacoes` from
   `profile.yaml` and clicks `form:inscreverse`.

JSF menu navigation works by writing `jscook_action` into
`menu:form_menu_discente` and submitting; the exact key discovery loop is
preserved verbatim. `tests/test_fragile_sigaa_contract.py` pins the literals so a
careless refactor fails loudly instead of silently at 2 a.m. against a live portal.

Long crawls checkpoint into `data/state/opportunity_ledger.json` after each row;
DuckDB is materialized at operation boundaries. The ledger is provenance, not a
second source of truth.

### 5.2 Public SIGAA professor corpus

`sigaa_public_scraper.py` resolves SIAPEs from the public faculty search and
archives seven deterministic tabs per professor plus detail pages, retaining raw
bytes, normalized UTF-8, generic DOM/table/field/link extraction, photos, and the
embedded `var curriculo` Lattes object (parsed with `json5` — it is not strict
JSON). Output: JSONL + Parquet + a standalone `sigaa_ufal.duckdb`, imported into
the canonical store as `sigaa_public_*`.

Current holdings: 65 professors, 1,932 pages, 60,749 table rows, 165,779 text
nodes, 1,194,400 flattened Lattes leaves. Roughly 1.2 GB. **This is expensive
acquired provenance. Never delete it to reclaim space.**

### 5.3 Lattes

Extracted from the embedded object, never re-fetched from CNPq. The flattened
`sigaa_public_lattes_flat(siape, path, value_type, value_text)` table is the
substrate for atom extraction (§7).

---

## 6. Canonical identities

| Entity | Identity | Notes |
|---|---|---|
| Professor | SIAPE | Names are aliases, never identities |
| Opportunity / work plan | SIGAA `id_oportunidade` | |
| Project | SIGAA project code (`PVCB5359-2026`) | Falls back to title when absent |
| Lattes | CNPq id | Kept separate from SIAPE |

Authenticated SIGAA only gives a supervisor *name*; the public faculty search
gives the SIAPE. The join goes through `professor_aliases`, keeping its match
score, and is re-run after every sync.

### Semantic unit distinction

A **work plan** is what a student applies to. A **project** is the research. Five
sibling work plans under one project share large copied paragraphs; modelling them
as five independent documents inflates the corpus with near-duplicates and makes
every topic model rediscover the same project repeatedly. So:

- retrieval and application are **work-plan level**;
- topic modelling, the landscape and clustering are **project level**;
- professor intelligence is **atom level** (§7).

---

## 7. The corpus model

### 7.1 Atoms

A professor is a **portfolio of atomic research evidence**, never one concatenated
mega-document. An atom is one project, one article, one work plan, one research
line — the smallest unit that can be *shown to a human as a reason*.

Atom kinds and their specificity prior (`semantics/corpus.py`):

| Kind | Specificity | Source |
|---|---|---|
| `opportunity` | 1.00 | canonical opportunities table |
| `sigaa_project` | 0.92 | public SIGAA *pesquisa* table |
| `lattes_project` | 0.90 | `projetodepesquisa` (title + description + years) |
| `research_line` | 0.80 | `linhadepesquisa` (with active flag) |
| `article` | 0.78 | `artigopublicado` |
| `orientation` | 0.70 | `orientacoesconcluidas` |
| `book_chapter` | 0.68 | books and chapters |
| `conference` | 0.55 | `trabalhoemeventos` |
| `technical` | 0.55 | `trabalhotecnico` |
| `sigaa_extension` | 0.55 | public SIGAA *extensão* table |
| `profile_summary` | 0.45 | Lattes `resumocv` |
| `knowledge_area` | 0.12 | `areasdeatuacao` — true, and useless as an explanation |

Extraction is declarative: `LattesAtomSpec` maps a CNPq path family to an atom
kind, grouping every leaf of one record (title, description, year, keywords) by
the path prefix through the record's array index. Adding a new evidence type is a
table entry, not new parsing code.

Current corpus: **15,019 atoms** from 187 opportunities, 89 projects and 65
professors — roughly 80× the opportunity table. That larger atom corpus is what
makes corpus-native distributional semantics viable at all (§8.3).

### 7.2 Field-aware views

A research record is not one bag of words:

- **DOMAIN** — what is the research about? (titles, CNPq area, introduction, objectives)
- **METHODS** — how is it performed? (plan title, methodology)
- **SKILLS** — what would the student actually do or learn? (acquired skills, methodology)

Field *labels* are never concatenated into the text; that manufactures meaningless
bigrams like `saude medicina`.

### 7.3 Corpus fingerprint

`SemanticCorpus.fingerprint()` hashes opportunities, **atoms** and professor
records. The previous engine hashed only opportunities, so a professor/Lattes
refresh silently left professor scores stale. That is now a detectable condition
(§12).

---

## 8. Semantic architecture

### 8.1 Three roles, kept structurally separate

This is the central design decision. The failed v2 design averaged five different
things into one number; the fix is to stop pretending they are the same kind of
object.

**Representations** induce a geometry. They support query→document retrieval *and*
document↔document structure (maps, clustering, neighbours).
→ word TF-IDF, char TF-IDF, LSA, SPPMI+SVD, Qwen3-Embedding-4B, fusions.

**Retrieval operators** answer query relevance and have no embedding.
→ BM25F. It lives in `retrieval.py` and cannot be used for a map.

**Interpretability models** expose structure for navigation, and are never used as
a similarity metric.
→ NMF topic hierarchy, the skill gazetteer, professor evidence extraction.

`benchmark.py` enforces the distinction: a `RepresentationCandidate` exposes
`doc_vectors`, a `BM25Candidate` returns `None`, and geometry-dependent tasks skip
it rather than faking a vector.

### 8.2 All models are fitted once, on the whole corpus

Every vectorizer and latent model is fitted on the full 15k-document atom corpus
and then used to *transform* arbitrary text. Consequences:

- a query never refits anything, so an ad-hoc search cannot change IDF;
- a candidate's score never changes because a different candidate was filtered out;
- the same fitted space serves opportunities, projects, professors and campaigns.

Artifacts are cached under `data/cache/semantic_spaces/<space_id>/space.joblib`
so ad-hoc search does not pay the fitting cost.

### 8.3 Corpus-native latent semantics: SPPMI + SVD, not canonical LSA

**LSA** factorizes the document–term TF-IDF matrix: `X ≈ U_k Σ_k V_kᵀ`. `V`
diagonalizes the term–term covariance *induced by co-membership in documents*, so
terms used in similar documents collapse onto shared latent directions.

**SPPMI+SVD** asks the sharper question. Instead of "which terms appear in similar
documents", it builds an explicit term–context co-occurrence matrix over a sliding
window, transforms counts with shifted positive PMI

```text
PMI(w,c) = log( P(w,c) / (P(w)·P(c)) )
PPMI     = max(PMI, 0)
SPPMI    = max(PMI − log k, 0)
```

with context-distribution smoothing (α = 0.75, which damps PMI's bias toward rare
contexts and is the single largest practical fix to raw PPMI), and factorizes it.
Levy & Goldberg showed skip-gram-with-negative-sampling implicitly factorizes
shifted PMI, so this is a deterministic, inspectable relative of word2vec: no
sampling, no training schedule, byte-identical output every run.

Document vectors are SIF-weighted averages of term vectors — weight `a/(a+p(w))`,
plus removal of the top common component, which strips the "generic academic
Portuguese" direction that otherwise dominates every document.

**It works.** Nearest neighbours learned purely from the UFAL corpus:

```text
geoprocessamento → espacial, visualizacao, territorial, coinfeccao, dashboards
citometria       → fluxo, citometrico, scatter, citometro, fsc, ssc, hemocitos
datasus          → sinan, sia/sus, ipea, boletins, geografia, oficiais
```

That is exactly the lexical-neighbourhood structure canonical LSA cannot express.

### 8.4 What the benchmark actually showed

Run over the full atom corpus (§9 tasks, MRR):

| task | word | char | bm25 | lsa48 | lsa64 | lsa128 | lsa256 | lsa384 | ppmi100 | ppmi200 | ppmi300 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| title_to_body | **0.930** | 0.920 | 0.923 | 0.459 | 0.507 | 0.654 | 0.786 | 0.830 | 0.736 | 0.787 | 0.801 |
| sibling_plan | 0.926 | **0.928** | 0.928 | 0.437 | 0.501 | 0.682 | 0.859 | 0.918 | 0.890 | 0.912 | 0.914 |
| sibling_deduplicated | 0.917 | **0.936** | 0.903 | 0.432 | 0.471 | 0.637 | 0.834 | 0.915 | 0.901 | 0.916 | 0.928 |
| objectives_to_methodology | **0.865** | 0.858 | 0.846 | 0.459 | 0.520 | 0.633 | 0.729 | 0.774 | 0.678 | 0.726 | 0.734 |
| masked_title | **0.885** | 0.853 | 0.878 | 0.402 | 0.452 | 0.620 | 0.737 | 0.782 | 0.736 | 0.780 | 0.789 |
| cross_project_area | 0.674 | 0.652 | 0.685 | 0.650 | 0.688 | **0.721** | 0.717 | 0.695 | 0.698 | 0.705 | 0.695 |
| professor_holdout | 0.702 | **0.777** | 0.717 | 0.478 | 0.490 | 0.552 | 0.611 | 0.634 | 0.700 | 0.739 | 0.752 |

Four conclusions, each of which changed the design:

1. **LSA at k=48–64 is not "a latent view", it is broken.** The previous engine ran
   at exactly those dimensionalities. Fitted on the real corpus, k=48 scores 0.44
   MRR on sibling retrieval where lexical scores 0.93. The earlier belief that
   "LSA is stronger at sibling retrieval" was an artifact of eigendecomposing a
   187-document matrix; on a 15k-document matrix, low-k LSA throws away exactly
   the specific vocabulary that identifies a project. **Never ship k < 256.**
2. **SPPMI beats LSA at every matched budget and is far cheaper.** ppmi300 fits in
   ~24 s and matches or beats lsa384 (~52 s) on five of seven tasks, decisively on
   `professor_holdout` (0.752 vs 0.634) and `sibling_deduplicated` (0.928 vs
   0.915). SPPMI is now the default `latent_model`.
3. **Lexical channels genuinely win literal tasks and genuinely lose broad ones.**
   Word TF-IDF leads `title_to_body`, `masked_title` and `objectives_to_methodology`;
   every lexical channel loses `cross_project_area` to LSA. This is not noise, it
   is the reason channels are exposed separately.
4. **Deduplication mattered less than assumed.** Suppressing verbatim shared
   sentences moved sibling MRR by <0.01 for lexical channels. Sibling plans
   paraphrase more than they copy, so the earlier claim that lexical sibling
   performance was mostly copy-paste is **not supported**. It is kept as a task
   because it is the honest version of the question.

### 8.5 Rank fusion is used sparingly, and never on correlated channels

RRF only makes sense on *decorrelated* rankings. Fusing word TF-IDF, char TF-IDF
and BM25F triple-counts one lexical signal and drowns out a genuinely independent
latent one — precisely the v2 defect. `rrf()` still exists in `retrieval.py` for
places where rank-space combination is right, but the production path does not
pre-fuse lexical channels.

### 8.6 Multi-view integration

Two mechanisms are implemented and benchmarked against their own best single view.

**Block concatenation** (default). Given L2-normalized views,

```text
z = [ √(α/Σ)·LATENT | √(β/Σ)·NEURAL | √(γ/Σ)·LEXICAL ]
```

Because each block is unit-norm, `cos(z_a, z_b)` is *exactly* the
weight-normalized average of the per-view cosines. That makes it a genuine joint
metric space with a one-line explanation, not an arbitrary score blend. This
property is pinned by a test.

It also makes the fusion computable without ever forming the concatenation:
`BlockFusion.similarity()` scores each view separately and averages the cosine
matrices. That is not an optimization detail — a lexical view has ~10⁵ columns,
so densifying the joint matrix for a 15k-document corpus is not representable at
all. Scoring therefore always goes through `similarity()`; `encode()` materializes
joint vectors only for geometry over a bounded entity set (89 projects, 187
opportunities), and `stack()` keeps the matrix sparse for `JointSVDFusion`, whose
truncated SVD accepts sparse input natively.

**Joint SVD** (`fusion = "joint_svd"`). Concatenate the weighted views and
re-factorize: `X_joint ≈ U_r Σ_r V_rᵀ`. Directions on which views agree accumulate
variance and survive truncation. The cost is that view-*specific* signal is
discarded too, which is why it is benchmarked rather than assumed better.

**The measured verdict.** Fusion earns its complexity, but not everywhere and not
by much. Against the best single view:

- `cross_project_area` NDCG@10: fusion 0.546 vs best single 0.508 (lsa384) and
  0.476 (Qwen alone) — a clear, task-relevant gain.
- `professor_holdout` NDCG@10: fusion 0.648 vs 0.633 (Qwen alone) — a small gain.
- Literal tasks: fusion recovers most of what Qwen alone destroys
  (`masked_title` 0.636 → 0.83) without ever beating plain word TF-IDF (0.885),
  which is why that channel stays separately addressable.

A weight sweep over qwen ∈ {1, 1.5, 2, 3} × word ∈ {0.5, 0.75} produced a
**plateau**: every configuration landed within ~0.01 on every task, and mean rank
across the battery separated the top four by 0.7 places. The chosen default
(neural 2.0 / latent 1.0 / lexical_word 0.75) is the best-ranked *three-view*
configuration, not a sharp optimum, and the config says so. Adding a fourth view
(LSA or char) moved one task by +0.01 and another by −0.01: refused under the
complexity rule.

`joint_svd` was benchmarked at 128 and 256 components. It marginally wins the
sibling tasks (0.9445 vs 0.9426 MRR) and clearly loses the graded ones
(professor_holdout NDCG 0.607 vs 0.648), which is the trade its docstring
predicts: consensus truncation discards view-specific signal. Block fusion stays
the default.

Not implemented, deliberately: CCA/GCCA and JIVE/AJIVE-style joint+individual
decompositions (§23). They are the right next question if block fusion plateaus,
but with 89 projects the sample size does not support learning cross-view
projections without overfitting.

### 8.7 The neural view

Qwen3-Embedding-4B, served locally by LM Studio over the OpenAI-compatible
`/v1/embeddings` endpoint, 2560 dimensions, Matryoshka-truncatable. Behind
`semantics/embedding.py`, which provides health check, loaded-model verification
and alias resolution, model identity for provenance, batching, timeout/retry,
deterministic preprocessing, a content-addressed DuckDB cache keyed by
`(provider identity, sha256 of normalized text)`, normalization, and dimension-
drift detection.

Measured throughput on the 6 GB RTX 4060: ~7 texts/s, so indexing the full 15.5k
atom corpus is a ~36-minute one-time cost, permanently cached. Nothing else in the
codebase knows LM Studio exists.

Neural embeddings are an *additional view*, not a replacement. MTEB leadership is
not evidence about Portuguese biomedical UFAL work plans; §8.4's battery is. Run
against it, Qwen's profile is exactly the complement of the lexical channels
(MRR, and NDCG@10 for the two graded tasks):

| task | word | char | ppmi300 | lsa384 | qwen2560 | qwen1024 | qwen512 |
|---|---|---|---|---|---|---|---|
| title_to_body | **0.930** | 0.920 | 0.801 | 0.830 | 0.780 | 0.766 | 0.740 |
| masked_title | **0.885** | 0.853 | 0.789 | 0.782 | 0.638 | 0.636 | 0.600 |
| objectives_to_methodology | **0.865** | 0.858 | 0.734 | 0.774 | 0.816 | 0.809 | 0.790 |
| sibling_deduplicated | 0.917 | **0.936** | 0.928 | 0.915 | 0.855 | 0.831 | 0.809 |
| cross_project_area (NDCG) | 0.476 | 0.425 | 0.495 | **0.508** | 0.486 | 0.476 | 0.461 |
| professor_holdout (NDCG) | 0.506 | 0.546 | 0.557 | 0.460 | 0.631 | **0.633** | 0.627 |

Read across the rows: Qwen is the **worst** channel at literal recall — it loses
`masked_title` to word TF-IDF by 0.25 MRR — and the **best** at portfolio
coherence, where it beats every corpus-native channel by ~0.08 NDCG. That is the
textbook signature of a genuinely complementary view, and it is the empirical
justification for fusing rather than choosing.

**Matryoshka truncation is free.** 2560 → 1024 dims changes nothing measurable
(professor_holdout NDCG 0.631 → 0.633; masked_title 0.638 → 0.636), so the neural
block runs at 1024 dims: 2.5× less memory in every fusion and map. 512 is
measurably worse and is not used.

### 8.8 Channels exposed to the product

`lexical_word`, `lexical_char`, `bm25f`, `latent`, `neural`, `fused`,
`skill_match` — stored one row per `(entity, facet, channel)` in `entity_scores`,
with both raw score and within-corpus percentile. A task picks the right evidence:

- *"Find explicit DATASUS/SIH projects"* → `bm25f` / `lexical_word`.
- *"Find research adjacent to quantitative population-health inference"* → `latent` / `neural`.
- *"Find work I could actually do"* → `skill_match`.

An `overall` facet exists for default sorting. It is a **rank-space** blend of the
domain/methods/skills fused channels using `[semantics.overall_weights]` —
averaging percentiles, not cosines, because cosines from different facets are not
on a common scale. It is labelled a ranking everywhere it appears.

---

## 9. The semantic regression battery

`semantics/benchmark.py`. Deterministic, self-supervised, built from PULSAR's own
corpus. Every candidate runs the same tasks; results are persisted per space.

| Task | Query → documents | Measures |
|---|---|---|
| `title_to_body` | plan title → work-plan bodies | literal recall floor |
| `sibling_plan` | plan title → bodies, target = sibling plan | thematic affinity (copy-inflated) |
| `sibling_deduplicated` | same, after verbatim shared sentences are deleted | honest affinity |
| `objectives_to_methodology` | objectives → methodologies | cross-view coherence |
| `masked_title` | title minus its two rarest tokens → bodies | penalizes identifier memorization |
| `cross_project_area` | project → projects sharing the CNPq area | broad thematic (graded, NDCG@10) |
| `professor_holdout` | one held-out portfolio atom → all atoms | portfolio coherence (graded) |

Metrics: MRR, Recall@1/5/10, NDCG@10 where graded. Geometry diagnostics
(`map_fidelity`) report Kruskal stress plus Pearson and Spearman distance
correlation.

Design note: the tasks are chosen so that **different tasks disagree**. A model
that wins everything would mean the tasks are measuring one thing, which would
make the battery useless for deciding anything.

Two v2 defects are fixed: the harness no longer hardcodes its own feature and
dimension settings independent of production config, and *all* tasks are persisted
rather than only two.

---

## 10. Topics

NMF over project-level documents, per facet (`domain`, `methods`). A row of `H` is
a non-negative basis vector over terms; calling it a "topic" is a human reading.
It is a navigation aid, never an ontology and never a similarity metric.

**Model selection** (`topics.select_k`) weights NPMI coherence (0.35), stability
under deterministic perturbation (0.25), independent project support (0.20) and
exclusivity (0.20) — then applies a parsimony rule: the smallest k within ε of the
best composite score. **Reconstruction error is deliberately excluded**: it falls
monotonically in k, so any score rewarding it is biased toward more topics. That
bias was the v2 defect.

**Stability** refits on deterministic 85% subsamples and measures mean best-match
cosine between factor bases. Persisted next to the model, because "we found 14
topics, 5 of which are noise" is a different statement from "we found 14 topics".

**Hierarchy.** Research structure is not 14 competing flat labels; it is broad
fields that subdivide. Root factors with ≥10 member projects are split into 2–4
sub-factors, and a split is kept only if its NPMI is non-negative. Depth 2.

**Labels** use a FREX-style score, frequency × √exclusivity, so a factor is named
by terms that are both prominent *and* distinctive.

**The skills NMF is gone** (§11).

---

## 11. Skills: a set, not a partition

The v2 skills NMF failed structurally, not through bad tuning: 74 of 89 projects
collapsed onto one factor of *leitura crítica / comunicação / escrita científica*.
Every work plan promises those, so they carry no discriminative information — and
a soft partition, which forces factors to compete for each document's mass, is the
wrong *shape* for capabilities. A project can involve Python *and* PCR *and*
systematic review with no competition between them.

`semantics/skills.py` replaces it with a curated multi-label gazetteer: ~50 skills
across `computing`, `statistics`, `data_source`, `wet_lab`, `clinical` and
`scholarly`, each with regex patterns and a `generic` flag. Generic scholarly
competencies are *recorded but never scored* — they explain nothing.

Two details that matter in practice:

- Single-letter identifiers need case. A lone capital **R** is the language; the
  folded text cannot see that, so `Skill.raw_patterns` matches the original
  string, with an explicit exclusion list (`vitamina R`, `R$`, …).
- `discover_candidate_skills()` surfaces salient technical terms the gazetteer
  does not yet cover — terms in several documents but not most — so the taxonomy
  grows from the real corpus. Run it after each sync:
  `pulsar semantics skills --discover`.

`skill_match` is the fraction of the operator's declared `skill_ids` present in a
record, ignoring generic skills. It is a channel, not a mystery.

---

## 12. Provenance: two identities

The v2 engine hashed corpus + config + *the operator's profile queries* into one
`model_id`. Editing a personal interest therefore invalidated the topic model and
the landscape — wrong, because Levi's interests do not change what UFAL researches.

```text
semantic_space_id = hash( corpus fingerprint
                        + preprocessing
                        + model architecture
                        + embedding model identity
                        + library versions )

profile_run_id    = hash( semantic_space_id + operator profile / query config )
```

Topics, geometry, skills and benchmarks belong to a **space**. Rankings and
evidence belong to a **run**. Editing `profile.yaml` creates a new run and leaves
the space untouched; refreshing the professor corpus creates a new space.

**Staleness.** `check_staleness()` compares the live corpus fingerprint against the
stored one. `pulsar doctor` and the console header show it, and campaign
creation *refuses* on a stale space unless `--allow-stale` is passed. PULSAR must
never silently combine fresh entities with stale semantic scores.

Library versions are part of the space identity because a scikit-learn upgrade can
move every coordinate.

---

## 13. The entity graph

Everything before this section describes a table of people with scores attached.
That is enough to rank, and it is what PULSAR was for a while. It cannot answer
the questions that actually decide what to do:

- Who sits between two groups that otherwise do not talk?
- Which clusters does this faculty decompose into, as opposed to the units it is
  administratively divided into?
- Whose collaboration reaches outside the institution, and whose does not?
- How would I get an introduction to this person?

None of those is a property of a row. They are properties of a structure, so
there is now a structure: one id space, one relation vocabulary, one projection.

### 13.1 The id space

`graph/model.py` declares seven kinds and ten relations, and nothing else is
allowed to exist. An entity id is `kind:key` — `person:1157495`, `org:icbs`,
`position:12345` — and the key is accent-folded, so `Nóbrega` and `Nobrega` are
one node rather than two.

| kind | what it is |
| --- | --- |
| `person` | a researcher: indexed faculty, or an external co-author |
| `org` | a centre, unit or department |
| `work` | an article, chapter, conference paper or technical output |
| `project` | a research or extension project |
| `position` | an offered supervision slot — a work plan |
| `topic` | a factor of the semantic space |
| `skill` | an extracted technique |

Faculty are identified by SIAPE. External co-authors have no registry number
anywhere reachable, so their identity is their name: `person:name-<slug>`. That
is genuinely weak — two people publishing under one name become one node, and
one person publishing under two spellings becomes two — and `is_indexed_person()`
exists so that no measure quietly treats the two classes as equally reliable.

`PersonResolver` closes the half of that which is closable. Lattes records a
project team member however the person filling the form wrote it, so an indexed
professor appears on other people's teams under a shortened name. It tries the
exact normalized name, then the alias table acquisition already built, then —
only if exactly one professor matches — first name, last name, and every token
in between appearing in order inside one indexed name. Reordered names and bare
surnames are refused outright.

On the live store that recovered fifteen nodes, with no ambiguous cases: fifteen
sets of collaboration edges that had been attributed to a ghost rather than to
the professor who earned them. That is not one missing edge each. It moved every
degree, centrality and bridging value those professors and their neighbours had,
and dropped the community count from 40 to 35. Every inference is counted and
reported by `graph build` and `graph status`, because a build that starts
inferring hundreds of merges is a build to look at.

### 13.2 The vocabulary

`affiliated_with`, `part_of`, `authored`, `supervised`, `leads`, `offers`,
`within`, `collaborates_with`, `about`, `uses`. Each declares which kinds it may
join, and `Edge.validate()` raises rather than storing a relation that does not
typecheck — a graph that accepts `work → authored → person` is a graph whose
answers cannot be trusted in either direction.

`collaborates_with` is the only symmetric relation. It is stored once, in
canonical id order, and expanded on read; storing both directions would make
every degree twice what it is.

Weight means whatever the relation means — co-authored papers, mentions, topic
share — and is **never comparable across relations**. Two facts implying the same
relation sum their weight rather than overwriting, because a second paper with
the same person is a stronger tie, not a duplicate row.

### 13.3 The projection is a replacement, not an accumulation

`graph/project.py` reads acquired facts and emits entities and edges; `store.py`
writes them. The write deletes first. An opportunity that closed, a co-author
whose name was corrected upstream, a department that was renamed must all
*disappear*, and an incremental upsert cannot express that. The graph is one pass
over facts already in the store, so replacement is affordable and honest.

The projection is deterministic: two builds of an unchanged store produce
identical entity and edge sets.

### 13.4 Structural measures

`intelligence/network.py`, measured over **co-authorship alone**. Shared
technique and shared subject are derived similarities, not observed relations,
and averaging an observation with an inference produces a number that means
neither.

| measure | what it answers |
| --- | --- |
| `degree` / `weighted_degree` | how many partners; how much accumulated tie strength |
| `betweenness` | who lies on the paths between others |
| `community` | which cluster this vertex keeps agreeing with |
| `bridging` | what share of a vertex's tie strength leaves its own community |
| `external_reach` | what share leaves the indexed faculty entirely |

Betweenness is Brandes over a deterministic sample of source vertices — the
highest-degree ones, ties broken by id, not a random draw — and normalized to a
share in [0, 1]. Exact betweenness is O(VE); on a few thousand people that is
minutes, and the ranking is stable long before the values converge. Below the
pivot budget the computation is exact.

Communities are weighted label propagation rather than modularity maximisation,
because label propagation makes no claim to have found an optimum. Modularity
optima on a graph this sparse move with the resolution parameter, and quoting one
as *the* community structure would be a stronger claim than the data licenses.

Everything is deterministic. A centrality that changes between two runs on
unchanged data is not a measurement; an operator who sees a rank move has to be
able to conclude that the data moved.

**Every normalized share needs a degree floor to be readable.** A co-author who
appears once, on one paper, with someone outside their cluster has a bridging of
1.0 and means nothing by it. `graph top` therefore defaults to `--min-degree 3`
and prints the degree beside the value, so a reader can see what the answer was
computed over.

---

## 14. The build pipeline

The ordering used to live in one CLI function: `sync all` ran opportunities, then
exported a seed CSV, then the public scrape, then resolution, then applications,
then semantics — six steps whose dependencies existed only in the sequence
someone had typed. Nothing could answer "is the graph stale", and nothing could
re-run *just* the part that had gone out of date, so the honest options were to
re-run everything or to guess.

`pipeline/registry.py` declares each stage with what it needs, what it produces,
and how to tell whether its output still reflects its inputs. The runner does the
ordering.

```text
acquire.opportunities ──┬── acquire.professors ──┬── intelligence.metrics ──┐
                        │                        │                          ├── graph.project ── graph.metrics
                        │                        └── semantics.space ───────┘
                        │                                   └── semantics.profile
                        └── acquire.applications
```

Freshness is derived from the store, never from a timestamp file. A stage is
stale when what it wrote disagrees with what it read — which survives someone
editing the database by hand, moving the project, or restoring a backup.

Five states, and the fourth is the one the module exists for:

| state | meaning |
| --- | --- |
| `ok` | current against its inputs |
| `stale` | its inputs have moved since it ran |
| `never` | it has not run |
| `blocked` | fresh *on its own terms*, but sitting below something that is not |
| `unknown` | the probe could not decide — a broken probe must not hide the pipeline |

`blocked` is the state operators misread. The stage itself is fine; re-running it
alone would still produce a confident answer from stale inputs, which is worse
than a missing one because it looks like an answer.

`unknown` covers the case where the data is present but the run that fetched it
was not journalled — a store bootstrapped from a ledger, or restored from a
backup taken before the journal existed. Calling that "never acquired" would
cascade every derived stage into `blocked` on a store that is in fact complete.

Two rules the runner enforces, both because the alternative has already gone
wrong somewhere in this project's history:

- **A scrape is not a recomputation.** Acquisition stages reach the network, take
  minutes, and are rate-limited by somebody else's server. They never run because
  something downstream of them went stale; they run because `--acquire` asked for
  them.
- **Nothing runs on stale inputs.** A stage whose dependency failed is skipped,
  not attempted.

Every stage journals into `sync_runs` — the same table acquisition already wrote
to — so freshness, history and failure have one home rather than one per
subsystem.

---

## 15. Database schema (v4)

**Acquired:** `meta`, `professors`, `professor_aliases`, `projects`,
`opportunities`, `applications`, `sync_runs`, `sigaa_public_*`.

**Derived:** `semantic_spaces`, `profile_runs`, `entity_scores` (tall:
run/entity/facet/channel/score/percentile), `entity_geometry`, `semantic_topics`
(with `parent_id`/`depth`), `entity_topics`, `entity_skills`, `professor_evidence`
(with `scope`), `semantic_benchmarks`, `map_diagnostics`, `professor_metrics`,
`collaboration_edges`, `global_metrics`, `embedding_cache`, and the graph:
`graph_entities`, `graph_edges`, `entity_metrics` (tall:
entity/metric/value/extra), `graph_builds`.

The graph is derived by construction — it is a projection of acquired facts, not
a source of new ones — which puts it in the auto-reconciling block below and
makes adding a kind, a relation or a measure a zero-migration change. A measure
is a row, exactly as a channel is a row in `entity_scores`.

**Outreach:** `campaigns` (with `provenance_json`, `theme`),
`campaign_recipients` (with `rationale_json`), `campaign_messages` (with
`body_text` + `body_html`).

`Database.initialize()` performs a forward-only migration in three passes:

1. drop the superseded `analysis_*` and `semantic_runs`/`semantic_entity_*` tables;
2. **reconcile derived-table shapes** — compare each derived table's live columns
   against the declared DDL and drop any that diverge, because
   `CREATE TABLE IF NOT EXISTS` silently leaves a table created by an older
   engine with different columns, and the failure then surfaces much later as a
   binder error inside a console query;
3. rebuild `professor_metrics` and widen the outreach tables in place.

Acquired and outreach tables are never dropped — only derived ones, which are
disposable by §3.2. The expected column set is parsed from the DDL constants
themselves, so the schema text stays the single source of truth.

The tall `entity_scores` shape replaced a 23-column wide table. It is what makes
adding a channel a zero-migration change.

---

## 16. CLI

Grouped by subsystem: `sync`, `semantics`, `graph`, `pipeline`, `opportunities`,
`professors`, `campaign`, `applications`, plus `init`, `doctor`, `dashboard`,
`import-ledger`.

`pipeline status` is the one to reach for when something looks wrong upstream: it
prints every declared stage, its state, and why — `--why` adds what each stage is
for, which matters after a month away. `pipeline explain <stage>` gives that one
stage's inputs and its blast radius; `pipeline run --dry-run` prints exactly what
a real invocation would do.

The `graph` group is the structural end: `build` and `measure` recompute,
`status` reports size and composition, `find` resolves a name to an entity id,
`show` prints one entity with its measures and neighbourhood, `top` ranks by a
measure, and `path` finds the strongest short chain between two people. Every
command that takes an entity accepts a SIAPE or a name fragment, and refuses
rather than guessing when a fragment is ambiguous.

Exactly two commands change the outside world, and both require `--confirm`:
`applications apply` and `campaign send`. `campaign send` without it is a dry run
that prints the recipient list.

`pulsar doctor` is the first thing to run when something looks wrong: it reports
config, credentials, corpus counts, active space and run, **freshness**, and the
embedding service health in one table.

---

## 17. Console

A React single-page application served from the standard library.
`webapp/server.py` is `http.server` and answers JSON out of
`dashboard/queries.py`, where all the SQL still lives; `webapp/frontend/` is a
Vite + React + TypeScript project whose build output *is* `webapp/static/`, so
the server needs no knowledge that a build step exists — it finds an
`index.html` and a hashed asset bundle exactly where it used to find
hand-written modules. `npm run dev` serves the app with hot reload on port 5173
and proxies `/api` to the console, so the two halves are developed
independently. `npm run build` is what the wheel ships.

The front end is layered rather than screen-by-screen, because the recurring
failure of the version before it was that a good pattern built inside one screen
never reached the other six:

- `api/` — payload types and the React Query hooks. A view asks for
  `useProfessor(siape)`, never for a URL, so a payload that moves cannot leave a
  stale path buried three files deep.
- `components/` — the shared vocabulary: table, meter, chip, tabs, the four SVG
  charts, and the three **records** (a supervisor, a work plan, a message). A
  supervisor is the same record whether reached from a ranking, the map, or a
  co-authorship edge.
- `components/Rail.tsx` — the stack that opens a record *over* the screen you
  are on. Clicking something must not route you somewhere else and discard the
  filters, the scroll position, the map's camera and the layout the graph spent
  three seconds converging into. The few links that genuinely do leave are drawn
  differently and say so.
- `views/` — one screen each, plus `views/network/` for the simulation.

It replaced a Streamlit app. Streamlit was the heaviest entry in the dependency
tree, it was not actually installed in the environment that ships the tool, and
its execution model — re-run the whole script on every widget change — is the
wrong shape for an interface whose job is to filter 187 rows and read one of
them. Choosing a work plan from a dropdown in order to "inspect" it is a
workaround for a framework, not a design. The corpus is small enough that the
browser holds all of it, which is what makes filtering feel immediate, and every
screen keeps its state in the URL, so a view is linkable and survives a reload.

- **Overview** — the two live questions only: which conversations are still
  open, and which funded plan nobody has written to deserves the next move.
  Everything else it used to show is the Landscape screen's job.
- **Work plans** — filters including *requires skill*, ranked table, and a
  side-by-side record with a per-channel ranking breakdown, topics and skills.
- **Professors** — ranked by *current supervision capacity* or *research
  trajectory*, with an evidence panel showing each atom's kind, year, match,
  specificity and recency.
- **Landscape** — the map, coloured by whichever question is being asked (field,
  fit, funding, campaign reach), with its stress and rank correlations stated
  inline, and a rail whose field and technique bars double as the filters that
  isolate them.
- **Network** — the faculty as a graph, over three switchable edge semantics
  (co-authorship, shared technique, shared subject) that disagree in useful ways.
  Detail in §17.1.
- **Outreach** — audiences, per-recipient drafts, and the state of every
  conversation. It never sends: that stays at a terminal, behind `--confirm`.
- **Semantic engine** — benchmarks (with a plain-language note per task), map
  fidelity, topic-quality curves over k, and both provenance identities.

`pulsar dashboard --window` draws the same pages in the OS webview instead of a
browser tab (`webapp/desktop.py`, optional `desktop` extra). Nothing in the front
end changes; the server moves to a background thread so the GUI toolkit can own
the main one. The browser remains the default because URL-as-state is only
useful while there is an address bar to copy from, and the window's View menu
hands the current URL back to the browser for exactly that.

The analytical density of the old static `study_landscape.py` is retained; its
incorrect analytics are not.

### 17.1 The network view

Every other screen ranks, and ranking answers *who*. It cannot answer *shape*:
whether a strong match sits alone or inside a group three of whose members have
already been written to; whether a technique lives in one unit or crosses both;
who is adjacent to a thread that has gone quiet. Those decide a second wave.

- **The page opens on an answer, not on a picture.** Four findings sit above
  the graph — reachable through a live thread, funded and unwritten, spanning
  both units, unconnected here — and each one is also the filter that shows who
  it counted, listed in the rail beside it. A number nobody can act on is
  decoration.
- **The controls ask questions, not encodings.** Four tabs — where I've
  reached, who works like me, who studies like me, who works together — each
  setting the graph, colour, size and edge meaning together, because those are
  not four independent choices. The raw encodings stay behind a Display
  disclosure for anyone who wants to take them apart.
- **Edges justify themselves.** Thickness carries tie strength; colour is held
  back for the one thing position cannot show, which ties bridge from someone
  written to toward someone not. And every tie names the terms it was computed
  from, so a line between two people can be read rather than trusted — the same
  rule the rankings follow.
- **Units are territory.** Full-height bands, width proportional to headcount,
  with a soft one-sided containment force: the layout leans into the partition
  instead of being clamped inside it, so a supervisor whose collaborators are
  nearly all on the other side visibly drifts toward the seam. Each unit can be
  toggled off, and the rest take its share of the frame. Boxes were tried first
  and were wrong twice over — a gutter turned every cross-unit tie into a long
  diagonal, and hard walls left a ridge of nodes pressed flat against them.
- **The layout is live.** Velocity Verlet with a decaying activity level, not a
  batch algorithm that computes once and freezes. Any interaction puts energy
  back in and it re-converges from where it now is: dragging a node drags its
  neighbourhood elastically, and the spread / edge-length / grouping sliders
  reorganise the drawing while they are still being moved. A dragged node stays
  pinned until double-clicked or released with *Unpin*.
- **Going deeper is not going elsewhere.** The rail is a stack: a supervisor's
  record, the plan they are offering, the message already sent to them — each
  opens over the graph with one way back, and the graph never moves. Routing to
  another screen for a glance would discard the layout, the camera, every
  pinned node and the active lens, which is a violent price for curiosity. The
  three links that genuinely leave are drawn differently and say so (`↗`).
  Switching question re-fetches the ties and hands them to the *running*
  simulation, so the field relaxes from where it stands into the new
  relationships; which supervisors move and which hold still is itself a
  finding, and rebuilding from scratch would destroy it.
- **Selection cards the neighbourhood.** Each neighbour states its name, funded
  slots and two words of subject matter in place. Subject is the one thing
  position, size and colour cannot encode, and it is usually what decides where
  to look next. Everything else lives in the record panel beside the graph —
  plans, techniques, ranked evidence, and both halves of the collaboration.

The three graphs are built in `webapp/network.py`. `entity_topics` holds no
professor rows — the topic model is fitted over documents, not over people — so
subject adjacency is derived from the plans each supervisor is offering.
Similarity graphs are pruned per node rather than by a threshold alone: a plain
cutoff either leaves a hairball or strands half the corpus.

---

## 18. Campaigns and email

A campaign is a **snapshot**. Creation persists the audience query, the recipient
set, the exact qualifying opportunities, a `rationale` (primary opportunity,
funded slots, percentiles, matched skills, top evidence), the semantic provenance,
and one independently editable draft per recipient.

Rendering: campaign data → Jinja2 → plaintext → HTML theme → MIME
`multipart/alternative`. The theme is inline-CSS, table-based, no JavaScript, no
external assets — email clients are not browsers. Every HTML message ships a real
plaintext alternative that says the same thing.

`SMTPProvider` implements a `MailProvider` protocol; adding a Gmail API transport
is a new class, not a rewrite.

---

## 19. Safety boundaries (do not regress these)

1. Discovery cannot apply. There is no call path from `sync_opportunities` to
   `apply_one`.
2. `applications apply` handles one id, prints it, and requires `--confirm`.
3. Campaign creation opens no SMTP connection.
4. Sending reads persisted drafts; templates are never re-rendered at send time.
5. Only `selected` recipients are sent; `sent` messages are never re-sent.
6. `campaign send` without `--confirm` is a dry run.
7. Campaign creation refuses on stale semantics unless explicitly overridden.
8. Secrets are environment variables named in config, never values in config.
9. The fragile SIGAA literals are pinned by a contract test.

---

## 20. Operations

```powershell
conda activate pegasus
pulsar doctor                     # always start here
pulsar sync all                   # ~depends on SIGAA; the public crawl is the slow part
pulsar semantics build            # ~3 min with a warm embedding cache
pulsar semantics profile          # seconds; after editing config/profile.yaml
pulsar dashboard
```

**Data layout.** `data/pulsar.duckdb` (canonical), `data/public_sigaa/` (raw
provenance — expensive, back it up, never delete), `data/state/` (sync journal +
seed CSV), `data/cache/semantic_spaces/` (fitted artifacts, regenerable, pruned to
the 3 most recent), `data/exports/` (campaign exports and previews). All of
`data/` is git-ignored.

**Embedding server.** LM Studio must be serving on `http://127.0.0.1:1234/v1` with
`text-embedding-qwen3-embedding-4b` loaded. If it is down, `build` logs the reason
and continues without the neural channel rather than failing.

**DuckDB locking.** One process holds the write lock. Do not run `semantics build`
while the dashboard is open on the same database.

---

## 21. Implementation status

| Area | Status |
|---|---|
| Repository audit and cleanup | **done** — patch backups, egg-info, generated bundles and stale docs removed; state files moved out of the repo root |
| Package restructure | **done** — `acquisition` / `semantics` / `intelligence` / `outreach` / `dashboard` |
| DuckDB schema v3 + migration | **done** |
| Atom corpus (15k items) | **done** |
| Representation layer + fusion | **done** |
| LM Studio embedding provider + cache | **done** |
| Semantic regression battery (7 tasks) | **done** |
| Provenance split + staleness enforcement | **done** |
| Deterministic skill extraction | **done** — replaces the failed skills NMF |
| Hierarchical topics + stability | **done** |
| PCoA→SMACOF landscape + fidelity | **done** |
| Professor current/trajectory evidence | **done** |
| CLI rebuild | **done** |
| Dashboard rebuild | **done** |
| Campaign snapshot + HTML rendering + dry-run send | **done** |
| Test suite | **done** — 42 tests |
| Acquisition journal (`sync_runs`) | **done** — every sync entry point records start, finish, status and details, including failures; `doctor` shows the latest per source |
| Delivery path | **done** — verified against an in-process SMTP sink |
| End-to-end verification | **done** — `init` migration, `semantics build` (space + topics + landscape + skills + 7-task battery + profile run + metrics), `doctor`, ranked opportunity/professor views, audience → campaign → preview → dry-run send, all 21 dashboard queries, Streamlit serving |
| Live SIGAA regression against the portal | **not re-run this session** (§22) |
| SMTP end-to-end send | **not exercised** — no SMTP host configured; the dry run is verified |

---

## 22. Known problems register

1. **Live SIGAA paths are untested this session.** The automation was restructured
   (module moved, credential lookup now config-driven) but not run against the
   live portal. The contract test pins the literals; it does not prove the flow.
   **Next operator action: run `pulsar sync applications` first** — it is the
   read-only path that exercises login, notice bypass and menu navigation.
2. **SMTP is unconfigured on this machine**, so no message has gone to a real
   address. The delivery path itself is covered end to end by `tests/test_mailer.py`,
   which runs a real SMTP session against an in-process sink and asserts the MIME
   structure, the selected-only rule, the no-resend rule and failure recording.
3. **`cross_project_area` uses a weak label.** CNPq area strings are coarse and
   inconsistently filled; treat that task's absolute numbers as indicative.
4. **Map fidelity is genuinely weak and shown as such.** SMACOF improves on PCoA
   (stress 0.414 → 0.378), but 0.378 stress with Pearson 0.59 / Spearman 0.60
   means the 2-D plot preserves neighbourhoods, not distances. Every view that
   renders it prints those numbers. Improving this needs a better *geometry
   source*, not a better projection — the fused space is ~41k dimensions and the
   projection is doing the only thing it can.
5. **Professor `methods`/`skills` facets only exist for professors who currently
   offer work plans.** Lattes gives no methodology text. This is a data limit, not
   a bug, but the UI should say so more loudly than it does.
6. **No outcome tracking.** History records what was sent, not what came back
   (reply, meeting, acceptance). §23.
7. **Single-letter skill matching is heuristic.** The capital-`R` rule is right
   nearly always; the exclusion list is empirical and will need extending.
8. **`joint_svd` fusion is implemented and benchmarked but not the default.** See
   §24 for the decision.
9. **Root topic count is 3 per facet**, at the floor of the configured range. The
    hierarchy that results is genuinely interpretable (public health / EB wounds /
    cellular effects, each splitting into four), but the selection score should be
    inspected across a wider `topic_max_k` before trusting 3 as the answer rather
    than the boundary.
10. **`current` and `trajectory` still correlate at Spearman 0.82.** That appears
    substantive — professors whose careers align also tend to run aligned projects
    — but it means the two scopes are most useful for their *evidence*, which now
    overlaps only 34%, rather than for producing different shortlists.

---

## 23. Future work

**Near term**
- Outcome tracking: reply / meeting / accepted / declined per campaign recipient,
  and a retrospective view of which evidence actually predicted a reply.
- Extend the skill gazetteer from `--discover` output after each sync.
- A "why not" view: why a plausible-looking opportunity ranked low.

**Medium term**
- Bibliographic coupling and co-authorship networks from the Lattes author lists
  (already extracted, currently only counted).
- Temporal view: which fields at UFAL are growing or shrinking, from atom years.
- Gap analysis: methods the operator has that the local ecosystem underuses.

**Research directions (benchmark before adopting)**
- CCA/GCCA-style shared latent spaces across views. Needs more paired entities
  than 89 projects to avoid overfitting.
- JIVE/AJIVE joint+individual decomposition, to preserve corpus-specific structure
  (local UFAL terminology) *and* pretrained-model structure (medical synonymy)
  instead of collapsing to consensus. This is the most promising unexplored idea.
- Learned per-task channel weighting from actual operator feedback, once outcome
  data exists.
- Instruction-conditioned neural embeddings per facet (Qwen supports task
  instructions); currently unused.

**Explicitly not planned**
- A vector database or ANN index. 15k vectors × 2560 dims is ~150 MB; exact cosine
  is milliseconds. ANN must be justified by scale, not fashion.

---

## 24. Decision log

**D1 — SPPMI+SVD replaces LSA as the default latent representation.**
Evidence §8.4. ppmi300 matches or beats lsa384 on five of seven tasks at half the
fitting cost, and decisively on portfolio coherence. LSA is retained behind
`latent_model = "lsa"` for comparison.

**D2 — LSA dimensionality floor raised from 48/64 to ≥256.**
The old values score ~0.44 MRR where lexical scores ~0.93. This was the single
largest defect in the v2 engine and it was invisible because the benchmark fitted
LSA on a different (187-document) matrix than production used.

**D3 — Channels are exposed separately; no universal `semantic_fit`.**
Different tasks have different winners by construction. Collapsing them destroys
the information the operator needs. An `overall` facet exists for default sorting
and is explicitly a rank blend.

**D4 — Lexical channels are not pre-fused by RRF.**
Word TF-IDF, char TF-IDF and BM25F are correlated; fusing them triple-counts one
signal and suppresses the independent latent one.

**D5 — Block concatenation is the default fusion.**
`cos` of concatenated unit-norm blocks is exactly the weighted mean of per-view
cosines: a real joint metric space with a one-line explanation. Joint SVD is
implemented and benchmarked but not default, because consensus truncation also
discards view-specific signal that §8.4 shows is real.

**D6 — Skills are a deterministic multi-label gazetteer, not NMF.**
Capabilities are a set, not a partition. The v2 NMF put 74/89 projects on one
generic factor. Rescuing that formulation was not attempted.

**D7 — Topic model selection excludes reconstruction error and prefers parsimony.**
Reconstruction improves monotonically in k and therefore measures capacity, not
structure. Selection now uses coherence, stability, support and exclusivity, then
takes the smallest k within ε of the best.

**D8 — Topics are hierarchical, depth 2, with splits accepted only on coherence.**
Flat k=14 was neither honest about research structure nor navigable.

**D9 — `semantic_space_id` and `profile_run_id` are separate identities.**
Editing a personal interest must not invalidate the landscape; refreshing the
professor corpus must invalidate professor scores. Both were wrong before.

**D10 — Campaign creation refuses on a stale semantic space.**
Silently mixing fresh entities with stale scores in an outreach decision is worse
than failing.

**D11 — Professor ranking is split into `current` and `trajectory` scopes, and
`trajectory` excludes the opportunities themselves.**
"Who can supervise me next semester" and "who thinks about the same problems" are
different questions and were previously conflated. Splitting them was not enough:
scoring `trajectory` over *all* atoms let the 2026 opportunities — top specificity,
perfect recency — crowd out the professor's own record, so the two scopes shared
85% of their evidence and correlated at Spearman 0.96. It was also circular: it
argued a career fits the operator because that professor's open call does.
Excluding opportunities from `trajectory` moved evidence overlap to 0.34 and
correlation to 0.82, and the trajectory evidence is now what it should be —
Lattes projects, SIGAA projects, articles, orientations, book chapters.

**D12 — Evidence weight = specificity × recency × corpus rarity.**
A Lattes area of "Medicina" used to surface as a top reason; `knowledge_area` now
appears in zero top-3 evidence rows. Rarity is computed from the corpus rather
than a hand-written blacklist, so it adapts to whatever UFAL actually researches.
Checked for the obvious failure mode of a top-k-max aggregate — that it would
just rank the most prolific professors — and it does not: Spearman between the
professor score and portfolio size is −0.09 (publications −0.05). The ranking
measures alignment, not output.

**D13 — Clusters come from the dominant topic path, not a separate k-means.**
Two discrete groupings of the same objects means two answers to one question. The
topic hierarchy is the honest one and it is already interpretable.

**D14 — The landscape refines PCoA with SMACOF and publishes its stress.**
A persuasive scatter plot without a distortion figure is a lie by omission.

**D15 — `entity_scores` is tall, not wide.**
Adding a channel is now a zero-migration change.

**D16 — The atom corpus is the training corpus.**
15k atoms instead of 187 opportunities. Distributional models are bounded by how
much domain language the training matrix contains, and the extra material was
already in the database.

**D17 — `data/` is git-ignored in full and the repo root holds no state files.**
`projects_ledger.*` and `professores_ufal.csv` moved to `data/state/`. Acquired
provenance is backed up separately, never committed.

**D18 — Derived-table shapes are reconciled on every `initialize()`.**
The live store carried a v2-shaped `semantic_topics` that `CREATE TABLE IF NOT
EXISTS` could not fix and did not report. The failure mode — a stale table shape
surfacing as a query error long after the migration "succeeded" — is worse than
the cost of dropping and rebuilding disposable tables. `set_meta()` replaced
`INSERT OR REPLACE INTO meta` for the same reason: it does not depend on a
primary key an older store may not have.

**D19 — Fused scores are computed per view, never from a materialized joint matrix.**
The first implementation densified every view inside `BlockFusion.encode`. With a
sparse lexical view that is ~10⁵ columns × 15k documents — unrunnable, and it
silently made the configured default fusion impossible to benchmark at corpus
scale. The block-fusion algebra already says the fused cosine equals the weighted
mean of per-view cosines, so the fix was to compute exactly that. A test pins the
two paths to agreement, so the cheap path cannot drift from the definition.

**D20 — Map fidelity is measured against the metric the map was optimized for.**
`build_map` minimized stress under `1 − cos` while `map_fidelity` reported stress
under the chord distance `√(2 − 2cos)`, and the engine let the second silently
overwrite the first. The published distortion figure therefore described a
projection nobody had built. Both now use `cosine_distance_matrix`, and a test
pins them to agreement.

**D21 — Currency is temporal, not kind-based.**
`Atom.is_current` originally meant "a kind that *can* be current, or a flag that
says active". Measured, that made CURRENT and TRAJECTORY professor rankings
near-duplicates (Spearman 0.96, identical top-8 order): the public SIGAA project
tab reaches back to 2014, and a Lattes project with no end year is "ongoing" only
because nobody updated it. Currency now requires an open call, an unexpired end
year, an explicit active flag with no contradicting dates, or a start within
`CURRENT_WINDOW_YEARS`. That cut 2,242 "current" atoms to 1,126.

**D22 — The operator profile has one canonical upstream, and it is not this repo.**
`config/profile.yaml` is now derived from `personal/academic_ledger.md`
(gitignored: it carries CPF, RG and unpublished manuscripts). The ledger
separates *truth status* from *documentary status* — a role is firm even when its
certificate has not been indexed — and carries two standing corrections that had
already leaked into 62 drafts: the OBMEP result is an honourable mention, not a
bronze medal, and the PIC/HU result is an approved-but-unclassified proposal, not
a scholarship. `about_lines` and `work_lines` are now covered by the
confidentiality test alongside `qualifications_text`, because all three are sent
to external recipients and only the last one was being checked.

**D23 — Charts in outreach are drawn in text, and only where a chart is honest.**
An inline image is blocked by default in enough clients to be unreliable, and a
remote one leaks a read receipt, so `outreach/panels.py` draws in monospace and
`text_to_html` preserves any fully indented paragraph as a `<pre>` block.
Two displays were rejected on their merits rather than styled: a bar chart of
corpus counts (work plans and archived pages do not share an axis) and a bar
chart of mean MRR (six representations inside 0.09, which as bars would look
identical while implying a resolution 187 cases do not support). What is drawn
instead is the *rank of every representation on every task*, which is where the
real result lives.

**D24 — A chart asserting alignment is an assertion of alignment.**
The method panel describes the engine and goes to all 62. The fit panel describes
one work plan and is gated on `STRONG_FIT_PERCENTILE`, exactly like the prose
claim it sits beside; without the gate a p7 recipient would be shown a bar chart
of their own poor match. The caption under the method table is *generated from
the table*, not written: on this corpus fusion wins only 2 of 7 tasks, so the
sentence claims what is true — that it is the only representation with no bad
case — and the supporting clause disappears automatically if a rebuilt space
stops supporting it.

**D25 — The reader is a professor, not a reviewer of this codebase.**
The measurement block was first written as an accurate description of the engine
and was correspondingly hard to read: six three-letter column heads, "tarefas de
recuperação", "SPPMI+SVD" unglossed. It is now transposed — representations down
the side with names a non-specialist can read, tasks numbered across the top with
a key beneath — which also puts each representation's seven results on one line,
where the pattern that carries the argument (one row with no bad number) is
visible without being explained. Two of the seven tasks are described concretely
in the prose so the table needs no prior reading.

**D26 — A funding flag read from the edital is not a vacancy.**
`has_funding` says what the edital published, and professors routinely commit a
bolsa before publication; a draft that treats the flag as ground truth asks a
question the recipient has to correct. Every funded draft now says where the
information came from, that the system cannot see a private commitment, and that
a negative answer is welcome. Both branches are tested.

**D27 — Annex delivery is proved against a copy of the live database.**
`campaign_attachments` verifies existence and total size, but nothing had shown
that the bytes survive MIME encoding for the actual campaign. The check copies
`pulsar.duckdb`, sends the real campaign to an in-process SMTP sink, and compares
SHA-256 of every received part against the source PDFs. All 62 messages carried
all four annexes intact. It runs on the copy because `send_campaign` marks rows
sent, and the real campaign must stay in `draft`.

**D28 — "Configured" was never evidence of "works", and doctor said it was.**
`_smtp_summary` reported `ready` whenever four strings were non-empty. All four
were, doctor said ready, and the relay rejected the login on the first message —
which on the real run would have been discovered mid-campaign. It now calls
`SMTPProvider.verify_credentials()`, which opens a real session, negotiates
STARTTLS and authenticates, sending nothing. The no-credentials path still
connects, because host resolution and STARTTLS are most of what fails. Google
rejecting an account password is the common case and is invisible in the error
text, so the failure names it: the stored secret was 19 characters where an App
Password is 16.

**D29 — Outreach prose is written in the register of the sender.**
A closing that explained at length that a refusal would not offend read as
pleading rather than as a colleague asking a question. The ask is now two
sentences: what the edital says, and whether it still holds. The test pins the
absence of the earlier phrasing, not only the presence of the new.

**D30 — Audience narrowed to plans that publish a funded slot.**
32 professors rather than 62. The wide send was for presenting the operator; the
narrow one is for finding a bolsa, and a plan with no funded slot cannot answer
the question the message asks.

**D31 — HTML is one element, not a wrapper.**
The theme used to wrap the message in a rounded card on a grey field in a serif
face, and the body was a stack of styled blocks, so a reply that quoted it pasted
as a mess. The body is now plain `<p>` in the reader's default size with no page
chrome; the single deliberate piece of HTML is a self-contained footer card,
placed after the signature where it cannot interrupt. Its values come from the
same `card_lines` the plaintext footer uses, so the two alternatives cannot
drift. Markup fell from ~14 KB to ~3.8 KB, and `<table>` count to one, which is
asserted.

**D32 — The measurement moved out of the body.**
The rank table and its caption were four paragraphs of method standing between a
professor reading on deadline day and the question being asked. The battery lives
in the attached report; three lines survive in the footer. `method_panel`,
`method_caption` and `fit_panel` were deleted rather than left unused.

**D33 — What is offered is selected by the recipient's own plan.**
`contribution_by_skill` maps extracted skill ids to sentences, and
`select_contributions` picks at most three against the skills PULSAR found in
*that* plan, deduplicated (four SUS skills mean one sentence about SUS
extraction, not four). `select_works` cites at most two prior outputs and only
where tags overlap the plan — an unrelated manuscript is not evidence of anything
the recipient cares about, so a plan with no overlap gets none. Across the 32
drafts this produced 19 distinct contribution sets.

**D34 — The gate is on the ranking claim, not on specificity.**
Previously a weak fit got no plan-specific content at all, which made it a form
letter. Naming a technique the recipient's own plan text asks for is a fact about
their plan, not an assertion about how well it matches, so it is now available at
any percentile; only "where you ranked" stays behind
`STRONG_FIT_PERCENTILE`. 7 of the 32 state a rank; 24 name a technique.

**D35 — The registration/conversation/indication distinction is stated once.**
PULSAR registered interest automatically wherever a plan was compatible. That is
triage, not commitment, and conflating it with an indication misrepresents the
operator to a professor who can see the SIGAA record. Every draft carries the
distinction in one sentence, and a test pins it.

**D36 — The registration is owned, not disclaimed.**
"Não é indicação nem compromisso" was discourteous and, by then, false: an
interest registered in SIGAA is visible to the professor and does carry weight.
The message now says the interest is assumed and only the *formalisation* is
pending, because the SIGAA link is singular. It also states that the offer does
not depend on a bolsa — conditioning collaboration on funding, in a message whose
whole argument is enthusiasm for the work, would read as cynical.

**D37 — Every annex is named; at most one is pointed at.**
Citing only the best-matching output presented a week that produced four
documents as one. All four are listed with their real status, `annex_recent_count`
carries the productivity claim ("3 deles escritos nesta semana"), and each date
is printed inside its own PDF — 04/09, 02/09, 01/09, and 14/08 for the older one,
which is flagged `recent: false`. Emphasis is the only thing the plan decides,
and a tie or a no-overlap plan marks nothing rather than pointing at something
unrelated.

**D38 — The procedural assembly is disclosed at the point where it can fail.**
The offer list and the annex emphasis are generated from the plan text and will
sometimes misfire. Saying so — once, without apology — costs nothing and is the
difference between a system that looks careless and one that knows its own error
modes.

**D39 — Two small charts, not a table.**
A one-line rank scale in the body (strong fits only) and three facet bars in the
footer. That is the whole graphical budget. It returns the technical texture the
big table had while leaving the message readable and quotable.

**D40 — The sender discrepancy is stated.**
The institutional account cannot issue an app password, so submission happens
through a personal Google account and the From line shows it. Rather than rely on
a "Send mail as" alias, the message says where it comes from and lists both
institutional addresses with what each is for; `reply_to` still routes a plain
reply to FAMED. A cold email that appears to misrepresent its sender is worse
than one that explains itself in a sentence.

**D41 — The expertise is placed before the offer.**
A medical student writing to a laboratory is otherwise read as asking for bench
time. One sentence states that the work is computational, names the two years of
continuous practice in data science, epidemiology and scientific computing, and
says where the return is highest — phrased as work done, not as a post held.

**D42 — The platform is the product; outreach is an application on it.**
`outreach/` moved to `apps/outreach/`, and nothing above `apps/` may import it.
The concrete failure this fixes: `webapp/payloads.py` — the console's serving
layer — imported `outreach/nomes.py` to recover a professor's accented name, so
a page rendering a ranking reached into the email package to find out what to
call someone. Name resolution is entity identity; it lives in `graph/identity.py`
now. The templates moved with the application that owns them.

**D43 — A typed, provenanced entity graph, projected rather than accumulated.**
People were rows in `professors`, collaboration was a count in
`collaboration_edges`, and orgs existed only as a string on a professor. Nothing
could be asked a structural question. `graph/` gives one id space (`kind:key`,
accent-folded), one relation vocabulary with enforced endpoints, and a
replacement-write projection. Weight sums across facts implying the same
relation; symmetric ties are stored once and expanded on read. The graph is
derived, so it costs no migration and can be rebuilt at will.

**D44 — External co-authors are named nodes, and the weakness is recorded.**
`person:name-<slug>` for anyone with no reachable registry number. Two people
publishing under one name become one node; one person under two spellings becomes
two. `is_indexed_person()` exists so a measure can tell the classes apart rather
than averaging over them, and the limitation is written down instead of hidden.

**D45 — Structural measures are computed over co-authorship alone.**
Shared technique and shared subject are derived similarities, not observed
relations. Mixing an observation with an inference into one centrality produces a
number that means neither, so `graph.metrics` measures `collaborates_with` and
nothing else. Everything is deterministic — sampled Brandes with high-degree
pivots rather than a random draw, label propagation with ties broken by smallest
label — because a rank that moves must mean the data moved.

**D46 — Every normalized share is reported with the degree it was computed over.**
Bridging and external reach are trivially extreme on a vertex with two ties: a
co-author who appears once, with one person outside their cluster, scores a
perfect 1.0 and means nothing by it. `graph top` defaults to `--min-degree 3` and
prints degree beside the value. Filtering silently would have been worse than the
noise.

**D47 — Build order is declared, not typed.**
`sync all` encoded its dependencies in the sequence a human had written them,
which made "is the graph stale" and "what does re-running this invalidate"
unanswerable. Stages now declare `depends_on`, `produces` and a freshness probe
derived from the store; the runner orders them. Acquisition is flagged, and never
runs because something downstream went stale.

**D48 — `blocked` is a state, and so is `unknown`.**
A stage that is fresh on its own terms but sits below a stale one is not `ok`:
re-running it alone would produce a confident answer from stale inputs, which is
worse than a missing one because it looks like an answer. And data present
without a journalled run is `unknown`, not `never` — a store bootstrapped from a
ledger has the rows without the run that fetched them, and calling that "never
acquired" would cascade a complete store into `blocked`.

**D49 — Name resolution infers, narrowly, and says how often it did.**
Requiring an exact name match left fifteen indexed professors with a duplicate
`person:name-…` node holding part of their collaboration. The rule that fixes it
matches first name, last name and an ordered subset of the middle names, and
accepts only a unique candidate — so it refuses where two professors could be
meant, refuses a reordered name, and refuses a bare surname. It cannot tell two
people who genuinely share a first and last name apart; the uniqueness
requirement stops that from compounding silently, and the count of inferences is
journalled into the build so the number is visible rather than assumed small.

---

## 25. Final note to the next agent

Do not preserve a weak design because it is described here. Do not add complexity
without a benchmark that justifies it. Do not touch the SIGAA automation without
testing against the live portal, and do not make sending email easier than
reviewing it.

The measure of success is not that all modules survived. It is that PULSAR can
acquire trustworthy data, search it precisely, discover non-obvious affinity,
distinguish lexical relevance from thematic compatibility, treat professors as
portfolios, rank usefully, **explain every recommendation**, characterize the
landscape, build evidence-backed audiences, draft individually, send
deliberately, and reproduce its own analytical state.
