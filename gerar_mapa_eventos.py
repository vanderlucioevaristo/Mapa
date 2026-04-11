#!/usr/bin/env python3
"""Gerar uma pagina HTML com mapa dos eventos lidos de um CSV local."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import math
import statistics
import sys
import time
import unicodedata
from html import escape
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from listar_eventos_planilha import (
    criar_contexto_ssl,
  ler_registros_csv_local,
    padronizar_registros,
    salvar_json,
)


DEFAULT_CSV_PATH = "eventos_bh.csv"
DEFAULT_LAT = -19.9167
DEFAULT_LON = -43.9345
SPREADSHEET_ID = "1BuXBFWZ396pSujh5ERjpDpAr850lGfZwqwzXquIPzgU"
CSV_FIELDS = [
  "DATA",
  "DATAFINAL",
  "DESCRICAO",
  "ENDERECO",
  "ESTIMATIVAPUBLICO",
  "NOMELOCAL",
  "RESPONSAVEL",
]


class GeocodingRateLimitError(RuntimeError):
    """Sinaliza que o servico de geocodificacao recusou a consulta por limite de taxa."""


def normalizar_nome_coluna(nome: str) -> str:
  sem_acentos = remover_acentos(nome or "")
  return "".join(ch for ch in sem_acentos.upper() if ch.isalnum())


def montar_url_planilha_csv(sheet_id: str) -> str:
  return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"


def normalizar_data_ddmmyyyy(valor: str) -> str:
  texto = (valor or "").strip()
  if not texto:
    return ""

  formatos_entrada = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
  ]

  for formato in formatos_entrada:
    try:
      data = dt.datetime.strptime(texto, formato)
      return data.strftime("%d/%m/%Y")
    except ValueError:
      continue

  return texto


def sincronizar_csv_com_planilha(caminho_csv: str, contexto_ssl) -> int:
  url_csv = montar_url_planilha_csv(SPREADSHEET_ID)

  try:
    with urlopen(url_csv, timeout=30, context=contexto_ssl) as resposta:
      conteudo = resposta.read().decode("utf-8-sig")
  except HTTPError as exc:
    raise RuntimeError(f"Falha HTTP ao ler planilha: {exc.code}") from exc
  except URLError as exc:
    raise RuntimeError(f"Erro de rede ao ler planilha: {exc.reason}") from exc

  leitor = csv.DictReader(io.StringIO(conteudo))
  registros_saida: list[dict[str, str]] = []

  for linha in leitor:
    normalizada = {
      normalizar_nome_coluna(chave): (valor or "").strip()
      for chave, valor in linha.items()
      if chave is not None
    }

    registro = {
      "DATA": normalizar_data_ddmmyyyy(normalizada.get("DATAINICIO", "")),
      "DATAFINAL": normalizar_data_ddmmyyyy(normalizada.get("DATAFIM", "")),
      "DESCRICAO": normalizada.get("DESCRICAO", ""),
      "ENDERECO": normalizada.get("ENDERECOLOCAL", ""),
      "ESTIMATIVAPUBLICO": normalizada.get("ESTIMATIVAPUBLICO", ""),
      "NOMELOCAL": normalizada.get("LOCAL", ""),
      "RESPONSAVEL": normalizada.get("ENTIDADE", ""),
    }

    if not any(registro.values()):
      continue

    registros_saida.append(registro)

  if not registros_saida:
    raise RuntimeError("A planilha nao retornou registros para sincronizacao do CSV.")

  with open(caminho_csv, "w", encoding="utf-8", newline="") as arquivo:
    escritor = csv.DictWriter(
      arquivo,
      fieldnames=CSV_FIELDS,
      delimiter=";",
      lineterminator="\n",
    )
    escritor.writeheader()
    escritor.writerows(registros_saida)

  return len(registros_saida)


MESES_PT_BR = {
  1: "Janeiro",
  2: "Fevereiro",
  3: "Marco",
  4: "Abril",
  5: "Maio",
  6: "Junho",
  7: "Julho",
  8: "Agosto",
  9: "Setembro",
  10: "Outubro",
  11: "Novembro",
  12: "Dezembro",
}

ABREV_MES_PARA_NUMERO = {
  "JAN": 1,
  "FEV": 2,
  "MAR": 3,
  "ABR": 4,
  "MAI": 5,
  "JUN": 6,
  "JUL": 7,
  "AGO": 8,
  "SET": 9,
  "OUT": 10,
  "NOV": 11,
  "DEZ": 12,
}


def parse_data_evento(texto: str) -> dt.datetime | None:
  if not texto:
    return None

  formatos = [
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y-%m-%d",
  ]
  for formato in formatos:
    try:
      return dt.datetime.strptime(texto, formato)
    except ValueError:
      continue

  partes = [parte.strip() for parte in texto.replace("-", "/").split("/")]
  if len(partes) == 3 and partes[0].isdigit() and partes[2].isdigit():
    mes = ABREV_MES_PARA_NUMERO.get(remover_acentos(partes[1]).upper()[:3])
    if mes:
      try:
        return dt.datetime(int(partes[2]), mes, int(partes[0]))
      except ValueError:
        return None

  return None


def extrair_data_info(data: str) -> dict[str, object]:
  texto = (data or "").strip()
  data_evento = parse_data_evento(texto)
  if data_evento is None:
    return {
      "mes_numero": None,
      "mes_nome": "Data invalida",
      "ano_numero": None,
      "data_ordem": None,
    }

  return {
    "mes_numero": data_evento.month,
    "mes_nome": MESES_PT_BR[data_evento.month],
    "ano_numero": data_evento.year,
    "data_ordem": data_evento.strftime("%Y-%m-%d"),
  }


def iterar_meses_periodo(data_inicio: dt.datetime, data_final: dt.datetime) -> list[str]:
  ano = data_inicio.year
  mes = data_inicio.month
  fim_ano = data_final.year
  fim_mes = data_final.month
  chaves: list[str] = []

  while (ano, mes) <= (fim_ano, fim_mes):
    chaves.append(f"{ano:04d}-{mes:02d}")
    if mes == 12:
      ano += 1
      mes = 1
    else:
      mes += 1

  return chaves


def extrair_periodo_info(data_inicio: str, data_final: str) -> dict[str, object]:
  inicio_evento = parse_data_evento((data_inicio or "").strip())
  final_evento = parse_data_evento((data_final or "").strip())

  if inicio_evento is None and final_evento is None:
    return {
      "mes_numero": None,
      "mes_nome": "Data invalida",
      "ano_numero": None,
      "data_ordem": None,
      "data_final_ordem": None,
      "meses_ano_chaves": [],
    }

  if inicio_evento is None:
    inicio_evento = final_evento
  if final_evento is None or (inicio_evento is not None and final_evento < inicio_evento):
    final_evento = inicio_evento

  assert inicio_evento is not None
  assert final_evento is not None

  return {
    "mes_numero": inicio_evento.month,
    "mes_nome": MESES_PT_BR[inicio_evento.month],
    "ano_numero": inicio_evento.year,
    "data_ordem": inicio_evento.strftime("%Y-%m-%d"),
    "data_final_ordem": final_evento.strftime("%Y-%m-%d"),
    "meses_ano_chaves": iterar_meses_periodo(inicio_evento, final_evento),
  }


def extrair_publico(publico: str) -> int | None:
  numeros = "".join(ch for ch in publico if ch.isdigit())
  if not numeros:
    return None
  return int(numeros)


def calcular_raio_marcador(publico_valor: int | None) -> float:
  if publico_valor is None:
    return 8

  # Escala progressiva: aumenta com o publico, com limites para manter legibilidade.
  raio = 6 + (2 * math.log10(publico_valor + 1))
  return round(min(20, max(8, raio)), 1)

def classificar_publico(publico: str) -> dict[str, object]:
  valor = extrair_publico(publico)
  if valor is None:
    return {
      "publico_valor": None,
      "faixa_publico": "Nao informado",
      "cor_marcador": "#64748b",
      "raio_marcador": calcular_raio_marcador(valor),
    }

  if valor <= 1000:
    return {
      "publico_valor": valor,
      "faixa_publico": "Pequeno porte",
      "cor_marcador": "#2f855a",
      "raio_marcador": calcular_raio_marcador(valor),
    }

  if valor <= 5000:
    return {
      "publico_valor": valor,
      "faixa_publico": "Medio porte",
      "cor_marcador": "#d97706",
      "raio_marcador": calcular_raio_marcador(valor),
    }

  return {
    "publico_valor": valor,
    "faixa_publico": "Grande porte",
    "cor_marcador": "#c53030",
    "raio_marcador": calcular_raio_marcador(valor),
  }


def remover_acentos(texto: str) -> str:
  return "".join(
    ch for ch in unicodedata.normalize("NFD", texto) if unicodedata.category(ch) != "Mn"
  )


def gerar_consultas_endereco(endereco: str, nomlocal: str = "") -> list[str]:
  base = " ".join(endereco.strip().split())
  local_base = " ".join(nomlocal.strip().split())
  sem_acentos = remover_acentos(base)
  local_sem_acentos = remover_acentos(local_base)

  variacoes = [
    f"{local_base}, {base}" if local_base else "",
    f"{local_sem_acentos}, {sem_acentos}" if local_sem_acentos else "",
    f"{base}, {local_base}" if local_base else "",
    f"{sem_acentos}, {local_sem_acentos}" if local_sem_acentos else "",
    base,
    sem_acentos,
    base.replace("/", ", "),
    sem_acentos.replace("/", ", "),
    base.replace(" BAIRRO ", ", "),
    sem_acentos.replace(" BAIRRO ", ", "),
    base.replace(" BAIRRO ", ", ").replace("/", ", ") + ", Brasil",
    sem_acentos.replace(" BAIRRO ", ", ").replace("/", ", ") + ", Brasil",
  ]

  consultas: list[str] = []
  for item in variacoes:
    item_limpo = " ".join(item.split())
    if item_limpo and item_limpo not in consultas:
      consultas.append(item_limpo)
  return consultas


def montar_chave_cache(endereco: str, nomlocal: str = "") -> str:
    endereco_limpo = " ".join((endereco or "").strip().upper().split())
    local_limpo = " ".join((nomlocal or "").strip().upper().split())
    return f"{local_limpo}||{endereco_limpo}"


def consultar_nominatim(consulta: str, contexto_ssl) -> list[dict[str, object]]:
    params = urlencode(
        {
            "q": consulta,
            "format": "jsonv2",
            "limit": 1,
        }
    )
    request = Request(
        f"https://nominatim.openstreetmap.org/search?{params}",
        headers={"User-Agent": "mapa-eventos-local/1.0"},
    )

    try:
        with urlopen(request, timeout=30, context=contexto_ssl) as resposta:
            return json.loads(resposta.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 429:
            raise GeocodingRateLimitError(
                f"Limite de consultas atingido ao geocodificar endereco '{consulta}'."
            ) from exc
        raise RuntimeError(f"Falha HTTP ao geocodificar endereco '{consulta}': {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"Erro de rede ao geocodificar endereco '{consulta}': {exc.reason}") from exc


def carregar_cache_geocodificacao(caminho_json: str) -> dict[str, dict[str, object]]:
    try:
        with open(caminho_json, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(dados, list):
        return {}

    cache: dict[str, dict[str, object]] = {}
    for item in dados:
        if not isinstance(item, dict):
            continue

        endereco = str(item.get("ENDERECO", "")).strip()
        nomlocal = str(item.get("NOMLOCAL", "")).strip()
        if not endereco:
            continue

        geodados = {
            "latitude": item.get("latitude"),
            "longitude": item.get("longitude"),
            "endereco_encontrado": item.get("endereco_encontrado", ""),
            "geocodificado": bool(item.get("geocodificado")),
        }
        cache[montar_chave_cache(endereco, nomlocal)] = geodados

        chave_legada = montar_chave_cache(endereco, "")
        if chave_legada not in cache:
            cache[chave_legada] = geodados

    return cache


def carregar_registros_existentes(caminho_json: str) -> list[dict[str, object]]:
    try:
        with open(caminho_json, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(dados, list):
        return []

    saida: list[dict[str, object]] = []
    for item in dados:
        if isinstance(item, dict):
            saida.append(item)
    return saida


def buscar_coordenadas(
  endereco: str,
  nomlocal: str,
  contexto_ssl,
  cache: dict[str, dict[str, object]],
) -> dict[str, object]:
  chave = montar_chave_cache(endereco, nomlocal)
  if chave in cache:
    return cache[chave]

  chave_legada = montar_chave_cache(endereco, "")
  if chave_legada in cache and bool(cache[chave_legada].get("geocodificado")):
    cache[chave] = cache[chave_legada]
    return cache[chave]

  payload: list[dict[str, object]] = []
  for consulta in gerar_consultas_endereco(endereco, nomlocal):
    payload = consultar_nominatim(consulta, contexto_ssl)
    time.sleep(1)
    if payload:
      break

  if not payload:
    resultado = {
      "latitude": None,
      "longitude": None,
      "endereco_encontrado": "",
      "geocodificado": False,
    }
  else:
    primeiro = payload[0]
    resultado = {
      "latitude": float(primeiro["lat"]),
      "longitude": float(primeiro["lon"]),
      "endereco_encontrado": primeiro.get("display_name", ""),
      "geocodificado": True,
    }

  cache[chave] = resultado
  if chave_legada not in cache:
    cache[chave_legada] = resultado
  return resultado


def enriquecer_registros(
    registros: list[dict[str, str]],
    contexto_ssl,
    cache_inicial: dict[str, dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    cache: dict[str, dict[str, object]] = dict(cache_inicial or {})
    saida: list[dict[str, object]] = []
    limite_atingido = False

    for registro in registros:
        endereco = registro.get("ENDERECO", "").strip()
        nomlocal = registro.get("NOMLOCAL", "").strip()
        chave_cache = montar_chave_cache(endereco, nomlocal)
        chave_legada = montar_chave_cache(endereco, "")
        geodados = {
            "latitude": None,
            "longitude": None,
            "endereco_encontrado": "",
            "geocodificado": False,
        }
        dados_publico = classificar_publico(registro.get("ESTIMATIVA PUBLICO", ""))
        dados_data = extrair_periodo_info(
          registro.get("DATA", ""),
          registro.get("DATAFINAL", ""),
        )
        if endereco:
            if chave_cache in cache:
                geodados = cache[chave_cache]
            elif chave_legada in cache and bool(cache[chave_legada].get("geocodificado")):
                geodados = cache[chave_legada]
                cache[chave_cache] = geodados
            elif not limite_atingido:
                try:
                    geodados = buscar_coordenadas(endereco, nomlocal, contexto_ssl, cache)
                except GeocodingRateLimitError:
                    limite_atingido = True
                    cache[chave_cache] = geodados
                    print(
                        "Aviso: limite de geocodificacao atingido; usando cache existente e seguindo sem novas consultas.",
                        file=sys.stderr,
                    )
            else:
                cache[chave_cache] = geodados

        item: dict[str, object] = dict(registro)
        item.update(geodados)
        item.update(dados_publico)
        item.update(dados_data)
        saida.append(item)

    return saida


def calcular_centro(registros: list[dict[str, object]]) -> tuple[float, float]:
    latitudes = [float(item["latitude"]) for item in registros if item.get("latitude") is not None]
    longitudes = [float(item["longitude"]) for item in registros if item.get("longitude") is not None]

    if not latitudes or not longitudes:
        return DEFAULT_LAT, DEFAULT_LON

    return statistics.mean(latitudes), statistics.mean(longitudes)


def montar_html(registros: list[dict[str, object]]) -> str:
    centro_lat, centro_lon = calcular_centro(registros)
    registros_json = json.dumps(registros, ensure_ascii=False)
    total_geocodificados = sum(1 for item in registros if item.get("geocodificado"))
    meses_encontrados: set[int] = set()
    anos_encontrados: set[int] = set()
    for item in registros:
        chaves = item.get("meses_ano_chaves")
        if isinstance(chaves, list) and chaves:
            for chave in chaves:
                if not isinstance(chave, str) or len(chave) != 7 or "-" not in chave:
                    continue
                ano_txt, mes_txt = chave.split("-", 1)
                if ano_txt.isdigit() and mes_txt.isdigit():
                    anos_encontrados.add(int(ano_txt))
                    meses_encontrados.add(int(mes_txt))
        else:
            if item.get("mes_numero") is not None:
                meses_encontrados.add(int(item["mes_numero"]))
            if item.get("ano_numero") is not None:
                anos_encontrados.add(int(item["ano_numero"]))

    meses_disponiveis = sorted(
        {(mes, MESES_PT_BR.get(mes, f"Mes {mes}")) for mes in meses_encontrados},
        key=lambda item: item[0],
    )
    anos_disponiveis = sorted(anos_encontrados)
    opcoes_mes = "\n".join(
        f'<option value="{numero}">{escape(nome)}</option>' for numero, nome in meses_disponiveis
    )
    opcoes_ano = "\n".join(
        f'<option value="{ano}">{ano}</option>' for ano in anos_disponiveis
    )

    return f"""<!DOCTYPE html>
