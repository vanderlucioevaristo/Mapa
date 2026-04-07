#!/usr/bin/env python3
"""Gera um calendario HTML de eventos por local e faixa de publico."""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import unicodedata
from html import escape

from listar_eventos_planilha import ler_registros_csv_local, padronizar_registros


DEFAULT_CSV_PATH = "eventos_bh.csv"
DEFAULT_HTML_OUTPUT = "calendario.html"

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

FAIXAS = {
    "PEQUENO": {
        "nome": "Pequeno porte",
        "cor": "#2f855a",
        "ordem": 1,
    },
    "MEDIO": {
        "nome": "Medio porte",
        "cor": "#d97706",
        "ordem": 2,
    },
    "GRANDE": {
        "nome": "Grande porte",
        "cor": "#c53030",
        "ordem": 3,
    },
    "NAO_INFORMADO": {
        "nome": "Nao informado",
        "cor": "#64748b",
        "ordem": 4,
    },
}


def remover_acentos(texto: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", texto) if unicodedata.category(ch) != "Mn"
    )


def parse_data_evento(texto: str) -> dt.date | None:
    if not texto:
        return None

    texto = texto.strip()
    formatos = ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"]
    for formato in formatos:
        try:
            return dt.datetime.strptime(texto, formato).date()
        except ValueError:
            continue

    partes = [parte.strip() for parte in texto.replace("-", "/").split("/")]
    if len(partes) == 3 and partes[0].isdigit() and partes[2].isdigit():
        mes = ABREV_MES_PARA_NUMERO.get(remover_acentos(partes[1]).upper()[:3])
        if mes:
            try:
                return dt.date(int(partes[2]), mes, int(partes[0]))
            except ValueError:
                return None

    return None


def extrair_publico(publico: str) -> int | None:
    numeros = "".join(ch for ch in (publico or "") if ch.isdigit())
    if not numeros:
        return None
    return int(numeros)


def classificar_publico(publico: str) -> dict[str, str | int | None]:
    valor = extrair_publico(publico)
    if valor is None:
        faixa = FAIXAS["NAO_INFORMADO"]
        return {
            "faixa_id": "NAO_INFORMADO",
            "faixa_nome": str(faixa["nome"]),
            "cor": str(faixa["cor"]),
            "ordem": int(faixa["ordem"]),
            "publico_valor": None,
        }

    if valor <= 1000:
        faixa = FAIXAS["PEQUENO"]
        return {
            "faixa_id": "PEQUENO",
            "faixa_nome": str(faixa["nome"]),
            "cor": str(faixa["cor"]),
            "ordem": int(faixa["ordem"]),
            "publico_valor": valor,
        }

    if valor <= 5000:
        faixa = FAIXAS["MEDIO"]
        return {
            "faixa_id": "MEDIO",
            "faixa_nome": str(faixa["nome"]),
            "cor": str(faixa["cor"]),
            "ordem": int(faixa["ordem"]),
            "publico_valor": valor,
        }

    faixa = FAIXAS["GRANDE"]
    return {
        "faixa_id": "GRANDE",
        "faixa_nome": str(faixa["nome"]),
        "cor": str(faixa["cor"]),
        "ordem": int(faixa["ordem"]),
        "publico_valor": valor,
    }


def normalizar_local(local: str) -> str:
    return " ".join((local or "").strip().split()) or "Local nao informado"


def iterar_periodo(inicio: dt.date, fim: dt.date) -> list[dt.date]:
    if fim < inicio:
        fim = inicio

    dias: list[dt.date] = []
    atual = inicio
    while atual <= fim:
        dias.append(atual)
        atual += dt.timedelta(days=1)
    return dias


def primeiro_dia_mes(ano: int, mes: int) -> dt.date:
    return dt.date(ano, mes, 1)


def ultimo_dia_mes(ano: int, mes: int) -> dt.date:
    return dt.date(ano, mes, calendar.monthrange(ano, mes)[1])


def parse_ano_mes(texto: str) -> tuple[int, int]:
    valor = (texto or "").strip()
    partes = valor.split("-")
    if len(partes) != 2 or not partes[0].isdigit() or not partes[1].isdigit():
        raise ValueError(f"Formato invalido '{texto}'. Use AAAA-MM.")

    ano = int(partes[0])
    mes = int(partes[1])
    if mes < 1 or mes > 12:
        raise ValueError(f"Mes invalido em '{texto}'.")
    return ano, mes


