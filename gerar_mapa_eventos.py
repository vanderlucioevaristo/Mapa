#!/usr/bin/env python3
"""Gerar uma pagina HTML com mapa dos eventos lidos da planilha."""

from __future__ import annotations

import argparse
import datetime as dt
import json
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
    extrair_gid,
    extrair_sheet_id,
    ler_registros,
    montar_url_csv,
    padronizar_registros,
    salvar_json,
)


DEFAULT_URL = "https://docs.google.com/spreadsheets/d/1BiZyq9KeLk8pBc-N_AZWOq98KvUPOseGuQVBQAGQ4pk/edit?usp=sharing"
DEFAULT_LAT = -19.9167
DEFAULT_LON = -43.9345
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


def extrair_data_info(data: str) -> dict[str, object]:
  texto = (data or "").strip()
  try:
    data_evento = dt.datetime.strptime(texto, "%d/%m/%Y")
  except ValueError:
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


def extrair_publico(publico: str) -> int | None:
  numeros = "".join(ch for ch in publico if ch.isdigit())
  if not numeros:
    return None
  return int(numeros)

def classificar_publico(publico: str) -> dict[str, object]:
  valor = extrair_publico(publico)
  if valor is None:
    return {
      "publico_valor": None,
      "faixa_publico": "Nao informado",
      "cor_marcador": "#64748b",
      "raio_marcador": 8,
    }

  if valor <= 1000:
    return {
      "publico_valor": valor,
      "faixa_publico": "Pequeno porte",
      "cor_marcador": "#2f855a",
      "raio_marcador": 8,
    }

  if valor <= 5000:
    return {
      "publico_valor": valor,
      "faixa_publico": "Medio porte",
      "cor_marcador": "#d97706",
      "raio_marcador": 11,
    }

  return {
    "publico_valor": valor,
    "faixa_publico": "Grande porte",
    "cor_marcador": "#c53030",
    "raio_marcador": 14,
  }


def remover_acentos(texto: str) -> str:
  return "".join(
    ch for ch in unicodedata.normalize("NFD", texto) if unicodedata.category(ch) != "Mn"
  )


def gerar_consultas_endereco(endereco: str) -> list[str]:
  base = " ".join(endereco.strip().split())
  sem_acentos = remover_acentos(base)

  variacoes = [
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
        raise RuntimeError(f"Falha HTTP ao geocodificar endereco '{consulta}': {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"Erro de rede ao geocodificar endereco '{consulta}': {exc.reason}") from exc


def buscar_coordenadas(endereco: str, contexto_ssl, cache: dict[str, dict[str, object]]) -> dict[str, object]:
    if endereco in cache:
        return cache[endereco]

    payload: list[dict[str, object]] = []
    for consulta in gerar_consultas_endereco(endereco):
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

    cache[endereco] = resultado
    return resultado


def enriquecer_registros(registros: list[dict[str, str]], contexto_ssl) -> list[dict[str, object]]:
    cache: dict[str, dict[str, object]] = {}
    saida: list[dict[str, object]] = []

    for registro in registros:
        endereco = registro.get("ENDERECO", "").strip()
        geodados = {
            "latitude": None,
            "longitude": None,
            "endereco_encontrado": "",
            "geocodificado": False,
        }
        dados_publico = classificar_publico(registro.get("ESTIMATIVA PUBLICO", ""))
        dados_data = extrair_data_info(registro.get("DATA", ""))
        if endereco:
            geodados = buscar_coordenadas(endereco, contexto_ssl, cache)

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
    meses_disponiveis = sorted(
        {
            (int(item["mes_numero"]), str(item["mes_nome"]))
            for item in registros
            if item.get("mes_numero") is not None
        },
        key=lambda item: item[0],
    )
    anos_disponiveis = sorted(
        {int(item["ano_numero"]) for item in registros if item.get("ano_numero") is not None}
    )
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
  <link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\" integrity=\"sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=\" crossorigin=\"\">
  <style>
    :root {{
      --bg: #f4efe8;
      --panel: rgba(255, 252, 247, 0.92);
      --ink: #1f2933;
      --muted: #52606d;
      --accent: #c2410c;
      --accent-soft: #fed7aa;
      --line: rgba(31, 41, 51, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      height: 100%;
      overflow: hidden;
    }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(194, 65, 12, 0.12), transparent 28%),
        linear-gradient(180deg, #f8f4ee 0%, var(--bg) 100%);
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
      backdrop-filter: blur(8px);
      border-right: 1px solid var(--line);
      overflow-y: auto;
    }}
    .eyebrow {{
      margin: 0 0 8px;
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--accent);
    }}
    h1 {{
      margin: 0;
      font-size: 34px;
      line-height: 1.05;
    }}
    .summary {{
      margin: 18px 0 24px;
      padding: 14px 16px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.7);
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
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .filters select {{
      width: 100%;
      padding: 12px 14px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.92);
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
      background: rgba(255, 255, 255, 0.78);
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: 0 10px 30px rgba(31, 41, 51, 0.06);
      cursor: pointer;
      transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
    }}
    .card:hover {{
      transform: translateY(-2px);
      box-shadow: 0 14px 34px rgba(31, 41, 51, 0.1);
    }}
    .card.is-active {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(194, 65, 12, 0.14), 0 14px 34px rgba(31, 41, 51, 0.1);
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
      <p class=\"eyebrow\">Mapa de Calor dos Eventos em Belo Horizonte</p>
      <h1>Mapa dos eventos</h1>
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
        const anoValido = !anoSelecionado || String(item.ano_numero || '') === anoSelecionado;
        const mesValido = meses.length === 0 || meses.includes(String(item.mes_numero || ''));
        const visivel = anoValido && mesValido;

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
        Data: ${{item.DATA || '-'}}<br>
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
            f"<p class=\"meta\"><strong>Mes:</strong> {mes}</p>"  
            f"<p class=\"meta\"><strong>Ano:</strong> {ano}</p>"  
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
        description="Le a planilha de eventos, geocodifica os enderecos e gera uma pagina HTML com mapa."
    )
    parser.add_argument("url", nargs="?", default=DEFAULT_URL, help="URL da planilha do Google Sheets")
    parser.add_argument("--gid", default=None, help="GID da aba (opcional).")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Desativa verificacao SSL para diagnostico local.",
    )
    parser.add_argument(
        "--html-output",
        default="mapa_eventos.html",
        help="Arquivo HTML de saida.",
    )
    parser.add_argument(
        "--json-output",
        default="eventos_geocodificados.json",
        help="Arquivo JSON com os registros e coordenadas.",
    )

    args = parser.parse_args()

    try:
        sheet_id = extrair_sheet_id(args.url)
    except ValueError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1

    gid = args.gid or extrair_gid(args.url) or "0"
    csv_url = montar_url_csv(sheet_id, gid)
    contexto_ssl = criar_contexto_ssl(args.insecure)

    try:
        registros = padronizar_registros(ler_registros(csv_url, contexto_ssl))
        registros_geocodificados = enriquecer_registros(registros, contexto_ssl)
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