<html lang=\"pt-BR\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>Mapa de Eventos</title>
    <link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">
    <link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>
    <link href=\"https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&display=swap\" rel=\"stylesheet\">
  <link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\" integrity=\"sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=\" crossorigin=\"\">
  <style>
    :root {{
      --bg: #f6f8fa;
      --panel: rgba(255, 255, 255, 0.96);
      --ink: #32373c;
      --muted: #5f6b76;
      --accent: #f08a24;
      --accent-soft: #ffe0c2;
      --brand: #007f8b;
      --line: rgba(50, 55, 60, 0.14);
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      height: 100%;
      overflow: hidden;
    }}
    body {{
      margin: 0;
      font-family: "Montserrat", "Helvetica Neue", Helvetica, Arial, sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at 5% 0%, rgba(0, 127, 139, 0.1), transparent 35%),
        radial-gradient(circle at 95% 15%, rgba(240, 138, 36, 0.1), transparent 30%),
        linear-gradient(180deg, #ffffff 0%, var(--bg) 100%);
    }}
    .layout {{
      display: grid;
      grid-template-columns: 360px 1fr;
      height: 100vh;
      overflow: hidden;
    }}
    .sidebar {{
      height: 100vh;
      padding: 28px 22px;
      background: var(--panel);
      backdrop-filter: blur(6px);
      border-right: 1px solid var(--line);
      overflow-y: auto;
      box-shadow: 6px 0 24px rgba(0, 0, 0, 0.04);
    }}
    .eyebrow {{
      margin: 0 0 8px;
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--brand);
      font-weight: 700;
    }}
    h1 {{
      margin: 0;
      font-size: 34px;
      line-height: 1.05;
      color: #1f2933;
    }}
    .summary {{
      margin: 18px 0 24px;
      padding: 14px 16px;
      border: 1px solid var(--line);
      background: #ffffff;
      border-radius: 14px;
      box-shadow: 0 6px 18px rgba(0, 0, 0, 0.05);
    }}
    .legend {{
      display: grid;
      gap: 8px;
      margin-top: 12px;
      font-size: 13px;
      color: var(--muted);
    }}
    .filters {{
      display: grid;
      gap: 14px;
      margin: 0 0 18px;
    }}
    .filter-group {{
      display: grid;
      gap: 8px;
    }}
    .filters label {{
      font-size: 13px;
      color: var(--brand);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-weight: 700;
    }}
    .filters select {{
      width: 100%;
      padding: 12px 14px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: #ffffff;
      color: var(--ink);
      font: inherit;
    }}
    .filters select[multiple] {{
      min-height: 144px;
      padding: 8px;
    }}
    .filters-help {{
      margin: -2px 0 0;
      font-size: 12px;
      color: var(--muted);
    }}
    .legend-item {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .legend-swatch {{
      width: 12px;
      height: 12px;
      border-radius: 999px;
      box-shadow: inset 0 0 0 1px rgba(31, 41, 51, 0.12);
    }}
    .event-list {{
      display: grid;
      gap: 14px;
    }}
    .card {{
      padding: 14px 16px;
      background: #ffffff;
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: 0 8px 22px rgba(50, 55, 60, 0.07);
      cursor: pointer;
      transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
    }}
    .card:hover {{
      transform: translateY(-2px);
      box-shadow: 0 14px 30px rgba(50, 55, 60, 0.12);
    }}
    .card.is-active {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(240, 138, 36, 0.18), 0 14px 30px rgba(50, 55, 60, 0.12);
    }}
    .card-header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 10px;
      cursor: pointer;
      user-select: none;
    }}
    .card h2 {{
      margin: 0;
      font-size: 16px;
      flex: 1;
    }}
    .card-date {{
      margin: 4px 0 0;
      font-size: 13px;
      color: var(--muted);
    }}
    .card-chevron {{
      flex-shrink: 0;
      margin-top: 2px;
      font-size: 12px;
      color: var(--muted);
      transition: transform 0.2s ease;
    }}
    .card.is-open .card-chevron {{
      transform: rotate(180deg);
    }}
    .card-details {{
      overflow: hidden;
      max-height: 0;
      transition: max-height 0.28s ease, padding-top 0.28s ease;
      padding-top: 0;
    }}
    .card.is-open .card-details {{
      max-height: 400px;
      padding-top: 12px;
    }}
    .meta {{
      margin: 4px 0;
      font-size: 14px;
      color: var(--muted);
    }}
    .status {{
      display: inline-block;
      margin-top: 10px;
      padding: 4px 8px;
      border-radius: 999px;
      font-size: 12px;
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 700;
    }}
    #map {{
      width: 100%;
      height: 100%;
    }}
    @media (max-width: 900px) {{
      .layout {{
        grid-template-columns: 1fr;
        grid-template-rows: 42vh 58vh;
        height: 100vh;
      }}
      .sidebar {{
        height: 42vh;
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }}
      #map {{
        height: 100%;
      }}
    }}
  </style>
