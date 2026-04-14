from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
import requests

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APPS_SCRIPT_URL = os.getenv("APPS_SCRIPT_URL", "").strip()
APPS_SCRIPT_MUTATIONS_ENABLED = os.getenv("APPS_SCRIPT_MUTATIONS_ENABLED", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
CSV_PATH = os.path.join(BASE_DIR, "eventos_bh.csv")
CSV_FIELDS = [
    "DATA",
    "DATAFINAL",
    "DESCRICAO",
    "ENDERECO",
    "ESTIMATIVAPUBLICO",
    "NOMELOCAL",
    "RESPONSAVEL",
]

app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")


def _normalizar_data_para_csv(valor: str, *, obrigatoria: bool = False) -> str:
    texto = (valor or "").strip()
    if not texto:
        if obrigatoria:
            raise ValueError("DATA obrigatoria.")
        return ""

    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).strftime("%d/%m/%Y")
        except ValueError:
            continue

    raise ValueError("Data invalida. Use AAAA-MM-DD ou DD/MM/AAAA.")


def _ler_eventos_csv() -> list[dict[str, str]]:
    if not os.path.exists(CSV_PATH):
        return []

    with open(CSV_PATH, "r", encoding="utf-8-sig", newline="") as arquivo:
        leitor = csv.DictReader(arquivo, delimiter=";")
        eventos: list[dict[str, str]] = []
        for linha in leitor:
            eventos.append({campo: (linha.get(campo) or "").strip() for campo in CSV_FIELDS})
        return eventos


def _escrever_eventos_csv(eventos: list[dict[str, str]]) -> None:
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=CSV_FIELDS, delimiter=";")
        escritor.writeheader()
        for evento in eventos:
            escritor.writerow({campo: (evento.get(campo) or "").strip() for campo in CSV_FIELDS})


def _serializar_evento(evento: dict[str, str], indice: int) -> dict[str, Any]:
    return {
        "id": indice,
        "data": evento.get("DATA", ""),
        "dataFinal": evento.get("DATAFINAL", ""),
        "descricao": evento.get("DESCRICAO", ""),
        "endereco": evento.get("ENDERECO", ""),
        "estimativaPublico": evento.get("ESTIMATIVAPUBLICO", ""),
        "nomeLocal": evento.get("NOMELOCAL", ""),
        "responsavel": evento.get("RESPONSAVEL", ""),
    }


def _validar_payload_evento_local(payload: dict[str, Any]) -> dict[str, str]:
    descricao = (payload.get("descricao") or "").strip()
    nome_local = (payload.get("nomeLocal") or payload.get("local") or "").strip()
    endereco = (payload.get("endereco") or payload.get("enderecoLocal") or "").strip()
    estimativa_publico = (payload.get("estimativaPublico") or "").strip()
    responsavel = (payload.get("responsavel") or payload.get("entidade") or "").strip()
    data = _normalizar_data_para_csv(payload.get("data") or payload.get("dataInicio") or "", obrigatoria=True)
    data_final = _normalizar_data_para_csv(payload.get("dataFinal") or payload.get("dataFim") or "")

    if not descricao or not nome_local or not endereco:
        raise ValueError("Preencha descricao, local e endereco.")

    if data_final:
        data_inicio_dt = datetime.strptime(data, "%d/%m/%Y")
        data_final_dt = datetime.strptime(data_final, "%d/%m/%Y")
        if data_final_dt < data_inicio_dt:
            raise ValueError("DATAFINAL nao pode ser menor que DATA.")

    return {
        "DATA": data,
        "DATAFINAL": data_final,
        "DESCRICAO": descricao,
        "ENDERECO": endereco,
        "ESTIMATIVAPUBLICO": estimativa_publico,
        "NOMELOCAL": nome_local,
        "RESPONSAVEL": responsavel,
    }


