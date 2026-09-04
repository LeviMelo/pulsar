from pathlib import Path


def test_fragile_sigaa_literals_are_preserved():
    source = (Path(__file__).parents[1] / "src" / "pulsar_research" / "acquisition" / "sigaa_authenticated.py").read_text(encoding="utf-8")
    required = [
        "interessadoBolsa.acompanharInscricoes",
        "agregadorBolsas.iniciarBuscar",
        "form#menu\\\\:form_menu_discente",
        "TextDecoder('windows-1252')",
        "form#listagemResultado table.listagem",
        "input[id='busca:btaoBuscar']",
        "textarea[id='form:qualificacoes']",
        "input[id='form:inscreverse']",
    ]
    for literal in required:
        assert literal in source
