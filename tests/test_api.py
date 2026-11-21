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
from tests.dobles import ModeloFalso


def cliente_con(tmp_path: Path, **ajustes_extra: object) -> TestClient:
    ajustes = crear_ajustes(tmp_path, **ajustes_extra)
    registro = RegistroEventos.desde_config(ajustes)
    bd = BaseDatos(ajustes.db_path, ajustes.carpeta_datos / "catalogo.json")
    bd.inicializar(recargar_catalogo=True)
    buscador = BuscadorDocumentos(chunks_de_prueba()[:2])
    buscador.indexar()
    servicio = crear_servicio(ajustes, registro, bd, buscador, ModeloFalso(fallar=True))
    app = crear_app(ajustes, servicio=servicio, indexar_al_iniciar=False)
    return TestClient(app)


@pytest.fixture()
def cliente(tmp_path: Path) -> TestClient:
    return cliente_con(tmp_path)


def test_health_resume_el_estado(cliente: TestClient) -> None:
    datos = cliente.get("/api/health").json()
    assert datos["estado"] == "ok"
    assert datos["modo"] == "demo"
    assert datos["corpus"]["chunks"] == 2
    assert datos["productos"] == 20
    assert "rag_umbral" in datos["config"]


def test_health_no_expone_claves(tmp_path: Path) -> None:
    c = cliente_con(tmp_path, llm_api_key="sk-secreta-123", admin_token="token-admin-456")
    texto = c.get("/api/health").text
    assert "sk-secreta-123" not in texto
    assert "token-admin-456" not in texto


def test_live_y_ready(cliente: TestClient) -> None:
    assert cliente.get("/api/health/live").json() == {"estado": "vivo"}
    assert cliente.get("/api/health/ready").status_code == 200


def test_ready_responde_503_mientras_arranca(tmp_path: Path) -> None:
    ajustes = crear_ajustes(tmp_path)
    app = crear_app(ajustes, indexar_al_iniciar=False)
    assert TestClient(app).get("/api/health/ready").status_code == 503


def test_productos_con_filtro(cliente: TestClient) -> None:
    datos = cliente.get("/api/productos", params={"consulta": "laptops"}).json()
    assert datos["total"] >= 1
    assert datos["productos"][0]["categoria"] == "laptop"


def test_politicas_lista_el_corpus(cliente: TestClient) -> None:
    datos = cliente.get("/api/politicas").json()
    assert datos["chunks"] == 2
    assert datos["documentos"] == ["politicas.pdf"]


def test_chat_responde_y_guarda(cliente: TestClient) -> None:
    r = cliente.post(
        "/api/chat",
        json={"mensaje": "¿cuánto cuesta el envío?", "usuario_id": "u1", "session_id": "s1"},
    )
    assert r.status_code == 200
    datos = r.json()
    assert datos["ruta"] == "faq"
    assert datos["turno_id"]
    assert datos["respuesta"]


def test_chat_valida_la_entrada(cliente: TestClient) -> None:
    assert cliente.post("/api/chat", json={"mensaje": ""}).status_code == 422
    malo = {"mensaje": "hola", "usuario_id": "../../etc/passwd"}
    assert cliente.post("/api/chat", json=malo).status_code == 422


def test_pedidos_del_cliente(cliente: TestClient) -> None:
    estado = cliente.app.state.estado  # type: ignore[attr-defined]
    estado.bd.reservar_pedido("u1", "LAPTOP001", 1)
    datos = cliente.get("/api/pedidos/u1").json()
    assert datos["total"] == 1
    assert datos["pendiente"] > 0


def test_logs_metricas_y_filtro_por_turno(cliente: TestClient) -> None:
    turno = cliente.post(
        "/api/chat", json={"mensaje": "hola", "usuario_id": "u1", "session_id": "s1"}
    ).json()["turno_id"]
    eventos = cliente.get("/api/logs", params={"n": 20}).json()
    assert any(e["evento"] == "turno.fin" for e in eventos["eventos"])
    filtrados = cliente.get("/api/logs", params={"turno": turno}).json()
    assert filtrados["total"] >= 2
    assert all(e["turno"] == turno for e in filtrados["eventos"])
    assert cliente.get("/api/metricas").json()["turnos"] >= 1


