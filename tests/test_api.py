"""API HTTP con TestClient: rutas, protección de administración, límites y cabeceras."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.logs import RegistroEventos
from app.main import crear_app
from app.persistencia.db import BaseDatos
from app.servicios.rag import BuscadorDocumentos
from tests.conftest import chunks_de_prueba, crear_ajustes, crear_servicio
from tests.datos_prueba import ISBN_DRAGON, ISBN_MAGO, ISBN_SUMAS, LIBROS_PRUEBA
from tests.dobles import ModeloFalso


def cliente_con(tmp_path: Path, **ajustes_extra: object) -> TestClient:
    ajustes = crear_ajustes(tmp_path, **ajustes_extra)
    registro = RegistroEventos.desde_config(ajustes)
    bd = BaseDatos(ajustes.db_path, ajustes.carpeta_datos / "catalogo.json")
    bd.inicializar(recargar_catalogo=True)
    buscador = BuscadorDocumentos(chunks_de_prueba()[:2])
    buscador.indexar()
    servicio = crear_servicio(ajustes, registro, bd, buscador, ModeloFalso(fallar=True))
    servicio.recomendador.indexar()
    app = crear_app(ajustes, servicio=servicio, indexar_al_iniciar=False)
    return TestClient(app)


@pytest.fixture()
def cliente(tmp_path: Path) -> TestClient:
    return cliente_con(tmp_path)


def chat(cliente: TestClient, mensaje: str, sesion: str = "s1") -> dict[str, object]:
    cuerpo = {"mensaje": mensaje, "usuario_id": "u1", "session_id": sesion}
    respuesta = cliente.post("/api/chat", json=cuerpo)
    assert respuesta.status_code == 200
    datos: dict[str, object] = respuesta.json()
    return datos


def test_health_resume_el_estado(cliente: TestClient) -> None:
    datos = cliente.get("/api/health").json()
    assert datos["estado"] == "ok" and datos["modo"] == "demo"
    assert datos["tienda"]["moneda"] == "S/" and datos["tienda"]["nombre"]
    assert datos["corpus"]["chunks"] == 2
    assert datos["catalogo"] == {"libros": 10, "motor": "tfidf", "indexado": True, "aviso": None}
    assert datos["libros"] == len(LIBROS_PRUEBA)
    assert "reco_pesos" in datos["config"]


def test_health_no_expone_claves(tmp_path: Path) -> None:
    c = cliente_con(tmp_path, llm_api_key="sk-secreta-123", admin_token="token-admin-456")
    texto = c.get("/api/health").text
    assert "sk-secreta-123" not in texto and "token-admin-456" not in texto


def test_live_y_ready(cliente: TestClient) -> None:
    assert cliente.get("/api/health/live").json() == {"estado": "vivo"}
    assert cliente.get("/api/health/ready").status_code == 200


def test_ready_responde_503_mientras_arranca(tmp_path: Path) -> None:
    app = crear_app(crear_ajustes(tmp_path), indexar_al_iniciar=False)
    assert TestClient(app).get("/api/health/ready").status_code == 503


def test_libros_con_filtros(cliente: TestClient) -> None:
    datos = cliente.get("/api/libros", params={"consulta": "magos"}).json()
    assert datos["total"] == 1 and datos["libros"][0]["isbn"] == ISBN_MAGO
    ninos = cliente.get("/api/libros", params={"edad": 5, "limite": 10}).json()
    assert ISBN_SUMAS in [lb["isbn"] for lb in ninos["libros"]]
    assert cliente.get("/api/libros", params={"edad": 200}).status_code == 422


def test_ficha_de_un_libro(cliente: TestClient) -> None:
    ficha = cliente.get(f"/api/libros/{ISBN_DRAGON}").json()
    assert ficha["titulo"] == "La montaña del dragón" and ficha["temas"]
    assert cliente.get("/api/libros/9780000000999").status_code == 404
    assert cliente.get("/api/libros/no-es-isbn").status_code == 422


def test_catalogo_completo_para_la_vitrina(cliente: TestClient) -> None:
    datos = cliente.get("/api/catalogo").json()
    assert datos["total"] == len(LIBROS_PRUEBA) == len(datos["libros"])
    assert {"isbn", "titulo", "autor", "precio", "stock", "portada"} <= datos["libros"][0].keys()


def test_categorias(cliente: TestClient) -> None:
    categorias = {c["categoria"]: c["libros"] for c in cliente.get("/api/categorias").json()}
    assert categorias["novela"] == 4 and sum(categorias.values()) == len(LIBROS_PRUEBA)


def test_politicas_lista_el_corpus(cliente: TestClient) -> None:
    datos = cliente.get("/api/politicas").json()
    assert datos["chunks"] == 2 and datos["documentos"] == ["politicas.md"]


def test_chat_responde_y_guarda(cliente: TestClient) -> None:
    datos = chat(cliente, "¿cuánto cuesta el envío?")
    assert datos["ruta"] == "faq" and datos["turno_id"] and datos["respuesta"]
    assert datos["libros"] == []


def test_chat_valida_la_entrada(cliente: TestClient) -> None:
    assert cliente.post("/api/chat", json={"mensaje": ""}).status_code == 422
    malo = {"mensaje": "hola", "usuario_id": "../../etc/passwd"}
    assert cliente.post("/api/chat", json=malo).status_code == 422
    largo = {"mensaje": "x" * 2500, "usuario_id": "u1", "session_id": "s1"}
    respuesta = cliente.post("/api/chat", json=largo)
    assert respuesta.status_code == 422 and "2000" in respuesta.json()["detail"]


def test_pedidos_del_cliente(cliente: TestClient) -> None:
    cliente.app.state.estado.bd.reservar_pedido("u1", ISBN_MAGO, 1)  # type: ignore[attr-defined]
    datos = cliente.get("/api/pedidos/u1").json()
    assert datos["total"] == 1 and datos["pendiente"] > 0
    assert datos["pedidos"][0]["lineas"][0]["titulo"] == "La escuela de magos"
    assert datos["pedidos"][0]["envio"] == 10.0  # Lima, por debajo del envío gratis


def test_logs_metricas_y_filtro_por_turno(cliente: TestClient) -> None:
    turno = chat(cliente, "hola")["turno_id"]
    eventos = cliente.get("/api/logs", params={"n": 20}).json()
    assert any(e["evento"] == "turno.fin" for e in eventos["eventos"])
    filtrados = cliente.get("/api/logs", params={"turno": turno}).json()
    assert filtrados["total"] >= 2 and all(e["turno"] == turno for e in filtrados["eventos"])
    assert cliente.get("/api/metricas").json()["turnos"] >= 1


def test_borrar_sesion(cliente: TestClient) -> None:
    chat(cliente, "hola", sesion="s2")
    assert cliente.delete("/api/sesiones/s2").json()["borrados"] == 2
    assert cliente.get("/api/sesion/s2").json()["total"] == 0


def test_ventas_csv(cliente: TestClient) -> None:
    bd = cliente.app.state.estado.bd  # type: ignore[attr-defined]
    pedido = bd.reservar_pedido("u1", ISBN_MAGO, 1)
    bd.registrar_pago("u1", pedido["pedido_id"], "transferencia")
    r = cliente.get("/api/ventas.csv")
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    assert r.text.splitlines()[0].startswith("pedido;usuario;isbn;titulo;autor")
    assert pedido["pedido_id"] in r.text and "La escuela de magos" in r.text


def test_admin_exige_token_si_esta_configurado(tmp_path: Path) -> None:
    c = cliente_con(tmp_path, admin_token="secreto-de-prueba")
    for ruta in ("/api/logs", "/api/metricas", "/api/ventas.csv", "/api/sesion/s1"):
        assert c.get(ruta).status_code == 401
        assert c.get(ruta, headers={"X-Admin-Token": "otro"}).status_code == 401
        assert c.get(ruta, headers={"X-Admin-Token": "secreto-de-prueba"}).status_code == 200
    assert c.get("/api/libros").status_code == 200  # lo público sigue abierto


def test_limite_de_mensajes_por_minuto(tmp_path: Path) -> None:
    c = cliente_con(tmp_path, limite_chat_por_minuto=2)
    cuerpo = {"mensaje": "¿cuánto cuesta el envío?", "usuario_id": "u1", "session_id": "s1"}
    assert c.post("/api/chat", json=cuerpo).status_code == 200
    assert c.post("/api/chat", json=cuerpo).status_code == 200
    tercera = c.post("/api/chat", json=cuerpo)
    assert tercera.status_code == 429 and int(tercera.headers["Retry-After"]) >= 1


def test_el_limite_no_se_esquiva_falsificando_x_forwarded_for(tmp_path: Path) -> None:
    """Detrás de un proxy confiable cuenta la IP que agrega el proxy (la última)."""
    c = cliente_con(tmp_path, limite_chat_por_minuto=1, proxies_confiables=1)
    cuerpo = {"mensaje": "¿cuánto cuesta el envío?", "usuario_id": "u1", "session_id": "s1"}
    primera = c.post("/api/chat", json=cuerpo, headers={"X-Forwarded-For": "1.1.1.1, 9.9.9.9"})
    assert primera.status_code == 200
    falsa = {"X-Forwarded-For": "2.2.2.2, 9.9.9.9"}
    assert c.post("/api/chat", json=cuerpo, headers=falsa).status_code == 429
    assert (
        c.post("/api/chat", json=cuerpo, headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 200
    )
    assert c.post("/api/chat", json=cuerpo).status_code == 200  # sin cabecera: IP de la conexión


def test_cabeceras_de_seguridad(cliente: TestClient) -> None:
    r = cliente.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert "Content-Security-Policy" not in cliente.get("/docs").headers


def test_la_portada_y_los_modulos_del_frontend(cliente: TestClient) -> None:
    portada = cliente.get("/")
    assert portada.status_code == 200 and "text/html" in portada.headers["content-type"]
    assert cliente.get("/js/main.js").headers["content-type"].startswith("text/javascript")
    assert cliente.get("/css/base.css").headers["content-type"].startswith("text/css")
