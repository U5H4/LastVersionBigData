"""PARTE II - ETAPA EXTRACT.

Extrae las tres fuentes crudas y produce la EVIDENCIA de extraccion que pide
el punto 6 del enunciado: fuente, formato, cantidad de registros, columnas y
tipos de datos.

Principio clave: TODO se lee como texto (dtype=str). Si Pandas infiriera tipos
al leer, el ETL no podria demostrar que sabe corregir "precios almacenados como
texto" ni "fechas en diferentes formatos". La inferencia se hace despues, en
transform, de forma explicita y auditable.
"""

from __future__ import annotations

import json
import os
import re
from urllib.parse import parse_qs, urlencode, urlparse
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from config import RAW_CSV, RAW_JSON, RAW_SHEETS, REPORTS
from utils import asegurar_dir, subtitulo, tabla, titulo

# Tokens que representan "sin dato" en las distintas fuentes.
NULOS = {"", "n/a", "na", "null", "none", "-", "nan", "#valor!", "#n/a", "nd", "consultar"}

_RE_ENTERO = re.compile(r"^[+-]?\d+$")
_RE_DECIMAL = re.compile(r"^[+-]?(\d{1,3}(,\d{3})+|\d+)[.]\d+$|^[+-]?\d+[.,]\d+$")
_RE_MONEDA = re.compile(r"^\s*[\$\€£]?\s*[\d.,\s]+(\s*(MXN|USD|usd))?\s*$")
_RE_FECHA = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2}"
    r"|\d{2}/\d{2}/\d{4}"
    r"|\d{2}-\d{2}-\d{4}"
    r"|\d{1,2}\s+de\s+\w+\s+de\s+\d{4}"
    r"|\d{4}/\d{2}/\d{2})"
)
_RE_BOOL = re.compile(r"^(true|false|si|no|sí|1|0|yes|y)$", re.IGNORECASE)


# ==========================================================================
# Descriptor de una fuente (evidencia del punto 6)
# ==========================================================================
@dataclass
class Descriptor:
    clave: str
    fuente: str            # A | B | C
    sistema_origen: str    # tienda / app / intranet / Google Sheets ...
    nombre: str
    formato: str
    ruta: Path
    registros: int
    columnas: int
    tipos: dict[str, str] = field(default_factory=dict)
    nulos: int = 0
    duplicados: int = 0
    muestra: list[str] = field(default_factory=list)


def inferir_tipo(serie: pd.Series) -> str:
    """Determina el tipo logico de una columna a partir de sus valores crudos."""
    valores = [str(v).strip() for v in serie.dropna().tolist()]
    valores = [v for v in valores if v.lower() not in NULOS]
    if not valores:
        return "vacio"

    muestra = valores[: min(len(valores), 400)]

    def todos(patron) -> bool:
        return all(patron.match(v) for v in muestra)

    if todos(_RE_ENTERO):
        return "entero"
    if todos(_RE_FECHA):
        return "fecha_texto"
    if todos(_RE_MONEDA):
        return "numero_texto"
    if todos(_RE_BOOL):
        return "booleano_texto"
    if todos(_RE_DECIMAL):
        return "decimal_texto"
    longitudes = {len(v) for v in muestra}
    if len(longitudes) == 1 and todas_mayusculas_o_digitos(muestra):
        return "codigo"
    return "texto"


def todas_mayusculas_o_digitos(muestra: list[str]) -> bool:
    for v in muestra:
        if v.isupper():
            continue
        if v.replace(".", "").replace("-", "").isdigit():
            continue
        return False
    return True


# ==========================================================================
# Extractores
# ==========================================================================
def leer_csv(ruta: Path) -> pd.DataFrame:
    """CSV delimitado por coma, leido como texto y con nulos ya reconocidos."""
    return pd.read_csv(
        ruta,
        dtype=str,
        keep_default_na=False,
        na_values=list(NULOS),
        encoding="utf-8",
        sep=",",
    )


def leer_json_documentos(ruta: Path) -> pd.DataFrame:
    """JSON array de objetos -> DataFrame plano (json_normalize aplana el anidado).

    El aplanado conserva los campos anidados con notacion de punto
    (cliente.nombre) y, en productos, serializa el sub-objeto `atributos`
    como JSON para no perder la estructura variable.
    """
    documentos = json.loads(ruta.read_text(encoding="utf-8"))
    df = pd.json_normalize(documentos, sep=".")
    # Toda columna debe quedar ESCALAR: listas y dicts se serializan a JSON.
    # Si no, operaciones como duplicated()/groupby() fallan por "unhashable list".
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, (list, dict))).any():
            df[col] = df[col].map(
                lambda v: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            )
    return df.replace({"": None})


