"""Fragmentación, búsqueda con MMR, embeddings densos, caché y degradación a TF-IDF."""

from __future__ import annotations

from pathlib import Path

from app.servicios.rag import (
    BuscadorDocumentos,
    Chunk,
    ParametrosRAG,
    dividir_oraciones,
    trocear,
)
from tests.conftest import chunks_de_prueba
from tests.dobles import EmbedderFalso


def test_respeta_el_maximo_de_palabras() -> None:
    texto = " ".join("Esta es una oración de prueba con varias palabras." for _ in range(40))
    chunks = trocear(texto, max_palabras=50, overlap_palabras=10)
    assert chunks
    assert max(len(c.split()) for c in chunks) <= 50


def test_el_solape_es_por_palabras() -> None:
    texto = " ".join(f"Oración número {i} con contenido suficiente." for i in range(30))
    primero, segundo = trocear(texto, max_palabras=40, overlap_palabras=12)[:2]
    a, b = primero.split(), segundo.split()
    assert any(a[-n:] == b[:n] for n in range(1, min(len(a), len(b)) + 1))


def test_no_parte_abreviaturas_ni_decimales() -> None:
    oraciones = dividir_oraciones(
        "El Dr. Pérez llegó a las 9.30 h. y cobró 3.5 soles. Luego cerró."
    )
    assert len(oraciones) == 2
    assert "Dr. Pérez" in oraciones[0]


def test_una_oracion_gigante_se_parte_igual() -> None:
    texto = " ".join(f"palabra{i}" for i in range(120))
    chunks = trocear(texto, max_palabras=50, overlap_palabras=5)
    assert len(chunks) == 3


def test_busqueda_devuelve_lo_relevante(buscador: BuscadorDocumentos) -> None:
    hallazgos = buscador.buscar("¿puedo cambiar un libro?", k=2)
    assert hallazgos[0]["id"] == "doc-cambios"
    assert 0 < hallazgos[0]["score"] <= 1
    assert hallazgos[0]["motor"] == "tfidf"


def test_umbral_descarta_lo_que_no_tiene_que_ver(buscador: BuscadorDocumentos) -> None:
    assert buscador.buscar("receta de pizza con anchoas", k=2) == []


def test_mmr_evita_tres_copias_del_mismo_parrafo() -> None:
    repetido = (
        "La garantía cubre defectos de fábrica durante un año desde la entrega "
        "del producto y no cubre el mal uso."
    )
    chunks = [
        Chunk("dup-1", "a.pdf", 1, 0, repetido),
        Chunk("dup-2", "a.pdf", 1, 1, repetido + " "),
        Chunk("dup-3", "a.pdf", 1, 2, repetido + "  "),
        Chunk("otro", "b.pdf", 2, 0, "La garantía también cubre los accesorios originales."),
    ]
    buscador = BuscadorDocumentos(chunks)
    sin_mmr = [h["id"] for h in buscador.buscar("garantía", k=2, inicial=4, lam=1.0)]
    con_mmr = [h["id"] for h in buscador.buscar("garantía", k=2, inicial=4, lam=0.5)]
    assert len(set(sin_mmr)) == 2
    assert "otro" in con_mmr, "MMR debe preferir un fragmento distinto antes que un duplicado"


def test_info_no_indexa_y_luego_informa_el_motor(buscador: BuscadorDocumentos) -> None:
    info = buscador.info()
    assert info["chunks"] == 4
    assert info["indexado"] is False
    buscador.indexar()
    assert buscador.info()["motor"] == "tfidf"


def test_con_embeddings_usa_el_motor_denso() -> None:
    embedder = EmbedderFalso()
    buscador = BuscadorDocumentos(
        chunks_de_prueba(), ParametrosRAG(umbral_denso=0.2), embedder=embedder
    )
    hallazgos = buscador.buscar("mi libro tiene páginas mal impresas", k=1)
    assert buscador.motor == "denso"
    assert hallazgos[0]["id"] == "doc-defectos"
    assert hallazgos[0]["motor"] == "denso"


def test_la_cache_evita_recalcular_embeddings(tmp_path: Path) -> None:
    cache = tmp_path / "embeddings.npz"
    primero = EmbedderFalso()
    BuscadorDocumentos(chunks_de_prueba(), embedder=primero, cache=cache).indexar()
    assert cache.exists()

    segundo = EmbedderFalso()
    BuscadorDocumentos(chunks_de_prueba(), embedder=segundo, cache=cache).indexar()
    assert segundo.llamadas == 0  # los documentos salieron de la caché


