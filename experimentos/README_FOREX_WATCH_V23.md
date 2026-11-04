# Forex Watch V2.3 — auditoria adversarial + monitor externo experimental

**Status: EXPERIMENTO. Não mesclar na main nem ativar o Worker sem passar nos testes e configurar uma conta Cloudflare.**

Arquivos:
- `experimentos/forex_watch_v23.py`: monitor Python puro, leitura de GitHub API público, CLI estruturado.
- `tests/test_forex_watch_v23.py`: 27 testes offline de falhas adversariais.
- `experimentos/forex_watch_v23_worker.mjs`: Cloudflare Worker independente, somente leitura, notificações Telegram condicionais.
- `tests/test_forex_watch_v23_worker.mjs`: 16 testes offline do Worker.
- `experimentos/wrangler_v23.toml`: **modelo** de deploy externo com Cron de 15 minutos.
- `.github/workflows/forex-watch-v23.yml`: executa **somente testes offline** na branch experimental; não publica Worker.

## O que monitora

- Execução GitHub Actions principal: `failure`, cancelamento, timeout, fila parada e execução em andamento além de 20 minutos.
- Último início agendado >30/75 minutos; intervalos recentes >30 minutos; histórico insuficiente; falha recente escondida por novo sucesso.
- `last_checked_at` / `updated_at`: com posição PAPER aberta, estado >35min = **CRITICO**.
- `last_candle_at`: em sessão regular aberta, >35 min = alerta; >75 min = crítico; com posição aberta e >35 min = crítico.
- Diferencia ausência de dados do GitHub de falha do robô: `INDETERMINADO` com código `MONITOR_UNAVAILABLE`.
- Detecta `gap_halt`, `data_gap`, `api_error`, respeita fuso `America/New_York` para fechamento regular forex sexta 17h até domingo 17h (grace de 45min na abertura).

**Limites:** não confunde *heartbeat* com preço de mercado. Feriados e pausas extraordinárias da fonte não são inferidos; podem gerar alertas legítimos para investigação, não prova de falha. Cron do GitHub e Cron do Cloudflare são best-effort, não relógios em tempo real. O monitor não detecta corrupção de `state.enc` nem gerencia stop/loss: apenas observa as informações públicas.

## Testar sem quaisquer chaves

Na raiz do repositório:
```bash
python -m unittest discover -s tests -p 'test_forex_watch_v23.py' -v
node --test tests/test_forex_watch_v23_worker.mjs
python experimentos/forex_watch_v23.py --json
```

Os dois primeiros comandos são offline. O último usa somente os endpoints públicos do GitHub (`state.json` público e lista de GitHub Actions). Código de saída Python: `0` OK/ATENCAO, `1` CRITICO, `2` INDETERMINADO (falha de verificação).

## Como preparar o monitor EXTERNO em Cloudflare (opt-in)

Não há integração com conta Cloudflare conectada nesta entrega. Estas etapas devem ocorrer **na conta do proprietário**, nunca em chats com secrets.

1. Faça login na Cloudflare; instale Node.js e Wrangler CLI, depois `npx wrangler login`.
2. Confira `experimentos/wrangler_v23.toml` (cron: `*/15 * * * *`, UTC). O Worker implementa `scheduled()`, conforme documentação Cloudflare.
3. Para habilitar alertas sem spam, crie uma namespace KV: `npx wrangler kv namespace create INCIDENT_KV --config experimentos/wrangler_v23.toml`.
4. Copie **somente o ID da KV** gerado para uma cópia local de `experimentos/wrangler_v23.toml`, removendo o comentário e configurando:
   ```toml
   [[kv_namespaces]]
   binding = "INCIDENT_KV"
   id = "ID_GERADO_PELA_CLOUDFLARE"
   ```
   **Não faça commit do arquivo com configurações de conta sem revisar.**
