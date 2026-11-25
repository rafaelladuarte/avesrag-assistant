// Página do beta (plano_assistant4.md §5). JS puro, sem build. Todo texto vindo da API entra
// por textContent, nunca por innerHTML.
(function () {
  "use strict";

  var raiz = document.getElementById("app");
  var CHAVE = "avesrag_sessao";
  var LIMITE = 1000;
  var EXEMPLOS = [
    "Azul brilhante, bico preto, comendo frutas",
    "Marrom, andando no chão, perto de um ninho de barro",
    "Grande e preta, voando em círculos",
  ];
  var estado = {
    visao: null, // a última resposta da API
    tela: "inicio", // inicio | conversa | detalhe
    detalhe: null,
    rascunho: "",
    local: "",
    ocupado: false,
    etapa: 0, // etapa do indicador de espera
    erro: null, // { texto, repetir }
  };
  var timers = [];

  // -- utilidades ---------------------------------------------------------------

  function el(tag, attrs) {
    var no = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "class") no.className = v;
      else if (k === "texto") no.textContent = v;
      else if (k.slice(0, 2) === "on") no.addEventListener(k.slice(2), v);
      else no.setAttribute(k, v === true ? "" : v);
    });
    function juntar(f) {
      if (f === null || f === undefined || f === false) return;
      if (Array.isArray(f)) return f.forEach(juntar);
      no.appendChild(typeof f === "string" ? document.createTextNode(f) : f);
    }
    for (var i = 2; i < arguments.length; i++) juntar(arguments[i]);
    return no;
  }

  function icone(caminhos, tam) {
    var s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("width", tam || 18);
    s.setAttribute("height", tam || 18);
    s.setAttribute("fill", "none");
    s.setAttribute("stroke", "currentColor");
    s.setAttribute("stroke-width", "2");
    s.setAttribute("stroke-linecap", "round");
    s.setAttribute("stroke-linejoin", "round");
    s.setAttribute("aria-hidden", "true");
    caminhos.forEach(function (d) {
      var p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", d);
      s.appendChild(p);
    });
    return s;
  }
  var SETA = ["M5 12h14", "m13 6 6 6-6 6"];
  var VOLTAR = ["M19 12H5", "m11 18-6-6 6-6"];

  function guardar(id) {
    try {
      if (id) sessionStorage.setItem(CHAVE, id);
      else sessionStorage.removeItem(CHAVE);
    } catch (e) {}
  }
  function lembrada() {
    try {
      return sessionStorage.getItem(CHAVE);
    } catch (e) {
      return null;
    }
  }

  function api(metodo, caminho, corpo) {
    var opcoes = { method: metodo, credentials: "same-origin", headers: {} };
    if (corpo !== undefined) {
      opcoes.headers["Content-Type"] = "application/json";
      opcoes.body = JSON.stringify(corpo);
    }
    return fetch(caminho, opcoes).then(function (r) {
      return r.text().then(function (t) {
        var dados = null;
        try {
          dados = t ? JSON.parse(t) : null;
        } catch (e) {}
        if (!r.ok) {
          var erro = new Error((dados && dados.erro) || "Tive um problema agora. Tente de novo em instantes.");
          erro.status = r.status;
          throw erro;
        }
        return dados;
      });
    });
  }

  function limparTimers() {
    timers.forEach(clearTimeout);
    timers = [];
  }

  function aoEsperar() {
    limparTimers();
    estado.etapa = 0;
    timers.push(
      setTimeout(function () {
        estado.etapa = 1;
        desenhar();
      }, 1200)
    );
  }

  // -- ações --------------------------------------------------------------------

  function enviar(texto) {
    texto = (texto || "").trim();
    if (!texto || estado.ocupado) return;
    estado.ocupado = true;
    estado.erro = null;
    estado.tela = "conversa";
    var previa = estado.visao;
    if (previa) {
      previa = JSON.parse(JSON.stringify(previa));
      previa.mensagens.push({ papel: "usuario", texto: texto });
      previa.pergunta = null;
      previa.candidatas = [];
      estado.visao = previa;
    }
    aoEsperar();
    desenhar();
    var passo = estado.visao && estado.visao.id
      ? Promise.resolve(estado.visao.id)
      : api("POST", "/api/sessoes").then(function (v) {
          estado.visao = v;
          guardar(v.id);
          return v.id;
        });
    passo
      .then(function (id) {
        return api("POST", "/api/sessoes/" + encodeURIComponent(id) + "/mensagens", { texto: texto });
      })
      .then(function (v) {
        estado.visao = v;
        estado.rascunho = "";
      })
      .catch(function (e) {
        if (e.status === 404) {
          guardar(null);
          estado.visao = null;
          estado.tela = "inicio";
        }
        estado.erro = { texto: e.message, repetir: e.status === 404 ? null : texto };
        if (estado.visao && estado.visao.mensagens.length && estado.visao.mensagens.slice(-1)[0].papel === "usuario") {
          estado.visao.mensagens.pop(); // a mensagem não chegou: sai da tela, vai para o campo
          estado.rascunho = texto;
        }
      })
      .then(function () {
        estado.ocupado = false;
        limparTimers();
        desenhar();
        var alvo = document.querySelector("[data-foco]");
        if (alvo) alvo.focus();
      });
  }

  function novaConversa() {
    guardar(null);
    limparTimers();
    estado.visao = null;
    estado.detalhe = null;
    estado.tela = "inicio";
    estado.rascunho = "";
    estado.erro = null;
    desenhar();
    window.scrollTo(0, 0);
  }

  function abrirDetalhe(speciesId) {
    estado.erro = null;
    api("GET", "/api/sessoes/" + encodeURIComponent(estado.visao.id) + "/especies/" + encodeURIComponent(speciesId))
      .then(function (d) {
        estado.detalhe = d;
        estado.tela = "detalhe";
        desenhar();
        window.scrollTo(0, 0);
      })
      .catch(function (e) {
        estado.erro = { texto: e.message, repetir: null };
        desenhar();
      });
  }

  function votar(voto) {
    api("POST", "/api/sessoes/" + encodeURIComponent(estado.visao.id) + "/voto", { voto: voto })
      .then(function (v) {
        estado.visao = v;
        desenhar();
      })
      .catch(function (e) {
        estado.erro = { texto: e.message, repetir: null };
        desenhar();
      });
  }

  // -- peças --------------------------------------------------------------------

  function aviso() {
    return el(
      "div",
      { class: "escopo", role: "note" },
      icone(["M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z", "M12 7.5v3", "M12 13h.01"], 20),
      el("span", {
        texto:
          "Conheço 387 aves de Uberlândia (MG). Se você viu a ave em outro lugar, as sugestões podem estar erradas.",
      })
    );
  }

  function blocoErro() {
    if (!estado.erro) return null;
    var e = estado.erro;
    return el(
      "div",
      { class: "erro", role: "alert" },
      el("span", { texto: e.texto }),
      el(
        "div",
        { class: "acoes" },
        e.repetir ? el("button", { class: "botao botao-pequeno", type: "button", onclick: function () { enviar(e.repetir); } }, "Tentar de novo") : null,
        el("button", { class: "link-botao", type: "button", onclick: novaConversa }, "Começar de novo")
      )
    );
  }

  function foto(f, texto) {
    var caixa = el("div", { class: "foto" });
    if (f) {
      caixa.appendChild(el("img", { src: f.url, alt: texto || "", loading: "lazy", referrerpolicy: "no-referrer" }));
      caixa.appendChild(
        el("div", { class: "credito" }, el("a", { href: f.link, target: "_blank", rel: "noopener noreferrer" }, f.credito))
      );
    } else {
      caixa.appendChild(el("span", { texto: "Sem foto" }));
    }
    return caixa;
  }

  function espera() {
    var passos = ["Lendo a sua descrição", "Buscando na base de espécies"];
    var linhas = passos.map(function (t, i) {
      var feita = i < estado.etapa;
      var atual = i === estado.etapa;
      return el(
        "div",
        { class: "etapa" + (!feita && !atual ? " pendente" : "") },
        feita ? icone(["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "m8 12 3 3 5-6"], 20) : atual ? el("span", { class: "girar", "aria-hidden": "true" }) : el("span", { class: "vazio", "aria-hidden": "true" }),
        t
      );
    });
    return el("div", { class: "espera", "aria-live": "polite" }, el("span", { class: "titulo", texto: "Procurando a sua ave" }), linhas);
  }

  // -- telas --------------------------------------------------------------------

  function telaInicio() {
    var area = el("textarea", {
      id: "descricao", rows: "4", maxlength: String(LIMITE), "data-foco": "1",
      placeholder: "Pequena, barriga amarela, uma faixa branca na cabeça. Estava num fio gritando alto.",
      oninput: function () { estado.rascunho = area.value; contagem.textContent = area.value.length + " / " + LIMITE; },
    });
    area.value = estado.rascunho;
    var contagem = el("div", { class: "contador", texto: area.value.length + " / " + LIMITE });
    var local = el("input", {
      id: "local", type: "text", maxlength: "80", placeholder: "Cidade, bairro ou parque",
      oninput: function () { estado.local = local.value; },
    });
    local.value = estado.local;
    var botao = el("button", { class: "botao", type: "submit", disabled: estado.ocupado }, "Identificar ave", icone(SETA, 20));
    var form = el(
      "form",
      {
        class: "campos",
        onsubmit: function (ev) {
          ev.preventDefault();
          var texto = area.value.trim();
          if (!texto) { area.focus(); return; }
          if (estado.local.trim()) texto += " Vi em " + estado.local.trim() + ".";
          enviar(texto);
        },
      },
      el(
        "div", { class: "campo" },
        el("label", { for: "descricao" }, "Descrição da ave"), area, contagem,
        el(
          "div", { class: "exemplos" },
          el("span", { texto: "Sem ideia de como começar? Use um exemplo:" }),
          EXEMPLOS.map(function (t) {
            return el("button", { class: "chip", type: "button", onclick: function () { area.value = t; estado.rascunho = t; contagem.textContent = t.length + " / " + LIMITE; area.focus(); } }, t);
          })
        )
      ),
      el("div", { class: "campo" }, el("label", { for: "local" }, "Onde você viu (opcional)"), local),
      el("div", { class: "acoes" }, botao, el("span", { class: "nota", texto: "Quanto mais detalhes, melhor: cores, tamanho, bico e o que a ave fazia." }))
    );
    var como = el(
      "aside", { class: "como" },
      el("div", { class: "ilustracao", "aria-hidden": "true" },
        icone(["M3 17c3-1 5-3 6-6 1-3 3-5 7-5h5l-3 3c0 5-4 10-10 10H3z", "M9 11l-5 6"], 96)),
      el("div", {},
        el("h2", { texto: "Como funciona" }),
        el("ol", {},
          el("li", {}, el("b", { texto: "1" }), "Você descreve o que viu, do seu jeito."),
          el("li", {}, el("b", { texto: "2" }), "Se faltar algo, o AvesRAG faz até 3 perguntas."),
          el("li", {}, el("b", { texto: "3" }), "Você recebe 3 candidatas e confirma qual é.")
        )
      )
    );
    return el(
      "div", {},
      aviso(), blocoErro(),
      el("div", { class: "inicio" },
        el("div", { class: "inicio-form" },
          el("div", {}, el("h1", { texto: "Que ave você viu?" }),
            el("p", { class: "sub", texto: "Descreva com suas palavras: cores, tamanho, o que ela fazia e onde estava. Não precisa de foto nem de gravação." })),
          form),
        como)
    );
  }

  function cartaoPrincipal(c) {
    return el(
      "article", { class: "principal" },
      (function () { var f = foto(c.foto, c.nome); f.classList.add("grande"); f.appendChild(el("span", { class: "posicao", texto: "1ª candidata" })); return f; })(),
      el("div", { class: "corpo" },
        el("div", { class: "nome" }, el("h2", { texto: c.nome }), el("span", { class: "cient", texto: c.nome_cientifico })),
        el("div", { class: "dois" },
          el("div", { class: "combina" }, el("b", { texto: "Por que combina" }), el("span", { texto: c.combina.length ? "Bate com o que você contou: " + c.combina.join(", ") + "." : "Sem atributos para comparar ainda." })),
          el("div", { class: "nao" }, el("b", { texto: "Atenção" }), el("span", { texto: c.nao_combina.length ? "Não bate com: " + c.nao_combina.join(", ") + "." : "Nada do que você contou contradiz a ficha." }))
        ),
        el("div", { class: "botoes" },
          el("button", { class: "botao botao-pequeno", type: "button", onclick: function () { enviar(String(c.numero)); } }, "É essa"),
          el("button", { class: "botao-contorno", type: "button", onclick: function () { abrirDetalhe(c.species_id); } }, "Ver detalhes")))
    );
  }

  function cartaoOutra(c) {
    var f = foto(c.foto, c.nome);
    return el(
      "article", { class: "outra" }, f,
      el("div", { class: "corpo" },
        el("div", { class: "nome" }, el("span", { class: "pos", texto: c.numero + "ª candidata" }), el("h2", { texto: c.nome }), el("span", { class: "cient", texto: c.nome_cientifico })),
        el("p", { class: "restantes", texto: c.combina.length ? "Combina: " + c.combina.join(", ") + "." : "" }),
        el("div", { class: "botoes" },
          el("button", { class: "botao botao-pequeno", type: "button", onclick: function () { enviar(String(c.numero)); } }, "É essa"),
          el("button", { class: "botao-contorno", type: "button", onclick: function () { abrirDetalhe(c.species_id); } }, "Ver detalhes")))
    );
  }

  function campoMensagem(placeholder) {
    var v = estado.visao;
    var sem = v && v.mensagens_restantes <= 0;
    var entrada = el("input", {
      id: "msg", type: "text", maxlength: String(LIMITE), autocomplete: "off", "data-foco": "1",
      placeholder: placeholder, disabled: estado.ocupado || sem,
    });
    entrada.value = estado.rascunho;
    entrada.addEventListener("input", function () { estado.rascunho = entrada.value; });
    return el(
      "form",
      { class: "enviar", onsubmit: function (ev) { ev.preventDefault(); enviar(entrada.value); } },
      el("div", { class: "campo" }, el("label", { for: "msg", texto: "Sua resposta" }), entrada),
      el("button", { type: "submit", "aria-label": "Enviar", disabled: estado.ocupado || sem }, icone(SETA, 20))
    );
  }

  function telaConversa() {
    var v = estado.visao;
    var partes = [aviso()];
    var topo = el("div", { class: "faixa" },
      el("span", { texto: "Uberlândia, MG" }),
      el("button", { class: "link-botao", type: "button", onclick: novaConversa }, "Identificar outra ave"));
    partes.push(topo);
    var corpo = el("div", { class: "conversa" });
    (v ? v.mensagens : []).forEach(function (m, i) {
      if (i === 0 && m.papel === "assistente") return; // a saudação não precisa na tela
      // o texto que lista as 3 candidatas repete os cartões
      var ultima = i === v.mensagens.length - 1 && m.papel === "assistente";
      if (v.etapa === "confirmacao" && ultima) return;
      // a pergunta aparece no cartão logo abaixo
      if (v.pergunta && ultima && !estado.ocupado) return;
      var texto = m.papel === "usuario" && /^[123]$/.test(m.texto.trim()) ? "É a " + m.texto.trim() + "ª candidata" : m.texto;
      corpo.appendChild(el("div", { class: "balao " + m.papel, texto: texto }));
    });
    if (estado.ocupado) corpo.appendChild(espera());
    var erro = blocoErro();
    if (erro) corpo.appendChild(erro);

    if (!estado.ocupado && v) {
      if (v.etapa === "coleta" && v.pergunta) {
        var p = v.pergunta;
        var pontos = el("div", { class: "pontos", "aria-hidden": "true" });
        for (var i = 1; i <= p.de; i++) pontos.appendChild(el("i", { class: i <= p.numero ? "on" : "" }));
        corpo.appendChild(
          el("div", { class: "pergunta" },
            el("div", { class: "passo" }, el("span", { texto: "Pergunta " + p.numero + " de " + p.de + ", no máximo" }), pontos),
            el("p", { class: "texto", texto: p.texto }),
            el("div", { class: "opcoes" }, p.opcoes.map(function (o) {
              return el("button", { class: "opcao", type: "button", onclick: function () { enviar(o); } }, o);
            })))
        );
        corpo.appendChild(campoMensagem("Ou responda com suas palavras"));
      } else if (v.etapa === "confirmacao") {
        var lista = el("div", { class: "candidatas" },
          el("p", { class: "intro", texto: "Encontrei 3 aves que combinam com a sua descrição. Toque em “É essa” na que você viu." }),
          v.candidatas.length ? cartaoPrincipal(v.candidatas[0]) : null,
          v.candidatas.length > 1 ? el("div", { class: "outras" }, v.candidatas.slice(1).map(cartaoOutra)) : null,
          el("div", { class: "nenhuma" },
            el("span", { texto: "Não é nenhuma delas? Conte mais um detalhe e a busca é refeita." }),
            el("button", { class: "botao-contorno", type: "button", onclick: function () { enviar("não"); } }, "Não é nenhuma")));
        corpo.appendChild(lista);
      } else if (v.etapa === "fim") {
        corpo.appendChild(fim(v));
      } else {
        corpo.appendChild(campoMensagem("Escreva aqui"));
      }
      if (v.etapa !== "fim" && v.mensagens_restantes <= 5) {
        corpo.appendChild(el("p", { class: "restantes", texto: v.mensagens_restantes > 0 ? "Restam " + v.mensagens_restantes + " mensagens nesta conversa." : "Esta conversa chegou ao limite de mensagens." }));
      }
    }
    partes.push(corpo);
    return el("div", {}, partes);
  }

  function fim(v) {
    if (v.confirmada) {
      return el("div", { class: "pergunta" },
        el("div", { class: "confirmada" }, icone(["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "m8 12 3 3 5-6"], 22),
          el("span", { texto: "Identificação confirmada: " + v.confirmada.nome + " (" + v.confirmada.nome_cientifico + ")." })),
        el("div", { class: "botoes" },
          el("button", { class: "botao botao-pequeno", type: "button", onclick: function () { abrirDetalhe(v.confirmada.species_id); } }, "Ver a ficha da espécie"),
          el("button", { class: "botao-contorno", type: "button", onclick: novaConversa }, "Identificar outra ave")),
        feedback(v));
    }
    return el("div", { class: "pergunta" },
      el("p", { class: "texto", texto: "Não consegui identificar a ave desta vez." }),
      el("div", { class: "botoes" }, el("button", { class: "botao botao-pequeno", type: "button", onclick: novaConversa }, "Identificar outra ave")));
  }

  function feedback(v) {
    if (v.voto) {
      return el("div", { class: "feedback" }, el("span", { texto: v.voto === "ajudou" ? "Obrigado. Sua resposta entra na avaliação do AvesRAG." : "Obrigado. Vamos usar isso para melhorar a busca." }));
    }
    return el("div", { class: "feedback" },
      el("span", { texto: "A identificação ajudou? Sua resposta melhora a base." }),
      el("div", { class: "botoes" },
        el("button", { class: "botao-contorno", type: "button", onclick: function () { votar("ajudou"); } }, "Ajudou"),
        el("button", { class: "botao-contorno", type: "button", onclick: function () { votar("nao_ajudou"); } }, "Não ajudou")));
  }

  function telaDetalhe() {
    var d = estado.detalhe;
    var v = estado.visao;
    var confirmada = v && v.confirmada && v.confirmada.species_id === d.species_id;
    var itens = function (lista) {
      var dl = el("dl", { class: "lista" });
      lista.forEach(function (i) { dl.appendChild(el("dt", { texto: i.rotulo })); dl.appendChild(el("dd", { texto: i.valor })); });
      return dl;
    };
    var fotos = el("section", { class: "fotos", "aria-label": "Foto e ilustração" });
    if (d.foto) {
      var f = foto(d.foto, d.nome);
      fotos.appendChild(el("figure", {}, f));
    }
    if (d.ilustracao) {
      var fi = el("div", { class: "foto" }, el("img", { src: d.ilustracao.url, alt: "Ilustração de " + d.nome, loading: "lazy", referrerpolicy: "no-referrer" }));
      fotos.appendChild(el("figure", {}, fi,
        el("figcaption", {}, d.ilustracao.rotulo + " · ", el("a", { href: d.ilustracao.link, target: "_blank", rel: "noopener noreferrer" }, "fonte"))));
    }
    return el("div", { class: "detalhe" },
      el("button", { class: "link-botao voltar", type: "button", onclick: function () { estado.tela = "conversa"; desenhar(); window.scrollTo(0, 0); } },
        icone(VOLTAR, 18), v && v.etapa === "fim" ? "Voltar" : "Voltar para as candidatas"),
      confirmada ? el("div", { class: "confirmada" }, icone(["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "m8 12 3 3 5-6"], 22),
        el("span", { texto: "Identificação confirmada: " + d.nome + "." }),
        el("button", { class: "link-botao", type: "button", onclick: novaConversa }, "Identificar outra ave")) : null,
      blocoErro(),
      el("section", { class: "topo" },
        el("div", { class: "nome" }, el("h1", { texto: d.nome }),
          el("span", { class: "cient", texto: d.nome_cientifico + (d.familia ? ", da família " + d.familia : "") }))),
      fotos,
      el("div", { class: "colunas" },
        el("section", {},
          d.como_reconhecer.length ? el("div", {}, el("h2", { texto: "Como reconhecer" }), itens(d.como_reconhecer)) : null,
          el("div", { class: "fontes" }, el("h2", { texto: "Fontes" }),
            el("ul", {},
              d.url_wikiaves ? el("li", {}, el("a", { href: d.url_wikiaves, target: "_blank", rel: "noopener noreferrer" }, "WikiAves"), ": página da espécie") : null,
              el("li", { texto: "AVONET, CBRO e eBird, para medidas, nomes e taxonomia" })),
            el("p", { texto: "As fotos pertencem aos seus autores e aparecem com crédito e link para a origem." }))),
        el("aside", {}, el("h2", { texto: "Ficha" }), d.ficha.length ? itens(d.ficha) : el("p", { texto: "Sem dados na ficha." })))
    );
  }

  // -- desenho ------------------------------------------------------------------

  function desenhar() {
    var ativo = document.activeElement;
    var id = ativo && ativo.id;
    var conteudo = estado.tela === "detalhe" && estado.detalhe ? telaDetalhe() : estado.tela === "conversa" ? telaConversa() : telaInicio();
    raiz.replaceChildren(conteudo);
    if (id) {
      var voltar = document.getElementById(id);
      if (voltar && voltar.focus) voltar.focus();
    }
  }

  document.getElementById("marca").addEventListener("click", function (ev) {
    ev.preventDefault();
    novaConversa();
  });

  // retoma a sessão ao recarregar a página
  var id = lembrada();
  if (id) {
    api("GET", "/api/sessoes/" + encodeURIComponent(id))
      .then(function (v) {
        estado.visao = v;
        estado.tela = v.mensagens.length > 1 ? "conversa" : "inicio";
        desenhar();
      })
      .catch(function () {
        guardar(null);
        desenhar();
      });
  } else {
    desenhar();
  }
})();