</head>
<body>
  <div class=\"layout\">
    <aside class=\"sidebar\">
      <p class=\"eyebrow\"> </p>
      <h1>Mapa de Calor dos Eventos em Belo Horizonte</h1>
      <div class=\"summary\">
        <div>Total de registros: <span id=\"summary-total\">{len(registros)}</span></div>
        <div>Enderecos geocodificados: <span id=\"summary-geocoded\">{total_geocodificados}</span></div>
        <div class="legend">
          <div class="legend-item"><span class="legend-swatch" style="background:#2f855a"></span> Pequeno porte ate 1.000 pessoas</div>
          <div class="legend-item"><span class="legend-swatch" style="background:#d97706"></span> Medio porte ate 5.000 pessoas</div>
          <div class="legend-item"><span class="legend-swatch" style="background:#c53030"></span> Grande porte acima de 5.000 pessoas</div>
          <div class="legend-item"><span class="legend-swatch" style="background:#64748b"></span> Publico nao informado</div>
        </div>
      </div>
      <div class=\"filters\">
        <div class=\"filter-group\">
          <label for=\"year-filter\">Filtrar por ano</label>
          <select id=\"year-filter\">
            <option value=\"\">Todos os anos</option>
            {opcoes_ano}
          </select>
        </div>
        <div class=\"filter-group\">
          <label for=\"month-filter\">Filtrar por mes</label>
          <select id=\"month-filter\" multiple>
            {opcoes_mes}
          </select>
          <p class=\"filters-help\">Use Command no macOS para selecionar mais de um mes.</p>
        </div>
      </div>
      <div class=\"event-list\">
        {montar_cards_eventos(registros)}
      </div>
    </aside>
    <main id=\"map\"></main>
  </div>

  <script src=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\" integrity=\"sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=\" crossorigin=\"\"></script>
  <script>
    const registros = {registros_json};
    const map = L.map('map').setView([{centro_lat}, {centro_lon}], 12);

    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }}).addTo(map);

    const bounds = [];
    const markers = new Map();
    const yearFilter = document.getElementById('year-filter');
    const monthFilter = document.getElementById('month-filter');
    const eventList = document.querySelector('.event-list');
    const totalSummary = document.getElementById('summary-total');
    const geocodedSummary = document.getElementById('summary-geocoded');

    function ativarCard(index) {{
      document.querySelectorAll('[data-event-index]').forEach((card) => {{
        card.classList.toggle('is-active', Number(card.dataset.eventIndex) === index);
      }});
    }}

    function mesesSelecionados() {{
      return Array.from(monthFilter.selectedOptions).map((option) => option.value).filter(Boolean);
    }}

    function itemNoPeriodoSelecionado(item, anoSelecionado, meses) {{
      const chaves = Array.isArray(item.meses_ano_chaves)
        ? item.meses_ano_chaves
            .map((valor) => String(valor || ''))
            .filter((valor) => /^\\d{{4}}-\\d{{2}}$/.test(valor))
        : [];

      if (chaves.length === 0) {{
        const anoValidoSimples = !anoSelecionado || String(item.ano_numero || '') === anoSelecionado;
        const mesValidoSimples = meses.length === 0 || meses.includes(String(item.mes_numero || ''));
        return anoValidoSimples && mesValidoSimples;
      }}

      const anoValido = !anoSelecionado || chaves.some((chave) => chave.startsWith(`${{anoSelecionado}}-`));
      const mesValido = meses.length === 0 || chaves.some((chave) => {{
        const [anoChave, mesChave] = chave.split('-');
        if (anoSelecionado && anoChave !== anoSelecionado) {{
          return false;
        }}
        return meses.includes(String(Number(mesChave)));
      }});

      return anoValido && mesValido;
    }}

    function ordenarCardsVisiveis() {{
      const cards = Array.from(document.querySelectorAll('[data-event-index]'));
      cards.sort((cardA, cardB) => {{
        const itemA = registros[Number(cardA.dataset.eventIndex)];
        const itemB = registros[Number(cardB.dataset.eventIndex)];
        const chaveA = itemA.data_ordem || '9999-99-99';
        const chaveB = itemB.data_ordem || '9999-99-99';
        return chaveA.localeCompare(chaveB);
      }});
      cards.forEach((card) => eventList.appendChild(card));
    }}

    function aplicarFiltro() {{
      const anoSelecionado = yearFilter.value;
      const meses = mesesSelecionados();
      const boundsFiltrados = [];
      let totalVisivel = 0;
      let geocodificadosVisiveis = 0;

      document.querySelectorAll('[data-event-index]').forEach((card) => {{
        const index = Number(card.dataset.eventIndex);
        const item = registros[index];
        const marker = markers.get(index);
        const visivel = itemNoPeriodoSelecionado(item, anoSelecionado, meses);

        card.style.display = visivel ? '' : 'none';
        if (!visivel) {{
          card.classList.remove('is-active');
        }}

        if (marker) {{
          if (visivel) {{
            marker.addTo(map);
            boundsFiltrados.push(marker.getLatLng());
            geocodificadosVisiveis += 1;
          }} else {{
            marker.remove();
          }}
        }}

        if (visivel) {{
          totalVisivel += 1;
        }}
      }});

      ordenarCardsVisiveis();
      totalSummary.textContent = String(totalVisivel);
      geocodedSummary.textContent = String(geocodificadosVisiveis);

      if (boundsFiltrados.length > 0) {{
        map.fitBounds(boundsFiltrados, {{ padding: [30, 30] }});
      }} else {{
        map.setView([{centro_lat}, {centro_lon}], 12);
      }}
    }}

    registros.forEach((item, index) => {{
      if (item.latitude === null || item.longitude === null) {{
        return;
      }}

      const popup = `
        <strong>${{item.DESCRICAO || 'Evento'}}</strong><br>
        Data inicial: ${{item.DATA || '-'}}<br>
        Data final: ${{item.DATAFINAL || item.DATA || '-'}}<br>
        Local: ${{item.NOMLOCAL || '-'}}<br>
        Endereco: ${{item.ENDERECO || '-'}}<br>
        Publico estimado: ${{item['ESTIMATIVA PUBLICO'] || '-'}}<br>
        Faixa: ${{item.faixa_publico || '-'}}
      `;

      const marker = L.circleMarker([item.latitude, item.longitude], {{
        radius: item.raio_marcador || 8,
        color: item.cor_marcador || '#64748b',
        fillColor: item.cor_marcador || '#64748b',
        fillOpacity: 0.85,
        weight: 2
      }}).addTo(map).bindPopup(popup);
      marker.on('click', () => ativarCard(index));
      markers.set(index, marker);
      bounds.push([item.latitude, item.longitude]);
    }});

    document.querySelectorAll('[data-event-index]').forEach((card) => {{
      const header = card.querySelector('.card-header');

      header.addEventListener('click', () => {{
        const isOpen = card.classList.contains('is-open');
        card.classList.toggle('is-open', !isOpen);
      }});

      card.addEventListener('click', (evt) => {{
        if (evt.target.closest('.card-header')) {{ return; }}
        const index = Number(card.dataset.eventIndex);
        const marker = markers.get(index);

        ativarCard(index);
        if (!marker) {{ return; }}

        const latLng = marker.getLatLng();
        map.flyTo(latLng, Math.max(map.getZoom(), 15), {{ duration: 0.6 }});
        marker.openPopup();
      }});
    }});

    document.querySelectorAll('.card-header').forEach((header) => {{
      header.addEventListener('click', (evt) => {{
        const card = header.closest('[data-event-index]');
        const index = Number(card.dataset.eventIndex);
        const marker = markers.get(index);

        ativarCard(index);
        if (!marker) {{ return; }}

        const latLng = marker.getLatLng();
        map.flyTo(latLng, Math.max(map.getZoom(), 15), {{ duration: 0.6 }});
        marker.openPopup();
      }});
    }});

    yearFilter.addEventListener('change', aplicarFiltro);
    monthFilter.addEventListener('change', aplicarFiltro);

    if (bounds.length > 0) {{
      map.fitBounds(bounds, {{ padding: [30, 30] }});
    }}
    aplicarFiltro();
  </script>
