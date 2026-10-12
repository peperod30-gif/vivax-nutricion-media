"""Estado del router en la nube: la base viaja cifrada en una rama huérfana del repositorio de medios.

El router de Vivax Nutrición guarda lo que decide (qué piezas hay, cuáles aprobó José y a qué hora, sus decisiones y avisos) en dos bases
SQLite y un par de ficheros. En la nube no hay disco que dure entre ejecuciones, así que cada ejecución:

    1. baja la rama `estado` (o `estado-prueba`) y descifra `router_datos.tar.enc` en una carpeta de trabajo,
    2. ejecuta una pasada del router,
    3. si algo cambió de verdad (huella lógica distinta), cifra otra vez y SOBRESCRIBE la rama con un solo commit (push --force):
       el repositorio no crece aunque la base cambie cien veces al día.

El cifrado es el de `openssl enc -aes-256-cbc -pbkdf2 -salt` con la clave en la variable VIVAX_CLAVE_ESTADO (secreto de Actions y línea de
router/.env.nube en el PC). La rama es pública porque el repositorio lo es (Instagram necesita descargar las imágenes), por eso va cifrada.

Uso (lo llama el workflow, y también sirve a mano):
    python -m nube.estado bajar --rama estado --datos <carpeta>
    python -m nube.estado subir --rama estado --datos <carpeta>
    python -m nube.estado huella --datos <carpeta>
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = "peperod30-gif/vivax-nutricion-media"
ARCHIVO = "router_datos.tar.enc"
FICHEROS = ("hub.sqlite", "cuentas/n1/estado.sqlite", "cuentas/n1/instagram.json", "token_ig.txt",
            "validaciones/n1.json", "validaciones/n1.log.jsonl")      # lo que José valide desde Telegram también tiene que sobrevivir
VOLATIL = ("router_latido",)          # lo que el router escribe en cada vuelta y no es un cambio de verdad


class ErrorEstado(Exception):
    pass


# ------------------------------------------------------------------------------------------------ cifrado
def _openssl() -> str:
    exe = shutil.which("openssl")
    if exe:
        return exe
    for c in (r"C:\Program Files\Git\usr\bin\openssl.exe", r"C:\Program Files\Git\mingw64\bin\openssl.exe"):
        if Path(c).is_file():
            return c
    raise ErrorEstado("No encuentro openssl.")


def _clave() -> str:
    c = os.environ.get("VIVAX_CLAVE_ESTADO", "")
    if len(c) < 16:
        raise ErrorEstado("Falta VIVAX_CLAVE_ESTADO (o es demasiado corta).")
    return c


def cifrar(datos: bytes) -> bytes:
    """AES-256-CBC con PBKDF2 y sal; la clave va por variable de entorno, nunca en la línea de órdenes."""
    p = subprocess.run([_openssl(), "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-pass", "env:VIVAX_CLAVE_ESTADO"], input=datos,
                       capture_output=True, env={**os.environ, "VIVAX_CLAVE_ESTADO": _clave()}, check=False)
    if p.returncode != 0:
        raise ErrorEstado("openssl no pudo cifrar el estado.")
    return p.stdout


def descifrar(datos: bytes) -> bytes:
    p = subprocess.run([_openssl(), "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-pass", "env:VIVAX_CLAVE_ESTADO"], input=datos,
                       capture_output=True, env={**os.environ, "VIVAX_CLAVE_ESTADO": _clave()}, check=False)
    if p.returncode != 0:
        raise ErrorEstado("No se pudo descifrar el estado: ¿la clave VIVAX_CLAVE_ESTADO es la buena?")
    return p.stdout


# ------------------------------------------------------------------------------------------------ paquete
def copia_consistente(origen: Path, destino: Path) -> None:
    """Copia una base SQLite con la API de copia de seguridad (consistente aunque alguien la esté usando)."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    a = sqlite3.connect(f"file:{origen.as_posix()}?mode=ro", uri=True)
    b = sqlite3.connect(str(destino))
    try:
        a.backup(b)
    finally:
        b.close()
        a.close()


