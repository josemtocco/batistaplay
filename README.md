# Batista Play → M3U para SS IPTV — versão 2

Esta versão usa um navegador Chromium real no GitHub Actions.

Isso é importante porque o Batista Play não está entregando os streams diretamente no HTML que o crawler HTTP simples consegue enxergar. A versão 2 também analisa:

- HTML renderizado;
- links internos;
- páginas individuais de canais;
- iframes;
- elementos `video`/`source`;
- scripts;
- URLs HLS/DASH;
- requisições de rede feitas pelo player;
- botões/elementos de reprodução.

Depois testa os streams encontrados e grava somente os ativos.

## Atualização

O GitHub Actions executa a cada 6 horas e também pode ser executado manualmente.

## Arquivos gerados na raiz

- `batistaplay.m3u`
- `descobertos.json`
- `atualizacao.log`

## SS IPTV

Depois de publicar no GitHub:

`https://raw.githubusercontent.com/josemtocco/batistaplay/main/batistaplay.m3u`

## Observação

O primeiro teste deve ser feito em:

GitHub → Actions → Atualizar Batista Play M3U → Run workflow.

O arquivo `descobertos.json` mostrará exatamente quantas páginas foram analisadas, quantos streams foram encontrados e quantos passaram no teste.