</body>
</html>
"""


def montar_cards_eventos(registros: list[dict[str, object]]) -> str:
    cards: list[str] = []
    for index, item in enumerate(registros):
        descricao = escape(str(item.get("DESCRICAO") or "Evento sem descricao"))
        data = escape(str(item.get("DATA") or "-"))
        data_final = escape(str(item.get("DATAFINAL") or item.get("DATA") or "-"))
        nomlocal = escape(str(item.get("NOMLOCAL") or "-"))
        endereco = escape(str(item.get("ENDERECO") or "-"))
        publico = escape(str(item.get("ESTIMATIVA PUBLICO") or "-"))
        faixa = escape(str(item.get("faixa_publico") or "Nao informado"))
        cor = escape(str(item.get("cor_marcador") or "#64748b"))
        mes = escape(str(item.get("mes_nome") or "Mes desconhecido"))
        ano = escape(str(item.get("ano_numero") or "Ano desconhecido"))
        status = "Localizado no mapa" if item.get("geocodificado") else "Endereco nao localizado"
        cards.append(
            f"<article class=\"card\" data-event-index=\"{index}\" data-month=\"{item.get('mes_numero') or ''}\" data-year=\"{item.get('ano_numero') or ''}\" data-date-order=\"{item.get('data_ordem') or ''}\" style=\"border-left: 6px solid {cor};\">"  
            f"<div class=\"card-header\">"  
            f"<div><h2>{descricao}</h2><p class=\"card-date\">{data}</p></div>"  
            f"<span class=\"card-chevron\">&#9660;</span>"  
            f"</div>"  
            f"<div class=\"card-details\">"  
            f"<p class=\"meta\"><strong>Data inicial:</strong> {data}</p>"  
            f"<p class=\"meta\"><strong>Data final:</strong> {data_final}</p>"  
            f"<p class=\"meta\"><strong>Mes:</strong> {mes}</p>"  
            f"<p class=\"meta\"><strong>Ano:</strong> {ano}</p>"  
            f"<p class=\"meta\"><strong>Local:</strong> {nomlocal}</p>"  
            f"<p class=\"meta\"><strong>Endereco:</strong> {endereco}</p>"  
            f"<p class=\"meta\"><strong>Publico:</strong> {publico}</p>"  
            f"<p class=\"meta\"><strong>Faixa:</strong> {faixa}</p>"  
            f"<span class=\"status\">{escape(status)}</span>"  
            f"</div>"  
            "</article>"
        )
    return "\n".join(cards)


def salvar_html(registros: list[dict[str, object]], caminho_saida: str) -> None:
    html = montar_html(registros)
    with open(caminho_saida, "w", encoding="utf-8") as arquivo:
        arquivo.write(html)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Le um CSV local de eventos, geocodifica os enderecos e gera uma pagina HTML com mapa."
    )
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=DEFAULT_CSV_PATH,
        help="Caminho do CSV local com os eventos.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Desativa verificacao SSL para diagnostico local (apenas para geocodificacao).",
    )
    parser.add_argument(
        "--html-output",
        default="mapa.html",
        help="Arquivo HTML de saida.",
    )
    parser.add_argument(
        "--json-output",
        default="eventos_geocodificados.json",
        help="Arquivo JSON com os registros e coordenadas.",
    )

    args = parser.parse_args()
    contexto_ssl = criar_contexto_ssl(args.insecure)

    try:
      total_sincronizado = sincronizar_csv_com_planilha(args.csv_path, contexto_ssl)
      print(f"CSV sincronizado com a planilha: {total_sincronizado} registros.")
    except RuntimeError as exc:
      print(f"Erro ao sincronizar planilha para CSV: {exc}", file=sys.stderr)
      return 1

    try:
      registros = padronizar_registros(ler_registros_csv_local(args.csv_path))
    except RuntimeError as exc:
      print(f"Erro: {exc}", file=sys.stderr)
      return 1

    try:
        cache_geocodificacao = carregar_cache_geocodificacao(args.json_output)
        registros_geocodificados = enriquecer_registros(
            registros,
            contexto_ssl,
            cache_inicial=cache_geocodificacao,
        )
        salvar_json(registros_geocodificados, args.json_output)
        salvar_html(registros_geocodificados, args.html_output)
    except RuntimeError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1

    total_geocodificados = sum(1 for item in registros_geocodificados if item.get("geocodificado"))
    print(f"Total de registros: {len(registros_geocodificados)}")
    print(f"Enderecos geocodificados: {total_geocodificados}")
    print(f"JSON salvo em: {args.json_output}")
    print(f"Mapa HTML salvo em: {args.html_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())