def _validar_apps_script_url() -> None:
    if not APPS_SCRIPT_URL:
        raise ValueError("Defina APPS_SCRIPT_URL no .env.")
    if APPS_SCRIPT_URL.endswith("/dev"):
        raise ValueError(
            "APPS_SCRIPT_URL esta em /dev. Publique como Aplicativo da Web e use a URL /exec."
        )


def _post_apps_script(payload: dict[str, Any]) -> dict[str, Any]:
    _validar_apps_script_url()

    resposta = requests.post(
        APPS_SCRIPT_URL,
        json=payload,
        timeout=20,
        allow_redirects=False,
    )

    if resposta.status_code in {301, 302, 303, 307, 308}:
        redirect_url = resposta.headers.get("Location")
        if not redirect_url:
            resposta.raise_for_status()
        if resposta.status_code in {301, 302, 303}:
            resposta = requests.get(redirect_url, timeout=20)
        else:
            resposta = requests.post(redirect_url, json=payload, timeout=20)

    resposta.raise_for_status()
    data = resposta.json()
    if isinstance(data, dict):
        return data
    return {"ok": True, "mensagem": "Registro enviado.", "raw": data}


def _evento_csv_para_apps_script(evento: dict[str, str]) -> dict[str, str]:
    return {
        "DESCRICAO": evento.get("DESCRICAO", ""),
        "LOCAL": evento.get("NOMELOCAL", ""),
        "ENDERECOLOCAL": evento.get("ENDERECO", ""),
        "DATAINICIO": evento.get("DATA", ""),
        "DATAFIM": evento.get("DATAFINAL", ""),
        "ENTIDADE": evento.get("RESPONSAVEL", ""),
        "ESTIMATIVAPUBLICO": evento.get("ESTIMATIVAPUBLICO", ""),
    }


def _sincronizar_mutacao_planilha(
    acao: str,
    evento_anterior: dict[str, str],
    evento_atualizado: dict[str, str] | None = None,
) -> bool:
    if not APPS_SCRIPT_MUTATIONS_ENABLED:
        return False

    payload: dict[str, Any] = {
        "ACAO": acao,
        "EVENTO_ANTERIOR": _evento_csv_para_apps_script(evento_anterior),
    }
    if evento_atualizado is not None:
        payload["EVENTO_ATUALIZADO"] = _evento_csv_para_apps_script(evento_atualizado)

    resultado = _post_apps_script(payload)
    if resultado.get("ok") is False:
        raise ValueError(resultado.get("erro", "Falha ao sincronizar alteracao na planilha."))

    return True


@app.get("/")
def home() -> Any:
    return send_from_directory(BASE_DIR, "index.html")


@app.post("/api/eventos")
def salvar_evento() -> Any:
    payload = request.get_json(silent=True) or {}

    descricao = (payload.get("descricao") or "").strip()
    local = (payload.get("local") or "").strip()
    endereco_local = (payload.get("enderecoLocal") or "").strip()
    data_inicio = (payload.get("dataInicio") or "").strip()
    data_fim = (payload.get("dataFim") or "").strip()
    entidade = (payload.get("entidade") or "").strip()
    estimativa_publico = (payload.get("estimativaPublico") or "").strip()

    if not all([descricao, local, endereco_local, data_inicio]):
        return jsonify({"ok": False, "erro": "Preencha os campos obrigatorios."}), 400

    try:
        datetime.strptime(data_inicio, "%Y-%m-%d")
    except ValueError:
        return jsonify({"ok": False, "erro": "DATAINICIO invalida. Use AAAA-MM-DD."}), 400

    if data_fim:
        try:
            datetime.strptime(data_fim, "%Y-%m-%d")
        except ValueError:
            return jsonify({"ok": False, "erro": "DATAFIM invalida. Use AAAA-MM-DD."}), 400

    linha = [
        descricao,
        local,
        endereco_local,
        data_inicio,
        data_fim,
        entidade,
        estimativa_publico,
    ]

    payload_apps_script = {
        "DESCRICAO": descricao,
        "LOCAL": local,
        "ENDERECOLOCAL": endereco_local,
        "DATAINICIO": data_inicio,
        "DATAFIM": data_fim,
        "ENTIDADE": entidade,
        "ESTIMATIVAPUBLICO": estimativa_publico,
    }

    try:
        resultado = _post_apps_script(payload_apps_script)
        if resultado.get("ok") is False:
            raise ValueError(resultado.get("erro", "Falha ao salvar via Apps Script."))
    except Exception as exc:
        return (
            jsonify(
                {
                    "ok": False,
                    "erro": "Nao foi possivel salvar na planilha via Apps Script.",
                    "detalhe": str(exc),
                }
            ),
            500,
        )

    return jsonify({"ok": True, "mensagem": "Evento cadastrado com sucesso."}), 201