def construir_recorte_geracao(args: argparse.Namespace) -> tuple[dt.date, dt.date, str] | None:
    if args.mes:
        ano, mes = parse_ano_mes(args.mes)
        inicio = primeiro_dia_mes(ano, mes)
        fim = ultimo_dia_mes(ano, mes)
        return inicio, fim, f"Mes {MESES_PT_BR[mes]}/{ano}"

    if args.intervalo_meses:
        inicio_ano, inicio_mes = parse_ano_mes(args.intervalo_meses[0])
        fim_ano, fim_mes = parse_ano_mes(args.intervalo_meses[1])
        inicio = primeiro_dia_mes(inicio_ano, inicio_mes)
        fim = ultimo_dia_mes(fim_ano, fim_mes)
        if fim < inicio:
            raise ValueError("Intervalo de meses invalido: fim anterior ao inicio.")
        return (
            inicio,
            fim,
            f"Intervalo {MESES_PT_BR[inicio.month]}/{inicio.year} ate {MESES_PT_BR[fim.month]}/{fim.year}",
        )

    if args.ano is not None:
        ano = int(args.ano)
        inicio = dt.date(ano, 1, 1)
        fim = dt.date(ano, 12, 31)
        return inicio, fim, f"Ano {ano}"

    if args.semestre:
        valor = (args.semestre or "").strip()
        partes = valor.split("-")
        if len(partes) != 2 or not partes[0].isdigit() or partes[1] not in ("1", "2"):
            raise ValueError("Formato invalido para semestre. Use AAAA-1 ou AAAA-2.")

        ano = int(partes[0])
        semestre = int(partes[1])
        if semestre == 1:
            inicio = dt.date(ano, 1, 1)
            fim = dt.date(ano, 6, 30)
        else:
            inicio = dt.date(ano, 7, 1)
            fim = dt.date(ano, 12, 31)

        return inicio, fim, f"{semestre}o semestre de {ano}"

    return None


def construir_linhas_calendario(
    registros: list[dict[str, str]],
    recorte: tuple[dt.date, dt.date, str] | None = None,
) -> tuple[list[dict[str, object]], list[int]]:
    linhas: dict[tuple[str, str], dict[str, object]] = {}
    anos: set[int] = set()

    for registro in registros:
        data_inicio = parse_data_evento(registro.get("DATA", ""))
        data_final = parse_data_evento(registro.get("DATAFINAL", ""))

        if data_inicio is None and data_final is None:
            continue
        if data_inicio is None:
            data_inicio = data_final
        if data_final is None:
            data_final = data_inicio

        assert data_inicio is not None
        assert data_final is not None

        if recorte is not None:
            inicio_recorte, fim_recorte, _ = recorte
            if data_final < inicio_recorte or data_inicio > fim_recorte:
                continue

        classificacao = classificar_publico(registro.get("ESTIMATIVA PUBLICO", ""))
        local = normalizar_local(registro.get("NOMLOCAL", ""))
        faixa_id = str(classificacao["faixa_id"])
        chave = (local, faixa_id)

        linha = linhas.get(chave)
        if linha is None:
            linha = {
                "local": local,
                "faixa_id": faixa_id,
                "faixa_nome": classificacao["faixa_nome"],
                "cor": classificacao["cor"],
                "ordem": classificacao["ordem"],
                "eventos": {},
            }
            linhas[chave] = linha

        eventos = linha["eventos"]
        for data_evento in iterar_periodo(data_inicio, data_final):
            anos.add(data_evento.year)
            chave_data = data_evento.strftime("%Y-%m-%d")
            if chave_data not in eventos:
                eventos[chave_data] = []

            eventos[chave_data].append(
                {
                    "dia": data_evento.day,
                    "mes": data_evento.month,
                    "ano": data_evento.year,
                    "inicio_periodo": data_evento == data_inicio,
                    "fim_periodo": data_evento == data_final,
                    "descricao": registro.get("DESCRICAO", "") or "Evento",
                    "publico": registro.get("ESTIMATIVA PUBLICO", "") or "-",
                    "endereco": registro.get("ENDERECO", "") or "-",
                    "data_inicio": registro.get("DATA", "") or "-",
                    "data_final": registro.get("DATAFINAL", "") or registro.get("DATA", "") or "-",
                }
            )

    linhas_ordenadas = sorted(
        linhas.values(),
        key=lambda item: (int(item["ordem"]), remover_acentos(str(item["local"])).upper()),
    )
    return linhas_ordenadas, sorted(anos)


