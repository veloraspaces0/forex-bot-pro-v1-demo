/**
 * Pure SQLite reservation statement for a single logical PAPER workflow.
 * One INSERT..ON CONFLICT..WHERE..RETURNING statement is atomic in SQLite.
 * A reservation is written BEFORE network dispatch; ambiguous failures keep
 * the cooldown to prevent duplicate side effects (fail closed).
 */
export const DISPATCH_HOLD_MS=40*60*1000;
export const CREATE_DISPATCH_TABLE=
  "CREATE TABLE IF NOT EXISTS backup_dispatch_guard ("+
  "slot INTEGER PRIMARY KEY CHECK(slot=1), reserved_at INTEGER NOT NULL)";

export const ATOMIC_RESERVATION_SQL=
  "INSERT INTO backup_dispatch_guard (slot, reserved_at) VALUES (1, ?) "+
  "ON CONFLICT(slot) DO UPDATE SET reserved_at = excluded.reserved_at "+
  "WHERE backup_dispatch_guard.reserved_at <= ? RETURNING reserved_at";

export function reserveSql(sql, now=Date.now()) {
  if(!Number.isSafeInteger(now) || now<=0) throw new Error("RESERVATION_CLOCK_INVALID");
  const result=sql.exec(ATOMIC_RESERVATION_SQL,now,now-DISPATCH_HOLD_MS).toArray();
  if(result.length>1) throw new Error("RESERVATION_SQL_INVALID");
  return result.length===1;
}