def empaquetar(datos: Path) -> bytes:
    """tar.gz cifrado con las bases y los ficheros de estado que existan en `datos`."""
    with tempfile.TemporaryDirectory(prefix="vivax_estado_") as tmp:
        tmp = Path(tmp)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for nombre in FICHEROS:
                f = Path(datos) / nombre
                if not f.is_file():
                    continue
                if nombre.endswith(".sqlite"):
                    copia = tmp / nombre
                    copia_consistente(f, copia)
                    f = copia
                tar.add(f, arcname=nombre)
        return cifrar(buf.getvalue())


def desempaquetar(cifrado: bytes, datos: Path) -> list[str]:
    """Deja en `datos` los ficheros del paquete. Solo acepta los nombres esperados (nada de rutas absolutas ni «..»)."""
    plano = descifrar(cifrado)
    hechos: list[str] = []
    datos = Path(datos)
    with tarfile.open(fileobj=io.BytesIO(plano), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.name not in FICHEROS or not m.isreg():
                raise ErrorEstado(f"El paquete de estado trae algo inesperado: {m.name!r}")
            destino = datos / m.name
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(tar.extractfile(m).read())
            hechos.append(m.name)
    return hechos


# ------------------------------------------------------------------------------------------------ huella lógica
def _volcado(ruta: Path):
    con = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True)
    try:
        for linea in con.iterdump():
            if any(f"'{v}'" in linea for v in VOLATIL):
                continue
            yield linea
    finally:
        con.close()


def huella(datos: Path) -> str:
    """Resumen del contenido lógico del estado: dos copias con los mismos datos dan la misma huella aunque los ficheros difieran byte a byte."""
    h = hashlib.sha256()
    for nombre in FICHEROS:
        f = Path(datos) / nombre
        h.update(nombre.encode() + b"\0")
        if not f.is_file():
            h.update(b"(ausente)")
            continue
        if nombre.endswith(".sqlite"):
            for linea in _volcado(f):
                h.update(linea.encode("utf-8") + b"\n")
        else:
            h.update(f.read_bytes())
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------ rutas de las piezas
def reescribir_rutas(estado_sqlite: Path, carpeta_piezas: Path) -> int:
    """`ruta_json` guarda rutas absolutas de la máquina que importó la pieza. Aquí se cambian por las del repositorio
    (`<carpeta_piezas>/<pieza_id>.json`) cuando ese fichero existe. Devuelve cuántas filas cambiaron."""
    estado_sqlite = Path(estado_sqlite)
    if not estado_sqlite.is_file():
        return 0
    con = sqlite3.connect(str(estado_sqlite))
    cambiadas = 0
    try:
        for pid, ruta in con.execute("SELECT pieza_id, ruta_json FROM piezas").fetchall():
            nueva = Path(carpeta_piezas) / f"{pid}.json"
            if nueva.is_file() and str(nueva.resolve()) != ruta:
                con.execute("UPDATE piezas SET ruta_json=? WHERE pieza_id=?", (str(nueva.resolve()), pid))
                cambiadas += 1
        con.commit()
    finally:
        con.close()
    return cambiadas


# ------------------------------------------------------------------------------------------------ rama de git
def token_de_gh() -> str | None:
    """El token de la sesión de gh del PC (solo para empujar a la rama de estado desde aquí; no se imprime)."""
    try:
        p = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    t = (p.stdout or "").strip()
    return t or None


def _git(args: list[str], cwd: Path, token: str | None = None, entrada: bytes | None = None) -> subprocess.CompletedProcess:
    pre: list[str] = ["-c", "credential.helper="] if token else []
    if token:
        b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        pre += ["-c", f"http.extraheader=AUTHORIZATION: basic {b64}"]
    entorno = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *pre, *args], cwd=str(cwd), capture_output=True, input=entrada, env=entorno, check=False)


def _url(repo: str) -> str:
    return f"https://github.com/{repo}.git"


def _token_para_git() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or token_de_gh()


