export function reversePicksPressureScore(packet: unknown): number | null {
  if (!packet || typeof packet !== 'object') return null;
  const value = packet as Record<string, unknown>;
  const score100 = Number(value.score100);
  if (Number.isFinite(score100)) {
    return Math.max(0, Math.min(100, Math.round(score100)));
  }
  const score = Number(value.score);
  if (Number.isFinite(score)) {
    return Math.max(0, Math.min(100, Math.round(score * 100)));
  }
  return null;
}

export function reversePicksPressureLabel(packet: unknown): string {
  const score = reversePicksPressureScore(packet);
  if (score == null) {
    const value = packet && typeof packet === 'object'
      ? packet as Record<string, unknown>
      : {};
    return String(value.label || 'Classified').toUpperCase();
  }
  if (score <= 20) return 'VERY LOW';
  if (score <= 40) return 'LOW';
  if (score <= 60) return 'MODERATE';
  if (score <= 80) return 'HIGH';
  return 'ELITE';
}