@app.get("/api/eventos/local")
def listar_eventos_local() -> Any:
    eventos = _ler_eventos_csv()
    return jsonify({"ok": True, "eventos": [_serializar_evento(evento, indice) for indice, evento in enumerate(eventos)]})


@app.put("/api/eventos/local/<int:evento_id>")
def atualizar_evento_local(evento_id: int) -> Any:
    eventos = _ler_eventos_csv()
    if evento_id < 0 or evento_id >= len(eventos):
        return jsonify({"ok": False, "erro": "Evento nao encontrado."}), 404

    payload = request.get_json(silent=True) or {}
    try:
        evento_anterior = dict(eventos[evento_id])
        evento_atualizado = _validar_payload_evento_local(payload)
        sincronizado_planilha = _sincronizar_mutacao_planilha(
            "atualizar",
            evento_anterior,
            evento_atualizado,
        )
        eventos[evento_id] = evento_atualizado
        _escrever_eventos_csv(eventos)
    except ValueError as exc:
        return jsonify({"ok": False, "erro": str(exc)}), 400
    except OSError as exc:
        return jsonify({"ok": False, "erro": "Falha ao salvar o CSV.", "detalhe": str(exc)}), 500
    except Exception as exc:
        return jsonify({"ok": False, "erro": "Falha ao sincronizar com a planilha.", "detalhe": str(exc)}), 502

    mensagem = "Evento atualizado com sucesso."
    if not sincronizado_planilha:
        mensagem = "Evento atualizado no CSV local. Para sincronizar com a planilha, publique o Apps Script atualizado e ative APPS_SCRIPT_MUTATIONS_ENABLED."

    return jsonify({"ok": True, "mensagem": mensagem, "sincronizadoPlanilha": sincronizado_planilha})


@app.delete("/api/eventos/local/<int:evento_id>")
def excluir_evento_local(evento_id: int) -> Any:
    eventos = _ler_eventos_csv()
    if evento_id < 0 or evento_id >= len(eventos):
        return jsonify({"ok": False, "erro": "Evento nao encontrado."}), 404

    try:
        evento_anterior = dict(eventos[evento_id])
        sincronizado_planilha = _sincronizar_mutacao_planilha("excluir", evento_anterior)
        eventos.pop(evento_id)
        _escrever_eventos_csv(eventos)
    except OSError as exc:
        return jsonify({"ok": False, "erro": "Falha ao salvar o CSV.", "detalhe": str(exc)}), 500
    except Exception as exc:
        return jsonify({"ok": False, "erro": "Falha ao sincronizar exclusao com a planilha.", "detalhe": str(exc)}), 502

    mensagem = "Evento excluido com sucesso."
    if not sincronizado_planilha:
        mensagem = "Evento excluido no CSV local. Para sincronizar com a planilha, publique o Apps Script atualizado e ative APPS_SCRIPT_MUTATIONS_ENABLED."

    return jsonify({"ok": True, "mensagem": mensagem, "sincronizadoPlanilha": sincronizado_planilha})


@app.get("/<path:filename>")
def static_files(filename: str) -> Any:
    return send_from_directory(BASE_DIR, filename)


if __name__ == "__main__":
    app.run(debug=True)
