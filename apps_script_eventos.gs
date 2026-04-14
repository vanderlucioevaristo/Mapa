function normalizarValor(valor) {
  if (valor === null || valor === undefined) return "";

  if (Object.prototype.toString.call(valor) === "[object Date]" && !isNaN(valor.getTime())) {
    return Utilities.formatDate(valor, Session.getScriptTimeZone(), "dd/MM/yyyy");
  }

  const texto = String(valor).trim();
  if (!texto) return "";

  if (/^\d{4}-\d{2}-\d{2}$/.test(texto)) {
    const partes = texto.split("-");
    return partes[2] + "/" + partes[1] + "/" + partes[0];
  }

  if (/^\d{2}\/\d{2}\/\d{4}$/.test(texto)) {
    return texto;
  }

  return texto;
}

function eventoParaLinha(evento) {
  const item = evento || {};
  return [
    normalizarValor(item.DESCRICAO),
    normalizarValor(item.LOCAL),
    normalizarValor(item.ENDERECOLOCAL),
    normalizarValor(item.DATAINICIO),
    normalizarValor(item.DATAFIM),
    normalizarValor(item.ENTIDADE),
    normalizarValor(item.ESTIMATIVAPUBLICO)
  ];
}

function linhaIgual(linhaPlanilha, linhaEsperada) {
  for (var i = 0; i < linhaEsperada.length; i += 1) {
    if (normalizarValor(linhaPlanilha[i]) !== normalizarValor(linhaEsperada[i])) {
      return false;
    }
  }
  return true;
}

function localizarLinha(aba, eventoAnterior) {
  const ultimaLinha = aba.getLastRow();
  if (ultimaLinha < 2) return -1;

  const linhaEsperada = eventoParaLinha(eventoAnterior);
  const linhas = aba.getRange(2, 1, ultimaLinha - 1, 7).getValues();

  for (var i = 0; i < linhas.length; i += 1) {
    if (linhaIgual(linhas[i], linhaEsperada)) {
      return i + 2;
    }
  }

  return -1;
}

function doPost(e) {
  try {
    const planilha = SpreadsheetApp.openById("1BuXBFWZ396pSujh5ERjpDpAr850lGfZwqwzXquIPzgU");
    const aba = planilha.getSheetByName("Eventos") || planilha.getSheets()[0];
    const body = JSON.parse((e && e.postData && e.postData.contents) || "{}");
    const acao = String(body.ACAO || body.acao || "criar").toLowerCase();

    if (acao === "criar") {
      aba.appendRow(eventoParaLinha(body));
      return ContentService
        .createTextOutput(JSON.stringify({ ok: true, mensagem: "Evento salvo com sucesso." }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    if (acao === "atualizar") {
      const linha = localizarLinha(aba, body.EVENTO_ANTERIOR);
      if (linha === -1) {
        throw new Error("Evento original nao encontrado na planilha.");
      }

      aba.getRange(linha, 1, 1, 7).setValues([eventoParaLinha(body.EVENTO_ATUALIZADO)]);
      return ContentService
        .createTextOutput(JSON.stringify({ ok: true, mensagem: "Evento atualizado com sucesso." }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    if (acao === "excluir") {
      const linha = localizarLinha(aba, body.EVENTO_ANTERIOR);
      if (linha === -1) {
        throw new Error("Evento original nao encontrado na planilha.");
      }

      aba.deleteRow(linha);
      return ContentService
        .createTextOutput(JSON.stringify({ ok: true, mensagem: "Evento excluido com sucesso." }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    throw new Error("Acao nao suportada: " + acao);
  } catch (erro) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, erro: String(erro) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}