def test_borrar_sesion(cliente: TestClient) -> None:
    cliente.post("/api/chat", json={"mensaje": "hola", "usuario_id": "u1", "session_id": "s2"})
    assert cliente.delete("/api/sesiones/s2").json()["borrados"] == 2
    assert cliente.get("/api/sesion/s2").json()["total"] == 0


def test_ventas_csv(cliente: TestClient) -> None:
    bd = cliente.app.state.estado.bd  # type: ignore[attr-defined]
    pedido = bd.reservar_pedido("u1", "LAPTOP001", 1)
    bd.registrar_pago("u1", pedido["pedido_id"], "tarjeta")
    r = cliente.get("/api/ventas.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert pedido["pedido_id"] in r.text
    assert r.text.splitlines()[0].startswith("pedido;usuario")


def test_admin_exige_token_si_esta_configurado(tmp_path: Path) -> None:
    c = cliente_con(tmp_path, admin_token="secreto-de-prueba")
    for ruta in ("/api/logs", "/api/metricas", "/api/ventas.csv", "/api/sesion/s1"):
        assert c.get(ruta).status_code == 401
        assert c.get(ruta, headers={"X-Admin-Token": "otro"}).status_code == 401
        assert c.get(ruta, headers={"X-Admin-Token": "secreto-de-prueba"}).status_code == 200
    assert c.get("/api/productos").status_code == 200  # lo público sigue abierto


def test_limite_de_mensajes_por_minuto(tmp_path: Path) -> None:
    c = cliente_con(tmp_path, limite_chat_por_minuto=2)
    cuerpo = {"mensaje": "¿cuánto cuesta el envío?", "usuario_id": "u1", "session_id": "s1"}
    assert c.post("/api/chat", json=cuerpo).status_code == 200
    assert c.post("/api/chat", json=cuerpo).status_code == 200
    tercera = c.post("/api/chat", json=cuerpo)
    assert tercera.status_code == 429
    assert int(tercera.headers["Retry-After"]) >= 1


def test_cabeceras_de_seguridad(cliente: TestClient) -> None:
    r = cliente.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert "Content-Security-Policy" not in cliente.get("/docs").headers


def test_la_portada_sirve_el_frontend(cliente: TestClient) -> None:
    r = cliente.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_el_limite_no_se_esquiva_falsificando_x_forwarded_for(tmp_path: Path) -> None:
    """Detrás de un proxy confiable cuenta la IP que agrega el proxy (la última)."""
    c = cliente_con(tmp_path, limite_chat_por_minuto=1, proxies_confiables=1)
    cuerpo = {"mensaje": "¿cuánto cuesta el envío?", "usuario_id": "u1", "session_id": "s1"}
    primera = c.post("/api/chat", json=cuerpo, headers={"X-Forwarded-For": "1.1.1.1, 9.9.9.9"})
    assert primera.status_code == 200
    falsa = {"X-Forwarded-For": "2.2.2.2, 9.9.9.9"}  # el cliente cambia la entrada de la izquierda
    assert c.post("/api/chat", json=cuerpo, headers=falsa).status_code == 429
    otro = {"X-Forwarded-For": "8.8.8.8"}  # otra IP real no queda afectada
    assert c.post("/api/chat", json=cuerpo, headers=otro).status_code == 200


def test_mensaje_mas_largo_que_el_limite_del_servicio(cliente: TestClient) -> None:
    cuerpo = {"mensaje": "x" * 2500, "usuario_id": "u1", "session_id": "s1"}
    r = cliente.post("/api/chat", json=cuerpo)
    assert r.status_code == 422
    assert "2000" in r.json()["detail"]


def test_sin_x_forwarded_for_se_usa_la_ip_de_la_conexion(tmp_path: Path) -> None:
    c = cliente_con(tmp_path, limite_chat_por_minuto=1, proxies_confiables=1)
    cuerpo = {"mensaje": "¿cuánto cuesta el envío?", "usuario_id": "u1", "session_id": "s1"}
    assert c.post("/api/chat", json=cuerpo).status_code == 200
    assert c.post("/api/chat", json=cuerpo).status_code == 429


def test_los_modulos_del_frontend_se_sirven_como_javascript(cliente: TestClient) -> None:
    r = cliente.get("/js/main.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert cliente.get("/css/estilos.css").headers["content-type"].startswith("text/css")