def traer_rama(rama: str, repo: str = REPO, token: str | None = None) -> bytes | None:
    """El contenido cifrado de la rama de estado, o None si la rama no existe todavía."""
    token = token or _token_para_git()
    with tempfile.TemporaryDirectory(prefix="vivax_rama_") as tmp:
        tmp = Path(tmp)
        _git(["init", "-q"], tmp)
        ls = _git(["ls-remote", "--exit-code", "--heads", _url(repo), f"refs/heads/{rama}"], tmp, token)
        if ls.returncode == 2:
            return None
        if ls.returncode != 0:
            raise ErrorEstado(f"No pude consultar la rama {rama} (git ls-remote, código {ls.returncode}).")
        f = _git(["fetch", "-q", "--depth", "1", _url(repo), f"refs/heads/{rama}"], tmp, token)
        if f.returncode != 0:
            raise ErrorEstado(f"No pude bajar la rama {rama} (git fetch, código {f.returncode}).")
        s = _git(["show", f"FETCH_HEAD:{ARCHIVO}"], tmp)
        if s.returncode != 0:
            raise ErrorEstado(f"La rama {rama} no trae {ARCHIVO}.")
        return s.stdout


def subir_rama(rama: str, cifrado: bytes, mensaje: str = "estado", repo: str = REPO, token: str | None = None) -> None:
    """Sobrescribe la rama con un único commit que solo contiene el paquete cifrado."""
    token = token or _token_para_git()
    with tempfile.TemporaryDirectory(prefix="vivax_rama_") as tmp:
        tmp = Path(tmp)
        for a in (["init", "-q", "-b", rama], ["config", "user.name", "Vivax Router"], ["config", "user.email", "vivax-router@users.noreply.github.com"]):
            _git(a, tmp)
        (tmp / ARCHIVO).write_bytes(cifrado)
        (tmp / "LEEME.txt").write_text("Estado cifrado del router de Vivax Nutricion. No contiene nada legible sin la clave.\n", encoding="utf-8")
        _git(["add", "-A"], tmp)
        c = _git(["commit", "-q", "-m", mensaje], tmp)
        if c.returncode != 0:
            raise ErrorEstado("git commit del estado falló.")
        for intento in range(3):
            p = _git(["push", "-q", "--force", _url(repo), f"HEAD:refs/heads/{rama}"], tmp, token)
            if p.returncode == 0:
                return
        raise ErrorEstado(f"No pude subir la rama {rama} (git push, código {p.returncode}).")


# ------------------------------------------------------------------------------------------------ órdenes
def _huella_previa(datos: Path) -> Path:
    return Path(str(datos).rstrip("/\\") + ".huella")


def bajar(rama: str, datos: Path, exigir: bool = False) -> None:
    """Baja y descifra el estado. Con `exigir`, una rama que no existe es un error (el router de verdad nunca empieza de cero)."""
    datos = Path(datos)
    shutil.rmtree(datos, ignore_errors=True)
    datos.mkdir(parents=True, exist_ok=True)
    cifrado = traer_rama(rama)
    if cifrado is None and exigir:
        raise ErrorEstado(f"La rama {rama} no existe: no se ha migrado el estado (migrar_a_nube.py --definitivo). No arranco con una base vacía.")
    if cifrado is None:
        print(f"La rama {rama} no existe todavía: empiezo con un estado vacío.", file=sys.stderr)
    else:
        hechos = desempaquetar(cifrado, datos)
        print(f"Estado descifrado de {rama}: {len(hechos)} ficheros.", file=sys.stderr)
    _huella_previa(datos).write_text(huella(datos), encoding="utf-8")


def subir(rama: str, datos: Path) -> bool:
    datos = Path(datos)
    antes = _huella_previa(datos).read_text(encoding="utf-8") if _huella_previa(datos).is_file() else None
    ahora = huella(datos)
    if antes == ahora:
        print("El estado no ha cambiado: no subo nada.", file=sys.stderr)
        return False
    subir_rama(rama, empaquetar(datos), mensaje="estado")
    _huella_previa(datos).write_text(ahora, encoding="utf-8")
    print(f"Estado cifrado y subido a {rama}.", file=sys.stderr)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nube.estado")
    ap.add_argument("orden", choices=["bajar", "subir", "huella"])
    ap.add_argument("--rama", default="estado")
    ap.add_argument("--datos", required=True, type=Path)
    ap.add_argument("--exigir", action="store_true", help="bajar: error si la rama no existe (modo real)")
    a = ap.parse_args(argv)
    try:
        if a.orden == "bajar":
            bajar(a.rama, a.datos, a.exigir)
        elif a.orden == "subir":
            subir(a.rama, a.datos)
        else:
            print(huella(a.datos))
    except ErrorEstado as exc:
        print(f"Error de estado: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
