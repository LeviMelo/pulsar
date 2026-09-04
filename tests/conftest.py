"""Shared fixtures.

Tests never touch the operator's real DuckDB. They build a small synthetic
corpus that exercises the same code paths: sibling work plans under one project,
two clearly different research fields, and a professor portfolio.
"""

from __future__ import annotations

import pytest

from pulsar_research.db import Database


ROWS = [
    {
        "id_opportunity": "1", "project_code": "P1",
        "project_title": "Epidemiologia espacial da dengue em Alagoas",
        "plan_title": "Análise espacial e modelagem da incidência",
        "professor_siape": "10", "professor_name": "Ana Souza",
        "large_area": "Ciências da Saúde", "area": "Epidemiologia",
        "center": "FAMED", "edital": "PIBIC 2026", "has_funding": True, "funded_slots": 2,
        "introduction_justification": "A dengue apresenta distribuição espacial heterogênea (SILVA et al., 2024).",
        "objectives": "Mapear a incidência de dengue e identificar fatores associados por município.",
        "methodology": "Geoprocessamento com QGIS, regressão de Poisson e índice de Moran sobre dados do SINAN/DATASUS.",
        "acquired_skills": "R, Python, geoprocessamento, bioestatística e redação científica.",
    },
    {
        "id_opportunity": "2", "project_code": "P1",
        "project_title": "Epidemiologia espacial da dengue em Alagoas",
        "plan_title": "Organização do banco de dados e mapas temáticos",
        "professor_siape": "10", "professor_name": "Ana Souza",
        "large_area": "Ciências da Saúde", "area": "Epidemiologia",
        "center": "FAMED", "edital": "PIBIC 2026", "has_funding": True, "funded_slots": 1,
        "introduction_justification": "A distribuição territorial dos casos exige integração de bases.",
        "objectives": "Construir o banco de dados municipal e produzir mapas temáticos.",
        "methodology": "Limpeza de dados em R, geocodificação e produção de mapas temáticos.",
        "acquired_skills": "R, curadoria de bases de dados e visualização de dados.",
    },
    {
        "id_opportunity": "3", "project_code": "P2",
        "project_title": "Cultura celular tumoral e citotoxicidade",
        "plan_title": "Ensaios in vitro de viabilidade celular",
        "professor_siape": "20", "professor_name": "Bruno Lima",
        "large_area": "Ciências Biológicas", "area": "Biologia celular",
        "center": "ICBS", "edital": "PIBIC 2026", "has_funding": False, "funded_slots": 0,
        "introduction_justification": "Modelos experimentais de células tumorais permitem triagem de compostos.",
        "objectives": "Avaliar a viabilidade celular após exposição a extratos vegetais.",
        "methodology": "Cultura celular, ensaio MTT, microscopia óptica e citometria de fluxo.",
        "acquired_skills": "Cultura celular, pipetagem, microscopia e leitura crítica de artigos.",
    },
]

OPPORTUNITY_COLUMNS = [
    "id_opportunity", "project_code", "project_title", "plan_title", "professor_siape",
    "professor_name", "vacancies_text", "funded_slots", "has_funding", "funding_basis",
    "unit", "department", "center", "large_area", "area", "edital", "quota", "status",
    "has_apply_link", "has_details_link", "details_collected", "introduction_justification",
    "objectives", "methodology", "acquired_skills", "references_text", "discovered_at",
    "updated_at", "applied_at",
]


@pytest.fixture()
def rows() -> list[dict]:
    return [dict(r) for r in ROWS]


@pytest.fixture()
def db(tmp_path) -> Database:
    database = Database(tmp_path / "test.duckdb")
    database.initialize()
    defaults = {"funded_slots": 0, "has_funding": False, "has_apply_link": True,
                "has_details_link": True, "details_collected": True}
    with database.connect() as con:
        for row in ROWS:
            con.execute(
                f"INSERT INTO opportunities VALUES ({','.join('?' * len(OPPORTUNITY_COLUMNS))})",
                [row.get(c, defaults.get(c, "")) for c in OPPORTUNITY_COLUMNS],
            )
        for siape, name, center in (("10", "Ana Souza", "FAMED"), ("20", "Bruno Lima", "ICBS")):
            con.execute(
                "INSERT INTO professors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [siape, name, "Depto", "Unidade", center, f"{siape}@ufal.br", "", "", "", "",
                 "", "", f"Pesquisador em {'epidemiologia' if siape == '10' else 'biologia celular'}, "
                         f"com experiência em métodos quantitativos e trabalho experimental.",
                 "", ""],
            )
    return database