def leer_sheet(ruta: Path) -> pd.DataFrame:
    """Lee la copia XLSX local; sirve como fallback reproducible."""
    return pd.read_excel(ruta, dtype=str, keep_default_na=False, na_values=list(NULOS))


# La Fuente C puede leerse DIRECTAMENTE de Google Sheets si se proporciona
# GOOGLE_SHEETS_<CLAVE>_URL en .env (sin tocar las otras ocho fuentes).
# Requiere permiso de lectura por enlace, NO permisos de edicion.
SHEETS_REQUERIDOS = {
    "sucursales": {"id_sucursal", "nombre", "ciudad", "region"},
    "proveedores": {"id_proveedor", "nombre", "rubro"},
    "promociones": {"id_promocion", "nombre", "descuento_pct"},
    "objetivos": {"anio", "mes", "categoria", "meta_ingresos"},
}


def url_exportacion_sheet(url: str) -> str:
    """Convierte una URL de hoja de Google al endpoint CSV público.

    Acepta enlaces edit/view compartidos y enlaces export?format=csv. Si el
    documento tiene varias pestañas, el gid identifica la pestaña correcta.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname != "docs.google.com":
        raise ValueError("Google Sheets requiere un enlace HTTPS de docs.google.com")
    match = re.match(r"^/spreadsheets/d/([A-Za-z0-9_-]+)(?:/|$)", parsed.path)
    if not match:
        raise ValueError("Enlace de Google Sheets invalido: falta ID de hoja")
    params = parse_qs(parsed.query)
    frag_params = parse_qs(parsed.fragment)
    gid = (params.get("gid") or frag_params.get("gid") or ["0"])[0]
    if not gid.isdigit():
        raise ValueError("El gid debe ser numerico")
    return f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?{urlencode({'format':'csv','gid':gid})}"


def leer_google_sheet(url: str, clave: str) -> pd.DataFrame:
    """Lee desde el servidor Google y valida encabezados antes de continuar."""
    export_url = url_exportacion_sheet(url)
    try:
        df = pd.read_csv(export_url, dtype=str, keep_default_na=False,
                         na_values=list(NULOS))
    except Exception as exc:
        raise RuntimeError(
            f"No pude leer Google Sheets para {clave}. Verifica permisos de lectura "
            "del enlace, red e identificador de pestaña (gid)."
        ) from exc
    faltan = SHEETS_REQUERIDOS[clave] - set(df.columns)
    if faltan:
        raise ValueError(f"Google Sheets ({clave}) no contiene columnas: {sorted(faltan)}")
    return df


# ==========================================================================
# Extraccion de las 3 fuentes
# ==========================================================================
# (clave, fuente, sistema_origen, ruta, lector, formato)
PLAN_EXTRACCION = [
    ("clientes", "A", "Tienda fisica + App movil", RAW_CSV / "clientes.csv", leer_csv, "CSV"),
    ("productos", "A", "Sistema de inventario", RAW_CSV / "productos.csv", leer_csv, "CSV"),
    ("pedidos", "A", "Punto de venta (web/tienda)", RAW_CSV / "pedidos.csv", leer_csv, "CSV"),
    ("detalle_pedido", "A", "Punto de venta (web/tienda)", RAW_CSV / "detalle_pedido.csv", leer_csv, "CSV"),
    ("pagos", "A", "Pasarela de pagos", RAW_CSV / "pagos.csv", leer_csv, "CSV"),
    ("productos_semi", "B", "Catalogo de producto (API interna)", RAW_JSON / "productos_semiestructurados.json", leer_json_documentos, "JSON"),
    ("resenas", "B", "App movil / Web", RAW_JSON / "resenas.json", leer_json_documentos, "JSON"),
    ("actividad", "B", "Logs de aplicacion", RAW_JSON / "actividad_usuario.json", leer_json_documentos, "JSON"),
    ("sucursales", "C", "Google Sheets - Direccion de Operaciones", RAW_SHEETS / "sucursales.xlsx", leer_sheet, "XLSX (Google Sheets)"),
    ("proveedores", "C", "Google Sheets - Compras", RAW_SHEETS / "proveedores.xlsx", leer_sheet, "XLSX (Google Sheets)"),
    ("promociones", "C", "Google Sheets - Marketing", RAW_SHEETS / "promociones.xlsx", leer_sheet, "XLSX (Google Sheets)"),
    ("objetivos", "C", "Google Sheets - Direccion Comercial", RAW_SHEETS / "objetivos_venta.xlsx", leer_sheet, "XLSX (Google Sheets)"),
]


def extraer(datos: dict[str, pd.DataFrame] | None = None) -> dict[str, pd.DataFrame]:
    """Ejecuta la extraccion completa y devuelve los DataFrames crudos."""
    print(titulo("PARTE II / ETAPA 1 - EXTRACT"))
    print("  Regla: todo se lee como TEXTO. Ninguna conversion implicita.")
    print("  Motivo: las conversiones se documentan una por una en la etapa Transform.\n")

    descriptors: list[Descriptor] = []
    resultado: dict[str, pd.DataFrame] = {}

    for clave, fuente, sistema, ruta, lector, formato in PLAN_EXTRACCION:
        enlace_sheet = (os.getenv(f"GOOGLE_SHEETS_{clave.upper()}_URL", "").strip()
                        if fuente == "C" else "")
        if datos is not None and clave in datos:
            df = datos[clave]
            if fuente == "C":
                sistema = "Fuente suministrada al ETL"
                formato = "DataFrame proporcionado"
        elif enlace_sheet:
            # No se permite fallback silencioso si el enlace de Google falla:
            # seria incorrecto declarar Google Sheets cuando se leyo XLSX.
            df = leer_google_sheet(enlace_sheet, clave)
            sistema = "Google Sheets (lectura directa HTTPS)"
            formato = "CSV remoto de Google Sheets"
        else:
            if not ruta.exists():
                raise FileNotFoundError(
                    f"No se encontro {ruta}. Ejecuta primero:  python src/generate_sources.py"
                )
            df = lector(ruta)
            if fuente == "C":
                sistema = "Archivo Excel local (compatible con Google Sheets)"
                formato = "XLSX local"

        tipos = {c: inferir_tipo(df[c]) for c in df.columns}
        d = Descriptor(
            clave=clave,
            fuente=fuente,
            sistema_origen=sistema,
            nombre=ruta.name,
            formato=formato,
            ruta=ruta,
            registros=len(df),
            columnas=df.shape[1],
            tipos=tipos,
            nulos=int(df.isna().sum().sum()),
            duplicados=int(df.duplicated().sum()),
            muestra=[str(v) for v in df.iloc[0].tolist()[:6]],
        )
        descriptors.append(d)
        resultado[clave] = df

    _imprimir_evidencia(descriptors)
    _guardar_evidencia(descriptors)
    return resultado


def _imprimir_evidencia(des: list[Descriptor]) -> None:
    print(subtitulo("EVIDENCIA DE EXTRACCION (punto 6)"))
    resumen = pd.DataFrame(
        [
            {
                "Fuente": d.fuente,
                "Archivo": d.nombre,
                "Formato": d.formato,
                "Sistema origen": d.sistema_origen,
                "Registros": d.registros,
                "Columnas": d.columnas,
                "Nulos": d.nulos,
                "Duplicados": d.duplicados,
            }
            for d in des
        ]
    )
    print(tabla(resumen))

    print("\n  Tipos inferidos por columna (muestra de las 3 fuentes mas relevantes):\n")
    for d in [des[0], des[5], des[8]]:
        print(f"  [{d.fuente}] {d.nombre}  ({d.registros:,} registros)")
        for col, tipo in d.tipos.items():
            print(f"      {col:<32} -> {tipo}")
        print()


def _guardar_evidencia(des: list[Descriptor]) -> None:
    asegurar_dir(REPORTS)

    # Evidencia larga: una fila por columna
    filas = []
    for d in des:
        for col, tipo in d.tipos.items():
            filas.append(
                {
                    "fuente": d.fuente,
                    "archivo": d.nombre,
                    "formato": d.formato,
                    "sistema_origen": d.sistema_origen,
                    "registros": d.registros,
                    "columnas": d.columnas,
                    "columna": col,
                    "tipo_inferido": tipo,
                }
            )
    pd.DataFrame(filas).to_csv(
        REPORTS / "evidencia_extraccion_columnas.csv", index=False, encoding="utf-8-sig"
    )

    # Evidencia resumida
    pd.DataFrame(
        [
            {
                "fuente": d.fuente,
                "archivo": d.nombre,
                "formato": d.formato,
                "sistema_origen": d.sistema_origen,
                "registros": d.registros,
                "columnas": d.columnas,
                "nulos": d.nulos,
                "duplicados_exactos": d.duplicados,
                "muestra_valores_crudos": " | ".join(d.muestra),
            }
            for d in des
        ]
    ).to_csv(REPORTS / "evidencia_extraccion.csv", index=False, encoding="utf-8-sig")

    print(f"  Evidencia guardada en:")
    print(f"     {REPORTS / 'evidencia_extraccion.csv'}")
    print(f"     {REPORTS / 'evidencia_extraccion_columnas.csv'}")


if __name__ == "__main__":
    extraer()