5. Crie um bot próprio do Telegram e identifique o chat de destino com segurança. Após o deploy, configure na Cloudflare: `npx wrangler secret put TELEGRAM_BOT_TOKEN --config experimentos/wrangler_v23.toml` e `npx wrangler secret put TELEGRAM_CHAT_ID --config experimentos/wrangler_v23.toml`. Nunca compartilhe os valores aqui.
6. Faça teste local com `npx wrangler dev --config experimentos/wrangler_v23.toml --test-scheduled` e execute o endpoint local `/cdn-cgi/local/scheduled` conforme a versão do Wrangler.
7. Com os testes offline aprovados, valide o segredo de Telegram e somente então: `npx wrangler deploy --config experimentos/wrangler_v23.toml`. Confirme o cron na Cloudflare e um alerta controlado de teste; monitore logs, limites e custos.

**Não existe deploy automático nem ligação da branch com produção.** A Cloudflare é uma infraestrutura externa independente do GitHub Actions, mas ainda consulta API pública do GitHub para detectar anomalias (uma falha na API é classificada como INDETERMINADO). Sem `INCIDENT_KV` e secrets do Telegram, o Worker apenas registra diagnósticos em logs; **não envia alertas**. Com KV, alertas repetidos equivalentes são suprimidos por até 4h, emitindo lembrete após esse período, mudanças relevantes e recuperação. Devido à consistência eventual do KV, alertas duplicados são possíveis em condições de corrida.

### Governança

- Somente PAPER-ONLY; nenhuma chamada a Twelve Data, ordens ou credenciais do Forex Bot.
- Não escrever em `v2/state.enc`, `v2/state.json` ou `main`.
- O monitor horário ChatGPT já configurado continua funcionando separadamente, e **não executa este novo Worker**.
- Para ativar a V2.3 em ambiente externo, é necessário consentimento e configuração do serviço pelo operador. É possível suspender o Cron no painel Cloudflare sem afetar a V2.1.
- Registrar em relatórios a diferença entre teste executado, análise estática e verificação live. Não declarar sucesso sem CI concluído.

Documentação Cloudflare:
- https://developers.cloudflare.com/workers/configuration/cron-triggers/
- https://developers.cloudflare.com/workers/runtime-apis/handlers/scheduled/
- https://developers.cloudflare.com/kv/get-started/

## Hardening pós-auditoria (branch `hardening-forex-watch-v23-20261104`)

- Na camada externa, buscar execuções **apenas** do workflow `.github/workflows/forex-v2.yml`, pelo endpoint GitHub `actions/workflows/forex-v2.yml/runs?branch=main`. Isso evita confundir Page builds com execuções do robô.
- `ALERT_MODE = "observe"` é o padrão seguro no modelo Wrangler. Nesse modo **não há envio de Telegram**. Para ativar em uma conta Cloudflare, alterar explicitamente para `active` e configurar `INCIDENT_KV`, `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`. Sem esses valores em modo `active`, o Cron falha com `ALERT_CHANNEL_NOT_CONFIGURED` em vez de fingir que enviou notificações.
- Em `active`, se o KV não puder ser lido durante um incidente, o monitor tenta enviar Telegram mesmo assim (**pode repetir alertas**, preferível ao silêncio). Se Telegram falhar, não registra o incidente como entregue. Verifica o JSON de sucesso `ok: true`, não só o HTTP 200.
- Recuperação exige **duas observações OK em horários distintos**, pelo menos 10 minutos separadas, com `last_checked_at` diferente. A recuperação não é enviada quando o KV fica inacessível.
- Base64/JSON inválido do estado público gera `INDETERMINADO` com código diagnóstico específico (não prova falha do robô).
- Deduplicação KV continua **best effort e não transacional**. Workers concorrentes podem mandar mensagens duplicadas. Para *exactly-once* forte, desenhar Durable Objects e testes de simultaneidade antes de declarar garantia.
- GitHub e Telegram continuam dependências externas; o monitor alerta apenas sobre o estado **PAPER ONLY**, não executa stop nem operação real.
- Sem uma conta externa configurada e teste real de entrega, esse código é **protótipo, não watchdog ativo**.

Os testes são automatizados pelo workflow de CI da branch de hardening, mas a aprovação depende de verificar o resultado da execução mais recente **após** todas as alterações. Nenhuma mudança em `main`, `v2/state.enc` ou `v2/state.json`.
