import {DurableObject} from "cloudflare:workers";
import {CREATE_DISPATCH_TABLE,reserveSql} from "./forex_backup_guard_sql.mjs";

/**
 * One Durable Object represents one logical PAPER workflow on main.
 * The SQLite conditional upsert reserves dispatch atomically across requests.
 *
 * This object never calls GitHub. The caller first confirms the stale
 * heartbeat and absence of recent/active Actions runs, then reserves a slot.
 * Reservation is retained after 403/timeout/unknown dispatch response to
 * avoid a second possibly-accepted side effect.
 */
export class BackupDispatchGuard extends DurableObject {
  constructor(ctx,env) {
    super(ctx,env);
    ctx.blockConcurrencyWhile(async()=>{
      ctx.storage.sql.exec(CREATE_DISPATCH_TABLE);
    });
  }

  async reserveDispatch() {
    // Use the coordinator's own clock, never a caller-provided timestamp.
    return reserveSql(this.ctx.storage.sql,Date.now());
  }
}
