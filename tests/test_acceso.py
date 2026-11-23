"""Acceso de la demo: hash de la clave, sesiones, inicio de sesión y protección de la API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import ValidationError

from app.api.acceso import COOKIE
from app.api.limitador import LimitadorPeticiones
from app.api.sesiones import Sesiones
from app.core.claves import generar_hash, hash_valido, verificar_clave
from tests.conftest import crear_ajustes
from tests.test_api import cliente_con

CLAVE = "clave-de-prueba-123"
HASH = generar_hash(
    CLAVE, iteraciones=1000
)  # pocas iteraciones: solo para que las pruebas sean rápidas
ACCESO = {"acceso_usuario": "demo", "acceso_clave_hash": HASH}
CHAT = {"mensaje": "hola", "usuario_id": "u1", "session_id": "s1"}


class Reloj:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def entrar(cliente: TestClient, usuario: str = "demo", clave: str = CLAVE) -> Response:
    return cliente.post("/api/acceso/login", json={"usuario": usuario, "clave": clave})


@pytest.fixture()
def protegido(tmp_path: Path) -> TestClient:
    return cliente_con(tmp_path, **ACCESO)


# -- hash de la clave ----------------------------------------------------------
def test_el_hash_solo_valida_la_clave_correcta() -> None:
    assert hash_valido(HASH)
    assert verificar_clave(CLAVE, HASH)
    assert not verificar_clave("otra-clave", HASH)
    assert generar_hash(CLAVE, iteraciones=1000) != HASH  # sal aleatoria
    sal = b"s" * 16
    assert generar_hash("x", iteraciones=1000, sal=sal) == generar_hash(
        "x", iteraciones=1000, sal=sal
    )


@pytest.mark.parametrize(
    "malo",
    [
        "",
        "clave-en-texto",
        "md5$1000$AAAAAAAAAAAAAAAAAAAAAA==$" + "A" * 44,
        "pbkdf2_sha256$1000$abc$def",  # base64 con relleno inválido
        "pbkdf2_sha256$1000$AAAA$" + "A" * 44,  # sal demasiado corta
    ],
)
def test_un_hash_mal_formado_nunca_valida(malo: str) -> None:
    assert not hash_valido(malo)
    assert not verificar_clave("x", malo)


# -- sesiones ------------------------------------------------------------------
def test_las_sesiones_vencen_y_se_cierran() -> None:
    reloj = Reloj()
    sesiones = Sesiones(60, reloj=reloj)
    token = sesiones.crear("demo")
    assert sesiones.usuario_de(token) == "demo"
    assert sesiones.usuario_de(None) is None
    assert sesiones.usuario_de("token-inventado") is None
    reloj.t = 61
    assert sesiones.usuario_de(token) is None
    otro = sesiones.crear("demo")
    sesiones.cerrar(otro)
    sesiones.cerrar(None)
    assert sesiones.usuario_de(otro) is None


def test_al_llegar_al_maximo_se_descarta_la_sesion_mas_antigua() -> None:
    sesiones = Sesiones(60, reloj=Reloj(), maximo=2)
    primera, segunda, tercera = (sesiones.crear(u) for u in ("a", "b", "c"))
    assert sesiones.usuario_de(primera) is None
    assert (sesiones.usuario_de(segunda), sesiones.usuario_de(tercera)) == ("b", "c")


# -- configuración -------------------------------------------------------------
def test_la_configuracion_del_acceso_se_valida_al_arrancar(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="se definen juntos"):
        crear_ajustes(tmp_path, acceso_usuario="demo")
    with pytest.raises(ValidationError, match="formato esperado"):
        crear_ajustes(tmp_path, acceso_usuario="demo", acceso_clave_hash="clave-en-texto")
    ajustes = crear_ajustes(tmp_path, **ACCESO)
    assert ajustes.acceso_configurado
    assert ajustes.resumen()["acceso_requerido"] is True
    assert not crear_ajustes(tmp_path).acceso_configurado


# -- API -----------------------------------------------------------------------
def test_sin_sesion_la_api_responde_401_salvo_salud(protegido: TestClient) -> None:
    respuesta = protegido.post("/api/chat", json=CHAT)
    assert respuesta.status_code == 401
    assert respuesta.headers["X-Acceso"] == "requerido"
    for ruta in ("/api/libros", "/api/categorias", "/api/logs", "/api/pedidos/u1"):
        assert protegido.get(ruta).status_code == 401, ruta
    for ruta in ("/api/health", "/api/health/live", "/api/health/ready"):
        assert protegido.get(ruta).status_code == 200, ruta
    estado = protegido.get("/api/acceso/estado").json()
    assert estado == {"requerido": True, "autenticado": False, "usuario": None}


def test_el_login_abre_la_api_y_el_logout_la_cierra(protegido: TestClient) -> None:
    respuesta = entrar(protegido)
    assert respuesta.status_code == 200
    assert respuesta.json() == {"requerido": True, "autenticado": True, "usuario": "demo"}
    cookie = respuesta.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" not in cookie
    assert protegido.get("/api/libros").status_code == 200
    assert protegido.post("/api/chat", json=CHAT).status_code == 200
    assert protegido.get("/api/acceso/estado").json()["autenticado"] is True
    assert protegido.post("/api/acceso/logout").json()["autenticado"] is False
    assert protegido.get("/api/libros").status_code == 401


def test_un_login_fallido_no_dice_que_dato_fallo(protegido: TestClient) -> None:
    for usuario, clave in (("demo", "mala"), ("otro", CLAVE), ("démo", CLAVE)):
        respuesta = entrar(protegido, usuario, clave)
        assert respuesta.status_code == 401
        assert respuesta.json()["detail"] == "Usuario o clave incorrectos."
        assert COOKIE not in respuesta.cookies


def test_los_intentos_se_limitan_por_ip(tmp_path: Path) -> None:
    cliente = cliente_con(tmp_path, acceso_intentos_por_ip=2, **ACCESO)
    assert entrar(cliente, clave="mala").status_code == 401
    assert entrar(cliente, clave="mala").status_code == 401
    bloqueado = entrar(cliente)  # ni con la clave correcta mientras dure el bloqueo
    assert bloqueado.status_code == 429
    assert int(bloqueado.headers["Retry-After"]) > 0


def test_hay_un_tope_total_de_intentos(protegido: TestClient) -> None:
    protegido.app.state.estado.intentos_totales = LimitadorPeticiones(1, 900)  # type: ignore[attr-defined]
    assert entrar(protegido, clave="mala").status_code == 401
    assert entrar(protegido).status_code == 429


def test_el_registro_anota_los_intentos_sin_guardar_la_clave(protegido: TestClient) -> None:
    entrar(protegido, clave="intento-secreto-que-no-va-al-log")
    entrar(protegido)
    eventos = protegido.app.state.estado.registro.leer(limite=50)  # type: ignore[attr-defined]
    nombres = {e["evento"] for e in eventos}
    assert {"acceso.fallido", "acceso.correcto"} <= nombres
    assert "intento-secreto-que-no-va-al-log" not in str(eventos)
    assert CLAVE not in str(eventos)


def test_sin_acceso_configurado_la_api_queda_abierta(tmp_path: Path) -> None:
    cliente = cliente_con(tmp_path)
    estado = cliente.get("/api/acceso/estado").json()
    assert estado == {"requerido": False, "autenticado": True, "usuario": None}
    assert entrar(cliente).json()["requerido"] is False
    assert cliente.get("/api/libros").status_code == 200


def test_en_produccion_la_cookie_solo_viaja_por_https(tmp_path: Path) -> None:
    cliente = cliente_con(tmp_path, entorno="produccion", admin_token="x", **ACCESO)
    respuesta = entrar(cliente)
    assert respuesta.status_code == 200
    assert "secure" in respuesta.headers["set-cookie"].lower()
    assert "secure" in cliente.post("/api/acceso/logout").headers["set-cookie"].lower()
