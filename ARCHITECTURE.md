# Architecture notes

## 1. Acquisition

### Authenticated SIGAA

`pulsar_research.sigaa.authenticated` preserves the working legacy interaction model. It exposes three operations:

1. opportunity discovery/detail synchronization;
2. authoritative application-state synchronization;
3. explicit single-opportunity application.

The first two never submit an application.

### Public professor SIGAA

`pulsar_research.sigaa.public_scraper` is the exhaustive public-page archiver. It resolves SIAPEs from the public faculty search and archives/parses the seven deterministic professor tabs. It retains raw bytes, normalized UTF-8, generic DOM/table/field/link extraction, photos, detail links, and the embedded `var curriculo` Lattes snapshot.

`ingest.public_dataset` imports that corpus into the canonical DuckDB and uses the resolver output to attach SIAPEs to opportunity records.

## 2. Canonical identities

- professor: SIAPE
- opportunity/work plan: SIGAA `id_oportunidade`
- project: SIGAA project code when present
- Lattes: CNPq/Lattes identifier, kept separate from SIAPE

Names are aliases, never primary identities.

## 3. Intelligence

The analytical engine is intentionally sparse and rebuildable. It does not persist dense transformer embeddings.

Each opportunity receives a structured composite document. Each professor receives a bounded document assembled from profile summary, current opportunities, public project/production/teaching/extension pages, and selected research-relevant Lattes leaves.

The engine calculates word/character TF-IDF, LSA, BM25 relevance, NMF topics, K-means clusters and 2-D latent coordinates. Campaigns can use these signals independently.

## 4. Campaign state

Campaigns are snapshots. Creation stores:

- audience criteria;
- exact recipient set;
- exact qualifying opportunity evidence;
- selection score;
- one rendered draft per recipient.

Editing a draft never changes the template for other recipients. Selection can also be toggled per recipient. Delivery only reads persisted selected drafts and skips messages already marked `sent`.

This prevents later source-data changes from silently changing why a professor was selected or what was sent.
