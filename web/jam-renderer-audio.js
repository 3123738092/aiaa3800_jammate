const HANDOFF_S = .008;

export function scheduleHandoff(baseline, preview, start, end, now) {
  const fade = Math.min(HANDOFF_S, (end - start) / 2);
  baseline.setValueAtTime(1, Math.max(now, start - fade));
  baseline.linearRampToValueAtTime(0, start);
  baseline.setValueAtTime(0, end);
  baseline.linearRampToValueAtTime(1, end + fade);
  preview.setValueAtTime(0, start);
  preview.linearRampToValueAtTime(1, start + fade);
  preview.setValueAtTime(1, end - fade);
  preview.linearRampToValueAtTime(0, end);
}

export function cancelHandoff(baseline, preview, start, end, now) {
  const fade = Math.min(HANDOFF_S, (end - start) / 2);
  const gain = now < start ? Math.min(1, (start - now) / fade)
    : now < end ? 0 : Math.min(1, (now - end) / fade);
  preview.cancelScheduledValues(now);
  preview.setValueAtTime(0, now);
  baseline.cancelScheduledValues(now);
  baseline.setValueAtTime(gain, now);
  baseline.linearRampToValueAtTime(1, now + HANDOFF_S);
}
