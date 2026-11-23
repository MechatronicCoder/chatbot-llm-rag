"""Evalúa el chatbot con el conjunto de casos de `evaluacion/casos.json`.

Cada caso abre una sesión nueva, envía sus mensajes y compara la ruta del
último turno (faq, filtro, herramientas, recomendacion, accion, rag…) con la
esperada; si el caso indica una FAQ, un documento o un libro, también se
comprueba. Para la recomendación se informa el acierto: cuántas veces el libro
esperado está entre los libros que mostró el turno. Se usa una base de datos
temporal: la evaluación no ensucia `var/tienda.db`.

Uso:
    uv run python scripts/evaluar.py              # con la configuración del .env
    uv run python scripts/evaluar.py --sin-llm    # solo lo que no necesita modelo
    uv run python scripts/evaluar.py --minimo 0.9 # sale con código 1 si no llega

El informe se imprime y se guarda en `evaluacion/resultados/`.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from app.core.config import Settings  # noqa: E402
from app.core.logs import RegistroEventos  # noqa: E402
from app.persistencia.db import BaseDatos  # noqa: E402
from app.servicios.fabrica import construir_servicio  # noqa: E402


def evaluar_caso(servicio: Any, caso: dict[str, Any]) -> dict[str, Any]:
    sesion = f"eval-{caso['id']}"
    respuesta = None
    for mensaje in caso["mensajes"]:
        respuesta = servicio.responder(mensaje, sesion, sesion)
    assert respuesta is not None
    documentos = {d["documento"] for d in respuesta.documentos}
    titulos = [lb["titulo"] for lb in respuesta.libros]
    fallos = []
    if respuesta.ruta not in caso["ruta"]:
        fallos.append(f"ruta {respuesta.ruta} (se esperaba {'/'.join(caso['ruta'])})")
    if caso.get("faq") and respuesta.faq_id != caso["faq"]:
        fallos.append(f"faq {respuesta.faq_id} (se esperaba {caso['faq']})")
    if caso.get("documento") and caso["documento"] not in documentos:
        fallos.append(f"documento esperado {caso['documento']} no citado")
    libro_ok = None
    if caso.get("libro_titulo"):
        # un título o una lista de títulos válidos (basta con que aparezca uno)
        esperados = caso["libro_titulo"]
        esperados = [esperados] if isinstance(esperados, str) else esperados
        libro_ok = any(t in titulos for t in esperados)
        if not libro_ok:
            fallos.append(f"ningún libro esperado mostrado ({' / '.join(esperados)})")
    if (textos := caso.get("contiene")) and not any(t in respuesta.respuesta for t in textos):
        fallos.append(f"la respuesta no menciona {' ni '.join(textos)}")
    if (tope := caso.get("precio_max")) is not None:
        caros = [lb["titulo"] for lb in respuesta.libros if lb["precio"] > tope]
        if not respuesta.libros or caros:
            fallos.append(
                f"libros sobre el presupuesto de {tope}: {', '.join(caros) or 'ninguno mostrado'}"
            )
    if esperado := caso.get("carrito"):
        # el pedido que tocó el último turno: estado y cantidad de libros
        carrito = respuesta.carrito or {}
        if carrito.get("estado") not in esperado["estado"]:
            fallos.append(
                f"pedido {carrito.get('estado')} (se esperaba {'/'.join(esperado['estado'])})"
            )
        if "unidades" in esperado and carrito.get("unidades") != esperado["unidades"]:
            fallos.append(
                f"{carrito.get('unidades')} libros en el pedido "
                f"(se esperaban {esperado['unidades']})"
            )
    return {
        "id": caso["id"],
        "categoria": caso["categoria"],
        "ruta": respuesta.ruta,
        "ok": not fallos,
        "detalle": "; ".join(fallos),
        "latencia_ms": respuesta.latencia_ms,
        "tokens": sum(respuesta.tokens),
        "respuesta": respuesta.respuesta,
        "libro_ok": libro_ok,
    }


def informe(resultados: list[dict[str, Any]], omitidos: list[str], modo: str) -> str:
    evaluados = len(resultados)
    aciertos = sum(r["ok"] for r in resultados)
    latencias = sorted(r["latencia_ms"] for r in resultados) or [0]
    por_categoria: dict[str, list[bool]] = defaultdict(list)
    for r in resultados:
        por_categoria[r["categoria"]].append(r["ok"])
    con_libro = [r["libro_ok"] for r in resultados if r["libro_ok"] is not None]

    lineas = [
        f"# Evaluación del {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"- Modo: {modo}",
        f"- Casos evaluados: {evaluados} (omitidos por falta de modelo: {len(omitidos)})",
        f"- Aciertos de ruta: {aciertos}/{evaluados} ({aciertos / max(evaluados, 1):.0%})",
        f"- Latencia p50: {statistics.median(latencias):.0f} ms · máxima: {latencias[-1]} ms",
        f"- Tokens totales: {sum(r['tokens'] for r in resultados)}",
        f"- Libro esperado entre los mostrados: {sum(con_libro)}/{len(con_libro)}",
        "",
        "| Categoría | Aciertos |",
        "|---|---|",
        *[f"| {c} | {sum(v)}/{len(v)} |" for c, v in sorted(por_categoria.items())],
        "",
        "| Caso | Ruta obtenida | Resultado | Latencia (ms) |",
        "|---|---|---|---|",
        *[
            f"| {r['id']} | {r['ruta']} | {'ok' if r['ok'] else 'falla: ' + r['detalle']} "
            f"| {r['latencia_ms']} |"
            for r in resultados
        ],
    ]
    if omitidos:
        lineas += ["", "Omitidos (requieren LLM): " + ", ".join(omitidos)]
    return "\n".join(lineas) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sin-llm", action="store_true", help="ignora el LLM del .env")
    parser.add_argument("--minimo", type=float, default=0.0, help="tasa mínima de aciertos")
    parser.add_argument("--casos", type=Path, default=RAIZ / "evaluacion" / "casos.json")
    args = parser.parse_args()

    casos = json.loads(args.casos.read_text(encoding="utf-8"))["casos"]
    with tempfile.TemporaryDirectory() as tmp:
        extra: dict[str, Any] = {
            "db_path": Path(tmp) / "eval.db",
            "log_archivo": Path(tmp) / "eval.jsonl",
            "log_consola": False,
            "limite_chat_por_minuto": 0,
        }
        if args.sin_llm:
            extra |= {"llm_base_url": "", "llm_2_base_url": ""}
        ajustes = Settings(**extra)
        registro = RegistroEventos.desde_config(ajustes)
        bd = BaseDatos(ajustes.db_path, ajustes.carpeta_datos / "catalogo.json")
        bd.inicializar()

        inicio = time.perf_counter()
        servicio = construir_servicio(ajustes, registro, bd)
        servicio.buscador.indexar()
        servicio.recomendador.indexar()
        catalogo = servicio.recomendador.info()
        print(
            f"Documentos: {len(servicio.buscador.chunks)} fragmentos ({servicio.buscador.motor}) · "
            f"catálogo: {catalogo['libros']} libros ({catalogo['motor']}) · "
            f"{time.perf_counter() - inicio:.1f} s"
        )
        hay_llm = servicio.llm.configurado
        modo = f"con LLM ({servicio.llm.modelo_principal})" if hay_llm else "sin LLM"

        resultados, omitidos = [], []
        for caso in casos:
            if caso.get("requiere_llm") and not hay_llm:
                omitidos.append(caso["id"])
                continue
            resultado = evaluar_caso(servicio, caso)
            print(f"{'ok   ' if resultado['ok'] else 'FALLA'} {caso['id']:<24} {resultado['ruta']}")
            resultados.append(resultado)

    texto = informe(resultados, omitidos, modo)
    carpeta = RAIZ / "evaluacion" / "resultados"
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"{datetime.now():%Y%m%d-%H%M%S}.md"
    destino.write_text(texto, encoding="utf-8")
    print("\n" + texto + f"\nInforme guardado en {destino.relative_to(RAIZ)}")

    tasa = sum(r["ok"] for r in resultados) / max(len(resultados), 1)
    return 0 if tasa >= args.minimo else 1


if __name__ == "__main__":
    raise SystemExit(main())