def meses_por_ano_no_recorte(
    anos: list[int],
    recorte: tuple[dt.date, dt.date, str] | None,
) -> dict[int, list[int]]:
    if not anos:
        return {}

    if recorte is None:
        return {ano: list(range(1, 13)) for ano in anos}

    inicio, fim, _ = recorte
    meses_por_ano: dict[int, list[int]] = {}
    for ano in anos:
        meses: list[int] = []
        for mes in range(1, 13):
            inicio_mes = primeiro_dia_mes(ano, mes)
            fim_mes = ultimo_dia_mes(ano, mes)
            if fim_mes < inicio or inicio_mes > fim:
                continue
            meses.append(mes)
        meses_por_ano[ano] = meses

    return meses_por_ano


def montar_bloco_mes(ano: int, mes: int, eventos_mes: dict[int, list[dict[str, object]]], cor: str) -> str:
    total_dias = calendar.monthrange(ano, mes)[1]
    dias_html: list[str] = []

    for dia in range(1, total_dias + 1):
        eventos_dia = eventos_mes.get(dia, [])
        if not eventos_dia:
            dias_html.append(f'<div class="day-cell"><span class="day-number">{dia}</span></div>')
            continue

        tem_inicio = any(bool(evento.get("inicio_periodo")) for evento in eventos_dia)
        tem_fim = any(bool(evento.get("fim_periodo")) for evento in eventos_dia)
        classes = ["day-cell", "has-event"]
        if tem_inicio and tem_fim:
            classes.append("is-single")
        elif tem_inicio:
            classes.append("is-start")
        elif tem_fim:
            classes.append("is-end")
        else:
            classes.append("is-middle")

        tags = []
        for evento in eventos_dia:
            descricao = escape(str(evento.get("descricao") or "Evento"))
            publico = escape(str(evento.get("publico") or "-"))
            endereco = escape(str(evento.get("endereco") or "-"))
            data_inicio = escape(str(evento.get("data_inicio") or "-"))
            data_final = escape(str(evento.get("data_final") or "-"))
            tags.append(
                f'<div class="event-tag" style="--event-color:{cor}" title="{descricao} | Periodo: {data_inicio} ate {data_final} | Publico: {publico} | Endereco: {endereco}">{descricao}</div>'
            )

        dias_html.append(
            f'<div class="{" ".join(classes)}" style="--event-color:{cor}"><span class="day-number">{dia}</span><div class="event-details">{"".join(tags)}</div><div class="event-count">{len(eventos_dia)} evento(s)</div></div>'
        )

    return f'<div class="month-grid">{"".join(dias_html)}</div>'


