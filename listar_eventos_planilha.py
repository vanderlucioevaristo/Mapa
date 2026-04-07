#!/usr/bin/env python3
"""Ler e listar registros de eventos a partir de um arquivo CSV local."""

from __future__ import annotations

import argparse
import csv
import json
import re
import ssl
import sys
import unicodedata
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen


DEFAULT_CSV_PATH = "eventos_bh.csv"


def extrair_sheet_id(url: str) -> str:
    """Extrai o ID da planilha de uma URL do Google Sheets."""
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not match:
        raise ValueError("Nao foi possivel extrair o ID da planilha da URL informada.")
    return match.group(1)


def extrair_gid(url: str) -> str | None:
    """Extrai o gid da aba, quando presente na URL."""
    parsed = urlparse(url)
    params = parse_qs(parsed.fragment)
    gids = params.get("gid")
    if gids:
        return gids[0]
    return None


def montar_url_csv(sheet_id: str, gid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def criar_contexto_ssl(insecure: bool) -> ssl.SSLContext:
    if insecure:
        return ssl._create_unverified_context()

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def ler_registros(csv_url: str, contexto_ssl: ssl.SSLContext) -> Iterable[dict[str, str]]:
    try:
        with urlopen(csv_url, timeout=20, context=contexto_ssl) as resposta:
            conteudo = resposta.read().decode("utf-8-sig")
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise RuntimeError(
                "A planilha parece nao estar publica para leitura. "
                "No Google Sheets, ative o compartilhamento para 'Qualquer pessoa com o link'."
            ) from exc
        if exc.code == 400:
            raise RuntimeError(
                "Falha HTTP ao acessar a planilha: 400. "
                "Verifique se o gid da aba existe e se a URL da planilha esta correta."
            ) from exc
        raise RuntimeError(f"Falha HTTP ao acessar a planilha: {exc.code}") from exc
    except ssl.SSLCertVerificationError as exc:
        raise RuntimeError(
            "Falha na validacao SSL. Tente instalar certifi (pip install certifi) "
            "ou execute com --insecure para teste local."
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Erro de rede ao acessar a planilha: {exc.reason}") from exc

    linhas = conteudo.splitlines()
    leitor = csv.DictReader(linhas)
    return list(leitor)


def ler_registros_csv_local(caminho_csv: str) -> Iterable[dict[str, str]]:
    try:
        with open(caminho_csv, "r", encoding="utf-8-sig") as arquivo:
            conteudo = arquivo.read()
    except OSError as exc:
        raise RuntimeError(f"Falha ao ler o arquivo CSV local: {exc}") from exc

    linhas = conteudo.splitlines()
    if not linhas:
        return []

    delimitador = ";" if linhas[0].count(";") > linhas[0].count(",") else ","
    leitor = csv.DictReader(linhas, delimiter=delimitador)
    return list(leitor)


def normalizar_nome_coluna(nome: str) -> str:
    sem_acentos = "".join(
        ch for ch in unicodedata.normalize("NFD", nome) if unicodedata.category(ch) != "Mn"
    )
    return re.sub(r"[^A-Z0-9]", "", sem_acentos.upper())


def padronizar_registros(registros: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    colunas_canonicas = {
        "DATA": "DATA",
        "DATAFINAL": "DATAFINAL",
        "DATAFIM": "DATAFINAL",
        "DESCRICAO": "DESCRICAO",
        "ENDERECO": "ENDERECO",
        "NOMLOCAL": "NOMLOCAL",
        "NOMELOCAL": "NOMLOCAL",
        "ESTIMATIVAPUBLICO": "ESTIMATIVA PUBLICO",
    }

    saida: list[dict[str, str]] = []
    for registro in registros:
        padrao = {
            "DATA": "",
            "DATAFINAL": "",
            "DESCRICAO": "",
            "ENDERECO": "",
            "NOMLOCAL": "",
            "ESTIMATIVA PUBLICO": "",
        }
        for chave, valor in registro.items():
            chave_norm = normalizar_nome_coluna(chave)
            destino = colunas_canonicas.get(chave_norm)
            if destino:
                padrao[destino] = (valor or "").strip()
        saida.append(padrao)
    return saida


def imprimir_registros(registros: Iterable[dict[str, str]]) -> None:
    registros = list(registros)
    if not registros:
        print("Nenhum registro encontrado.")
        return

    colunas_alvo = [
        "DATA",
        "DATAFINAL",
        "DESCRICAO",
        "ENDERECO",
        "NOMLOCAL",
        "ESTIMATIVA PUBLICO",
    ]

    print(f"Total de registros: {len(registros)}")
    print("-" * 60)

    for i, linha in enumerate(registros, start=1):
        print(f"Registro {i}:")
        for coluna in colunas_alvo:
            valor = linha.get(coluna, "").strip()
            print(f"  {coluna}: {valor}")
        print("-" * 60)


def salvar_json(registros: Iterable[dict[str, str]], caminho_saida: str) -> None:
    registros = list(registros)
    with open(caminho_saida, "w", encoding="utf-8") as arquivo:
        json.dump(registros, arquivo, ensure_ascii=False, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Le um arquivo CSV local e lista os registros."
    )
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=DEFAULT_CSV_PATH,
        help="Caminho do arquivo CSV local com os eventos.",
    )
    parser.add_argument(
        "--json-output",
        default=None,
        help="Caminho do arquivo para salvar os registros em JSON (opcional).",
    )

    args = parser.parse_args()

    try:
        registros = padronizar_registros(ler_registros_csv_local(args.csv_path))
        imprimir_registros(registros)
        if args.json_output:
            salvar_json(registros, args.json_output)
            print(f"JSON salvo em: {args.json_output}")
    except RuntimeError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
