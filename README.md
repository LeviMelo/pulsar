# PULSAR

Local research/opportunity intelligence system for UFAL/SIGAA. It consolidates authenticated research opportunities, public professor/Lattes data, lightweight unsupervised semantic analysis, a DuckDB analytical store, a Streamlit operator dashboard, and personalized email campaigns.

## Architecture

```text
Authenticated SIGAA ─┐
Public professor SIGAA ─┼─> DuckDB + raw provenance ─> sparse intelligence ─> audiences ─> editable drafts ─> SMTP
Legacy ledgers/CSV ────┘                              └──────────── Streamlit operator console
```

The important separation is **acquisition → intelligence → audience → campaign → delivery**. A campaign recipient always persists the exact qualifying evidence (opportunity/project/plan), rather than a bare `has_bolsa=true` flag.

## Install

```powershell
conda env create -f environment.yml
conda activate pulsar
pip install -e .
playwright install chromium
```

Initialize the database:

```powershell
pulsar init
```

### Secrets

The old prototype embedded SIGAA credentials directly in Python. This system deliberately does not. Set them in the shell/session or a local secret manager:

```powershell
$env:UFAL_SIGAA_USERNAME="..."
$env:UFAL_SIGAA_PASSWORD="..."
```

SMTP credentials are similarly read from `PULSAR_SMTP_USER` and `PULSAR_SMTP_PASSWORD`. Configure host/from-address in `config/default.toml`.

## Migration from the existing root files

If `projects_ledger.json` and `professores_ufal.csv` are still in the repository root:

```powershell
pulsar import-legacy
```

This imports them into DuckDB but keeps the legacy files usable as compatibility/provenance artifacts.

## Synchronization

Authenticated opportunity discovery (safe: **does not apply**):

```powershell
pulsar sync opportunities
```

Public professor/Lattes corpus:

```powershell
pulsar sync professors
```

The command regenerates `professores_ufal.csv` from the current opportunity database when possible, resolves SIAPEs through the public SIGAA faculty search, archives all seven public professor tabs and detail pages, extracts the embedded Lattes object, and imports the resulting corpus into the main DuckDB database.

Authoritative application-state sync:

```powershell
pulsar sync applications
```

Complete pipeline:

```powershell
pulsar sync all
```

### Explicit application

Application is separated from discovery and requires explicit confirmation:

```powershell
pulsar applications apply 98379721 --confirm
```

The Playwright navigation/selectors/JSF bean actions and Windows-1252 detail-fetch code are intentionally conservative ports of the working prototype. Avoid refactoring those internals without live SIGAA regression testing.

## Intelligence engine

No transformer embeddings or vector database are required. Rebuild with:

```powershell
pulsar analyze rebuild
```

The corpus-native engine combines:

- word TF-IDF (1–2 grams)
- character TF-IDF (3–5 grams)
- Latent Semantic Analysis / truncated SVD
- BM25-style lexical relevance
- NMF topics
- K-means thematic clusters

It produces independent similarity components plus a configurable combined score. The underlying signals remain visible and campaign filters can use semantic score, cluster/topic membership, structured metadata, funding, center, and literal research vocabulary. Arbitrary research questions can also be scored on demand without storing embeddings:

```powershell
pulsar analyze search "epidemiologia espacial DATASUS" --type opportunity
```

## Dashboard

```powershell
pulsar dashboard
```

The Streamlit app reads DuckDB directly and provides:

- overview and concentration/funding metrics
- opportunity explorer
- professor explorer
- semantic landscape/topics
- campaign audience construction
- per-recipient draft editing and deselection
- explicit campaign send control

## Campaign workflow

Create a funded-opportunity campaign:

```powershell
pulsar campaign create "PIBIC outreach" --funded --center FAMED --min-opportunity-fit 0.30
```

More targeted selection:

```powershell
pulsar campaign create "Epidemiologia" \
  --funded \
  --center FAMED \
  --query "epidemiologia de dados públicos do SUS e análise espacial" \
  --min-query-score 0.25 \
  --keyword epidemiologia
```

Inspect exact qualifying projects and rendered drafts:

```powershell
pulsar campaign preview <campaign_id>
```

Export:

```powershell
pulsar campaign export <campaign_id>
```

Sending is disabled unless explicitly confirmed:

```powershell
pulsar campaign send <campaign_id> --confirm
```

The recommended workflow is to create the campaign in CLI or Streamlit, inspect the persisted qualifying evidence, edit individual drafts in Streamlit, deselect recipients as needed, and only then send.

## Useful inspection commands

```powershell
pulsar doctor
pulsar professors list
pulsar professors show 1157495
pulsar opportunities list --funded
pulsar campaign list
```

## Data

The main analytical store is `data/pulsar.duckdb`. Raw public SIGAA snapshots remain under `data/public_sigaa/`; the raw server responses and parsed public dataset are preserved separately from the normalized intelligence tables.