def montar_html(
    linhas: list[dict[str, object]],
    anos: list[int],
    filtro_descricao: str,
    recorte: tuple[dt.date, dt.date, str] | None = None,
) -> str:
    linhas_json = json.dumps(linhas, ensure_ascii=False)
    anos = anos or [dt.date.today().year]
    meses_por_ano = meses_por_ano_no_recorte(anos, recorte)

    secoes: list[str] = []
    for ano in anos:
        meses_ano = meses_por_ano.get(ano, list(range(1, 13)))
        if not meses_ano:
            continue

        cabecalho_meses = "".join(
            f"<th>{MESES_PT_BR[mes]}</th>" for mes in meses_ano
        )
        linhas_tabela: list[str] = []
        for linha in linhas:
            eventos_raw = linha.get("eventos", {})
            eventos_dict = eventos_raw if isinstance(eventos_raw, dict) else {}
            por_mes: dict[int, dict[int, list[dict[str, object]]]] = {mes: {} for mes in range(1, 13)}

            for chave_data, eventos in eventos_dict.items():
                try:
                    data = dt.datetime.strptime(str(chave_data), "%Y-%m-%d").date()
                except ValueError:
                    continue

                if data.year != ano:
                    continue

                mes_evento = data.month
                dia_evento = data.day
                if dia_evento not in por_mes[mes_evento]:
                    por_mes[mes_evento][dia_evento] = []
                por_mes[mes_evento][dia_evento].extend(eventos)

            celulas_mes = "".join(
                f"<td>{montar_bloco_mes(ano, mes, por_mes[mes], str(linha.get('cor') or '#64748b'))}</td>"
                for mes in meses_ano
            )

            local = escape(str(linha.get("local") or "Local nao informado"))
            faixa = escape(str(linha.get("faixa_nome") or "Nao informado"))
            cor = escape(str(linha.get("cor") or "#64748b"))
            linhas_tabela.append(
                "<tr>"
                f"<th class=\"local-cell\"><div class=\"local-name\">{local}</div><div class=\"faixa\" style=\"--faixa-color:{cor}\">{faixa}</div></th>"
                f"{celulas_mes}"
                "</tr>"
            )

        secoes.append(
            f"""
            <section class=\"year-section\">
              <h2>{ano}</h2>
                            <div class=\"table-wrap\" style=\"--meses-count:{len(meses_ano)}\">
                <table>
                  <thead>
                    <tr>
                      <th class=\"local-head\">Local do Evento</th>
                      {cabecalho_meses}
                    </tr>
                  </thead>
                  <tbody>
                    {''.join(linhas_tabela)}
                  </tbody>
                </table>
              </div>
            </section>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Calendario de Eventos — Visite Belo Horizonte</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700;800&family=Outfit:wght@300;400;600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #f4f5f9;
      --ink: #1a1f36;
      --muted: #5b6573;
      --line: rgba(0, 11, 51, 0.13);
      --panel: #ffffff;
      --header: #000b33;
      --header-ink: #FED141;
      --accent: #FED141;
      --accent-dark: #c9a500;
            --local-col-width: 260px;
            --month-col-width: 150px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Outfit", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at 100% 0%, rgba(0, 11, 51, 0.10), transparent 40%),
        radial-gradient(circle at 0% 100%, rgba(254, 209, 65, 0.12), transparent 35%),
        var(--bg);
    }}
    .shell {{
      padding: 24px;
      display: grid;
      gap: 20px;
    }}
    .hero {{
      background: linear-gradient(120deg, #000b33 0%, #001a6e 60%, #002080 100%);
      color: #f8fafc;
      border-radius: 18px;
      padding: 22px 24px 18px;
      box-shadow: 0 12px 36px rgba(0, 11, 51, 0.36);
      position: relative;
      overflow: hidden;
    }}
    .hero::before {{
      content: "";
      position: absolute;
      inset: 0;
      background:
        radial-gradient(ellipse at 90% -10%, rgba(254, 209, 65, 0.18) 0%, transparent 55%),
        radial-gradient(ellipse at 10% 110%, rgba(254, 209, 65, 0.10) 0%, transparent 45%);
      pointer-events: none;
    }}
    .hero-brand {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 10px;
    }}
    .hero-brand-badge {{
      background: var(--accent);
      color: #000b33;
      font-family: "Montserrat", sans-serif;
      font-weight: 800;
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      padding: 3px 10px;
      border-radius: 4px;
    }}
    .hero-brand-site {{
      font-size: 12px;
      opacity: 0.65;
      letter-spacing: 0.05em;
    }}
    .hero h1 {{
      margin: 0;
      font-family: "Montserrat", sans-serif;
      font-size: 26px;
      font-weight: 800;
      color: var(--accent);
      letter-spacing: -0.01em;
      line-height: 1.2;
    }}
    .hero p {{ margin: 8px 0 0; opacity: 0.85; font-size: 14px; }}
    .hero p strong {{ color: var(--accent); opacity: 1; }}
    .view-toggle {{
      margin-top: 14px;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      font-weight: 600;
      background: rgba(254, 209, 65, 0.12);
      border: 1px solid rgba(254, 209, 65, 0.35);
      border-radius: 999px;
      padding: 6px 14px;
      color: #f8fafc;
      cursor: pointer;
    }}
    .view-toggle input {{ accent-color: var(--accent); }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 14px;
    }}
    .legend-item {{
      display: inline-flex;
      align-items: center;
      gap: 7px;
      background: rgba(254, 209, 65, 0.08);
      border: 1px solid rgba(254, 209, 65, 0.28);
      padding: 5px 10px;
      border-radius: 999px;
      font-size: 12px;
      color: rgba(248, 250, 252, 0.92);
    }}
    .legend-dot {{ width: 9px; height: 9px; border-radius: 999px; flex-shrink: 0; }}
    .year-section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-top: 3px solid var(--header);
      border-radius: 12px;
      padding: 14px;
      box-shadow: 0 6px 20px rgba(0, 11, 51, 0.08);
    }}
    .year-section h2 {{
      margin: 0 0 14px;
      font-family: "Montserrat", sans-serif;
      font-size: 20px;
      font-weight: 800;
      color: var(--header);
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .year-section h2::after {{
      content: "";
      flex: 1;
      height: 2px;
      background: linear-gradient(90deg, var(--accent) 0%, transparent 100%);
      border-radius: 1px;
      opacity: 0.6;
    }}
        .table-wrap {{ overflow-x: auto; --meses-count: 12; }}
        table {{
            border-collapse: collapse;
            width: max-content;
            min-width: calc(var(--local-col-width) + (var(--meses-count) * var(--month-col-width)));
        }}
    th, td {{ border: 1px solid var(--line); vertical-align: top; }}
    thead th {{
      position: sticky;
      top: 0;
      z-index: 3;
      background: var(--header);
      color: var(--header-ink);
      padding: 10px;
      font-family: "Montserrat", sans-serif;
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .local-head {{ left: 0; z-index: 4; min-width: var(--local-col-width); position: sticky; }}
    .local-cell {{
      position: sticky;
      left: 0;
      z-index: 2;
    min-width: var(--local-col-width);
      background: #f0f1f8;
      text-align: left;
      padding: 10px;
      border-left: 3px solid var(--header);
    }}
    .local-name {{ font-size: 14px; font-weight: 600; color: var(--header); }}
    .faixa {{
      margin-top: 6px;
      display: inline-flex;
      font-size: 11px;
      padding: 3px 8px;
      border-radius: 999px;
      border: 1px solid color-mix(in srgb, var(--faixa-color) 60%, white);
      background: color-mix(in srgb, var(--faixa-color) 14%, white);
      color: color-mix(in srgb, var(--faixa-color) 85%, black);
      font-weight: 600;
    }}
    td {{ width: var(--month-col-width); background: #ffffff; padding: 6px; }}
    .month-grid {{
      display: grid;
      grid-template-columns: repeat(7, 1fr);
      gap: 0;
    }}
    .day-cell {{
      min-height: 56px;
      border: 1px dashed rgba(0, 11, 51, 0.10);
      border-radius: 0;
      padding: 4px;
      background: #fafbff;
      display: grid;
      gap: 4px;
      align-content: start;
    }}
    .day-cell.has-event {{
      background: color-mix(in srgb, var(--event-color) 52%, white);
      border: 0;
      box-shadow: none;
    }}
    .day-cell.has-event.is-middle {{ border-radius: 0; }}
    .day-cell.has-event.is-start {{
      border-top-right-radius: 0;
      border-bottom-right-radius: 0;
      border-top-left-radius: 8px;
      border-bottom-left-radius: 8px;
    }}
    .day-cell.has-event.is-end {{
      border-top-left-radius: 0;
      border-bottom-left-radius: 0;
      border-top-right-radius: 8px;
      border-bottom-right-radius: 8px;
    }}
    .day-cell.has-event.is-single {{ border-radius: 8px; }}
    .day-number {{
      font-size: 11px;
      color: color-mix(in srgb, var(--event-color) 88%, #000b33);
      font-weight: 700;
    }}
    .event-tag {{
      font-size: 10px;
      line-height: 1.2;
      padding: 2px 4px;
      border-radius: 6px;
      color: color-mix(in srgb, var(--event-color) 92%, #000b33);
      background: color-mix(in srgb, white 58%, var(--event-color) 42%);
      border: 1px solid color-mix(in srgb, var(--event-color) 68%, white);
      font-weight: 600;
      overflow: hidden;
      text-overflow: ellipsis;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
    }}
    .event-count {{
      display: none;
      font-size: 10px;
      font-weight: 700;
      color: color-mix(in srgb, var(--event-color) 92%, #000b33);
      background: color-mix(in srgb, white 50%, var(--event-color) 50%);
      border: 1px solid color-mix(in srgb, var(--event-color) 72%, white);
      border-radius: 999px;
      padding: 1px 6px;
      width: fit-content;
    }}
    body.compact-view .event-details {{ display: none; }}
    body.compact-view .event-count {{
      display: inline-flex;
      align-items: center;
    }}
    @media (max-width: 900px) {{
            :root {{
                --local-col-width: 200px;
                --month-col-width: 130px;
            }}
      .shell {{ padding: 12px; }}
      .hero h1 {{ font-size: 20px; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="hero-brand">
        <span class="hero-brand-badge">Visite BH</span>
        <span class="hero-brand-site">visitebelohorizonte.com</span>
      </div>
      <h1>Calendario de Eventos por Local e Publico</h1>
    <p>Linhas agrupadas por local e faixa de publico. Um local pode aparecer mais de uma vez quando possui eventos em faixas diferentes.</p>
    <p><strong>Recorte:</strong> {escape(filtro_descricao)}</p>
            <label class="view-toggle" for="toggle-details">
                <input id="toggle-details" type="checkbox" checked>
                Exibir detalhes dos eventos
            </label>
      <div class="legend">
        <span class="legend-item"><span class="legend-dot" style="background:#2f855a"></span>Pequeno porte (ate 1.000)</span>
        <span class="legend-item"><span class="legend-dot" style="background:#d97706"></span>Medio porte (ate 5.000)</span>
        <span class="legend-item"><span class="legend-dot" style="background:#c53030"></span>Grande porte (acima de 5.000)</span>
        <span class="legend-item"><span class="legend-dot" style="background:#64748b"></span>Nao informado</span>
      </div>
    </section>
    {''.join(secoes)}
  </main>
  <script>
    window.__CALENDARIO_EVENTOS__ = {linhas_json};
        const toggleDetails = document.getElementById('toggle-details');

        function aplicarVisaoCalendario() {{
            document.body.classList.toggle('compact-view', !toggleDetails.checked);
        }}

        toggleDetails.addEventListener('change', aplicarVisaoCalendario);
        aplicarVisaoCalendario();
  </script>
</body>
</html>
"""


def salvar_html(conteudo: str, caminho_saida: str) -> None:
    with open(caminho_saida, "w", encoding="utf-8") as arquivo:
        arquivo.write(conteudo)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera um calendario de eventos por local e faixa de publico em HTML."
    )
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=DEFAULT_CSV_PATH,
        help="Caminho do CSV local com os eventos.",
    )
    parser.add_argument(
        "--html-output",
        default=DEFAULT_HTML_OUTPUT,
        help="Arquivo HTML de saida.",
    )
    grupo_recorte = parser.add_mutually_exclusive_group()
    grupo_recorte.add_argument(
        "--mes",
        default=None,
        help="Gera somente um mes no formato AAAA-MM.",
    )
    grupo_recorte.add_argument(
        "--intervalo-meses",
        nargs=2,
        metavar=("INICIO", "FIM"),
        default=None,
        help="Gera um intervalo de meses no formato AAAA-MM AAAA-MM.",
    )
    grupo_recorte.add_argument(
        "--ano",
        type=int,
        default=None,
        help="Gera somente um ano completo (ex.: 2026).",
    )
    grupo_recorte.add_argument(
        "--semestre",
        default=None,
        help="Gera um semestre no formato AAAA-1 ou AAAA-2.",
    )
    args = parser.parse_args()

    try:
        recorte = construir_recorte_geracao(args)
    except ValueError as exc:
        parser.error(str(exc))

    filtro_descricao = recorte[2] if recorte is not None else "Completo (todos os registros)"

    registros = padronizar_registros(ler_registros_csv_local(args.csv_path))
    linhas, anos = construir_linhas_calendario(registros, recorte=recorte)
    html = montar_html(linhas, anos, filtro_descricao, recorte=recorte)
    salvar_html(html, args.html_output)

    print(f"Total de linhas no calendario (local + faixa): {len(linhas)}")
    print(f"Anos encontrados: {', '.join(str(ano) for ano in anos) if anos else '-'}")
    print(f"Calendario HTML salvo em: {args.html_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
