# Forex Watch V2.3 — instalação externa (Windows)

**ESTADO:** branch `revisao-final-forex-watch-v23-20261104`. Preparo e testes automatizados. **Não é um deploy ativo** enquanto o operador não executar o instalador na própria conta. A V2.1 `main` e os arquivos `state.enc`/`state.json` permanecem intocados.

O monitor é **PAPER-ONLY e READ-ONLY**: nunca coloca ordens, nunca lê a chave Twelve Data nem o estado criptografado. O Cron Cloudflare é independente do GitHub Actions, mas as evidências consultadas ainda vêm do GitHub.

## Pré-requisitos do operador

- Conta Cloudflare Workers autorizada, Node.js com `node` e `npx` instalados.
- Conta Telegram com bot e Chat ID criados pelo próprio operador.
- Conta Healthchecks.io com um check chamado, por exemplo, `Forex Watch V2.3 alive`, **período de 15 min, graça de 20 min**, com alerta enviado para um canal escolhido na própria Healthchecks.io. Guarde a URL ping `https://hc-ping.com/<UUID>` como **segredo**.
- Nunca inserir tokens/chaves em ChatGPT, repositórios, commits ou imagens. O instalador usa **prompts locais** da ferramenta Wrangler.

## Ordem segura recomendada

**Passo inicial — somente verificação local, sem login, sem deploy:**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\\experimentos\\setup_watch_v23.ps1 -PreflightOnly
```

O instalador exige Node.js 20+ e valida a compilação com `wrangler@4 deploy --dry-run`.
É preciso ter internet para instalar/usar o Wrangler, mas **nenhuma conta externa é modificada no preflight**.

**Passo seguinte — OBSERVE:** execute sem argumentos para autenticar, criar o binding KV se necessário e publicar o Worker com `ALERT_MODE = "observe"`. Neste modo **não há alertas Telegram nem pings Healthchecks de sucesso**, deliberadamente. O monitor será apenas diagnóstico nos logs.

**Só após configurar Telegram e Healthchecks:** execute `-Activate`. A ativação faz primeiro o deploy em `observe` e mantém `observe` durante **todos** os `wrangler secret put`. Essa ordem é importante porque o comando `wrangler secret put` já publica uma nova versão do Worker. O modo `active` só é publicado depois de obter os três segredos e executar mais um `dry-run`.

**Critério de aprovação final:** confirme que pelo menos duas invocações reais do Cron ocorreram e que uma mensagem real foi recebida no Telegram e um ping/sinal de falha real foi recebido pelo Healthchecks. O CI não cobre essas integrações na conta do operador.

**Não há garantia de intervalo exato de execução:** Cloudflare Cron é best-effort; `scheduledTime` representa o horário nominal, mas o código usa o relógio real `Date.now()` para calcular a idade dos dados. Mesmo com Cron de 15 min, a detecção de anomalias não substitui gestão automática de risco nem stop-loss real.

## Instalar em OBSERVE (sem notificações Telegram)

1. Abra a branch [revisao-final-forex-watch-v23-20261104](https://github.com/veloraspaces0/forex-bot-pro-v1-demo/tree/revisao-final-forex-watch-v23-20261104), clique **Code > Download ZIP** e descompacte.
2. No PowerShell, dentro da pasta extraída, execute:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\experimentos\setup_watch_v23.ps1
   ```

3. O script roda os testes JavaScript offline, pede autenticação Cloudflare no navegador, cria/binda uma namespace `INCIDENT_KV` apenas se não houver binding, e publica o Worker em `ALERT_MODE="observe"`.
4. O Cron configurado em `experimentos/wrangler_v23.toml` é `*/15 * * * *` (UTC), e o Worker **não** expõe endpoint HTTP público; `workers_dev=false`. Cloudflare Cron pode levar até 15 min para propagar alterações.
5. No modo observe, verifique os logs Cloudflare. Não há Telegram nem sinal de sucesso Healthchecks nesse modo, mesmo que segredos estejam configurados.

## Ativar notificações depois de criar as contas

Tenha em mãos, **somente no seu computador**, os três segredos:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `MONITOR_HEARTBEAT_URL` (URL `https://hc-ping.com/UUID` completa, gerada pela conta Healthchecks)

Depois execute, na mesma pasta:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\experimentos\setup_watch_v23.ps1 -Activate
```

O script pede confirmação digitando `ATIVAR`, solicita cada segredo separadamente por `npx wrangler secret put` (sem compartilhar no chat), altera a cópia **local** do arquivo TOML para `ALERT_MODE="active"` e publica o Worker.

**Sem KV, Telegram e Healthchecks, o modo active falha explicitamente; não finge que notificou.** O instalador permanece em OBSERVE até receber todos os segredos. O Worker envia `/fail` ao Healthchecks em ciclos com dados públicos inacessíveis ou com falha de entrega Telegram; depois de um ciclo bem-sucedido, envia ping de sucesso. Mesmo quando o bot está em `CRITICO`, o ping pode ser sucesso se o monitor funcionou e conseguiu alertar: é um sinal de saúde do *monitor*, não do *robô*.

O Cloudflare KV mantém o histórico de alertas de forma eventual (*best effort*); alguns alertas duplicados são possíveis, especialmente em concorrência ou indisponibilidade do KV.

## Verificação operacional que precisa ser feita após o deploy

1. Confira que o Cron existe em **Cloudflare > Workers & Pages > Worker > Settings > Triggers > Cron Triggers**.
2. Consulte logs de invocação e confirme no Healthchecks.io que há pings no intervalo esperado e o canal de notificação externo está habilitado.
3. Faça um teste controlado de envio Telegram em um ambiente de teste ou com um incidente simulado autorizado; confirme o **recebimento** real, não apenas HTTP 200.
4. Teste um sinal `/fail` do Healthchecks e confirme que o serviço avisa no canal configurado. Execute esse teste somente na sua própria conta de monitoramento.
5. Compare alertas com dados públicos em `main`, como estado e execuções GitHub. Não altere nem simule trades no `main` para testar o monitor.
6. Confirme limites de CPU gratuitos da Cloudflare e uso de KV antes de declarar operação confiável. Os testes em GitHub/Node não substituem testes no runtime da Cloudflare.

## Desativar sem afetar o Forex V2.1

Acesse a conta Cloudflare e desative o Cron Trigger ou altere `[triggers] crons=[]` em cópia local e faça deploy. Para desativar alertas Telegram mantendo diagnóstico, use `ALERT_MODE="observe"` e publique. Não é necessário alterar a branch `main` ou o estado do Forex.

## Observações de segurança

- A URL Healthchecks identifica um check e pode ser usada para injetar sinais falsos: preserve seu sigilo.
- Segredos são mantidos pela Cloudflare e não devem estar no `wrangler_v23.toml`.
- O Worker valida o host da URL de heartbeat como `hc-ping.com`, para evitar envio de tokens a endpoints arbitrários.
- Sem acesso à conta Cloudflare/Telegram/Healthchecks, não é possível afirmar que o Worker esteja implantado ou que notificações estejam chegando.

Documentação oficial:
- [Cloudflare Cron](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
- [Cloudflare KV](https://developers.cloudflare.com/kv/get-started/)
- [Cloudflare Wrangler Secrets](https://developers.cloudflare.com/workers/configuration/secrets/)
- [Healthchecks.io: Ping e /fail](https://healthchecks.io/docs/signaling_failures/)
