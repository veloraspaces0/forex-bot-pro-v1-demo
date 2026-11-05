"""Public, non-sensitive heartbeat for successful PAPER_ONLY executions."""
import json
from datetime import datetime, timezone
from pathlib import Path


def build_heartbeat(state, run_id, run_attempt, now=None):
    if state.get('mode') != 'PAPER_ONLY' or state.get('pair') != 'EUR/USD':
        raise ValueError('Unexpected public state identity')
    checked = state.get('last_checked_at')
    if not isinstance(checked, str) or not checked:
        raise ValueError('Missing last_checked_at')
    if not str(run_id).isdigit() or not str(run_attempt).isdigit() or int(run_attempt) < 1:
        raise ValueError('Invalid workflow run identity')
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError('Heartbeat time must be timezone aware')
    return {
        'schema_version': 1,
        'mode': 'PAPER_ONLY',
        'run_id': str(run_id),
        'run_attempt': int(run_attempt),
        'completed_at': instant.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'state_last_checked_at': checked,
        'status': 'SUCCESS',
    }


def publish(state_path, output_path, run_id, run_attempt, atomic_write):
    state = json.loads(Path(state_path).read_text(encoding='utf-8'))
    heartbeat = build_heartbeat(state, run_id, run_attempt)
    atomic_write(Path(output_path), (json.dumps(heartbeat, indent=2) + '\n').encode('utf-8'))
    return heartbeat
