from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
import requests

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APPS_SCRIPT_URL = os.getenv("APPS_SCRIPT_URL", "").strip()

app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")


def _validar_apps_script_url() -> None:
    if not APPS_SCRIPT_URL:
        raise ValueError("Defina APPS_SCRIPT_URL no .env.")
    if APPS_SCRIPT_URL.endswith("/dev"):
        raise ValueError(
            "APPS_SCRIPT_URL esta em /dev. Publique como Aplicativo da Web e use a URL /exec."
        )


def _post_apps_script(payload: dict[str, str]) -> dict[str, Any]:
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


@app.get("/<path:filename>")
def static_files(filename: str) -> Any:
    return send_from_directory(BASE_DIR, filename)


if __name__ == "__main__":
    app.run(debug=True)