def test_si_la_api_de_embeddings_falla_se_usa_tfidf() -> None:
    buscador = BuscadorDocumentos(chunks_de_prueba(), embedder=EmbedderFalso(fallar=True))
    hallazgos = buscador.buscar("¿puedo cambiar un libro?", k=1)
    assert buscador.motor == "tfidf"
    assert buscador.info()["aviso"]
    assert hallazgos[0]["id"] == "doc-cambios"


def test_corpus_real_se_fragmenta() -> None:
    from app.core.config import RAIZ
    from app.servicios.rag import construir_chunks

    chunks = construir_chunks(RAIZ / "datos" / "documentos", ParametrosRAG())
    documentos = {c.documento for c in chunks}
    assert {"politicas_libreria.md", "Manual_Consumidor_Indecopi.pdf"} <= documentos
    assert max(len(c.texto.split()) for c in chunks) <= 150


def test_markdown_se_divide_por_secciones(tmp_path: Path) -> None:
    from app.servicios.rag import construir_chunks, leer_markdown

    texto = (
        "Introducción sin título con varias palabras para que el fragmento tenga sentido.\n\n"
        "# Envíos\n\n- El envío en Lima cuesta **diez soles** y llega en dos días hábiles.\n\n"
        "## Cambios\n\n1. Un libro nuevo se puede cambiar dentro de los siete días siguientes.\n"
    )
    (tmp_path / "politicas.md").write_text(texto, encoding="utf-8")
    (tmp_path / "notas.txt").write_text("se ignora", encoding="utf-8")
    secciones = leer_markdown(tmp_path / "politicas.md")
    assert [n for n, _ in secciones] == [1, 2, 3]
    assert secciones[1][1].startswith("Envíos") and "**" not in secciones[1][1]
    assert "- " not in secciones[1][1] and "1. " not in secciones[2][1]
    chunks = construir_chunks(tmp_path, ParametrosRAG())
    assert {c.documento for c in chunks} == {"politicas.md"}


def test_corpus_vacio_no_rompe() -> None:
    buscador = BuscadorDocumentos([])
    assert buscador.indexar() == 0
    assert buscador.buscar("garantía") == []
    assert buscador.info()["palabras_por_chunk"] == [0, 0, 0]


def test_umbral_explicito_en_la_busqueda(buscador: BuscadorDocumentos) -> None:
    assert buscador.buscar("garantía", umbral=0.99) == []


def test_si_falla_la_consulta_densa_se_usa_tfidf_en_esa_pregunta() -> None:
    embedder = EmbedderFalso()
    buscador = BuscadorDocumentos(chunks_de_prueba(), embedder=embedder)
    buscador.indexar()
    embedder.fallar = True
    hallazgos = buscador.buscar("¿puedo cambiar un libro?", k=1)
    assert hallazgos[0]["motor"] == "tfidf"
    assert "TF-IDF" in (buscador.info()["aviso"] or "")


def test_cache_corrupta_o_de_otro_modelo_se_recalcula(tmp_path: Path) -> None:
    cache = tmp_path / "embeddings.npz"
    cache.write_bytes(b"no es un npz")
    embedder = EmbedderFalso()
    BuscadorDocumentos(chunks_de_prueba(), embedder=embedder, cache=cache).indexar()
    assert embedder.llamadas == 1  # la caché dañada se ignoró

    class OtroModelo(EmbedderFalso):
        @property
        def identificador(self) -> str:
            return "otro:10"

    otro = OtroModelo()
    BuscadorDocumentos(chunks_de_prueba(), embedder=otro, cache=cache).indexar()
    assert otro.llamadas == 1  # huella distinta: no reutiliza vectores de otro modelo


def test_si_no_se_puede_escribir_la_cache_se_sigue(tmp_path: Path) -> None:
    carpeta = tmp_path / "embeddings.npz"
    carpeta.mkdir()  # una carpeta donde debería ir el archivo
    buscador = BuscadorDocumentos(chunks_de_prueba(), embedder=EmbedderFalso(), cache=carpeta)
    buscador.indexar()
    assert buscador.motor == "denso"


def test_parametros_desde_la_configuracion(tmp_path: Path) -> None:
    from tests.conftest import crear_ajustes

    ajustes = crear_ajustes(tmp_path, rag_k_final=2, rag_mmr_lambda=0.7)
    parametros = ParametrosRAG.desde_config(ajustes)
    assert parametros.k_final == 2 and parametros.mmr_lambda == 0.7
