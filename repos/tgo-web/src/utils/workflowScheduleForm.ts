import type { ScheduleCommand } from '../types/workflowSchedules';

export type TimerPreset = 'hourly' | 'daily' | 'weekdays' | 'weekly' | 'custom';
export function parseTimerForm(expression: string) {
  const fields = expression.trim().split(/\s+/);
  const number = (value: string | undefined, max: number) => Boolean(value && /^\d+$/.test(value) && Number(value) <= max);
  let mode: TimerPreset = 'custom';
  if (fields.length === 5 && number(fields[0], 59) && fields[2] === '*' && fields[3] === '*') {
    if (fields[1] === '*' && fields[4] === '*') mode = 'hourly';
    else if (number(fields[1], 23)) {
      if (fields[4] === '*') mode = 'daily';
      else if (fields[4] === '1-5') mode = 'weekdays';
      else if (number(fields[4], 6)) mode = 'weekly';
    }
  }
  return { mode, time: `${number(fields[1], 23) ? fields[1].padStart(2, '0') : '09'}:${number(fields[0], 59) ? fields[0].padStart(2, '0') : '00'}`,
    weekday: mode === 'weekly' ? fields[4] : '1' };
}
export function timerExpression(mode: TimerPreset, time: string, weekday: string): string | null {
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time) || !/^[0-6]$/.test(weekday)) return null;
  const [hour, minute] = time.split(':').map(Number);
  if (mode === 'custom') return null;
  return `${minute} ${mode === 'hourly' ? '*' : hour} * * ${mode === 'weekdays' ? '1-5' : mode === 'weekly' ? weekday : '*'}`;
}
export function readScheduleCommand(value: string | null): ScheduleCommand | null {
  if (!value || value.length > 2048) return null;
  try {
    const command: unknown = JSON.parse(value);
    if (!command || typeof command !== 'object' || Object.keys(command).length !== 2 || !('operation' in command) || !('request' in command)) return null;
    const request = command.request;
    if (!request || typeof request !== 'object' || !('request_id' in request) || !('expected_revision' in request)
      || typeof request.request_id !== 'string' || !/^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(request.request_id)
      || typeof request.expected_revision !== 'number' || !Number.isSafeInteger(request.expected_revision) || request.expected_revision < 0) return null;
    if (command.operation === 'disable' && request.expected_revision > 0 && Object.keys(request).length === 2) {
      return { operation: 'disable', request: { request_id: request.request_id, expected_revision: request.expected_revision } };
    }
    if (command.operation === 'save' && Object.keys(request).length === 5 && 'expected_release_id' in request
      && typeof request.expected_release_id === 'string' && request.expected_release_id.length <= 128 && request.expected_release_id.length > 0
      && 'expected_release_digest' in request && typeof request.expected_release_digest === 'string'
      && /^[a-f0-9]{64}$/.test(request.expected_release_digest) && 'enabled' in request && typeof request.enabled === 'boolean') {
      return { operation: 'save', request: { request_id: request.request_id, expected_revision: request.expected_revision,
        expected_release_id: request.expected_release_id, expected_release_digest: request.expected_release_digest, enabled: request.enabled } };
    }
  } catch { return null; }
  return null;
}
