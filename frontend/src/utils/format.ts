/** Small formatting helpers shared by the Studio screens. */

export function formatDuration(seconds?: number | null): string {
  if (!seconds || seconds <= 0) return '0:00'
  const total = Math.round(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
  }
  return `${minutes}:${String(secs).padStart(2, '0')}`
}

export function formatBytes(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`
}

export function formatRelative(iso?: string | null): string {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const diff = Date.now() - then
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}d ago`
  return new Date(iso).toLocaleDateString()
}

export function scoreLabel(score: number): { text: string; color: string } {
  if (score >= 0.7) return { text: 'Strong', color: '#52c41a' }
  if (score >= 0.6) return { text: 'Good', color: '#4facfe' }
  if (score >= 0.5) return { text: 'Fair', color: '#faad14' }
  return { text: 'Weak', color: '#8c8c8c' }
}

export const ASPECT_PRESETS = [
  { key: 'vertical', label: 'Vertical 9:16', detail: 'Shorts · Reels · TikTok', width: 1080, height: 1920 },
  { key: 'horizontal', label: 'Horizontal 16:9', detail: 'YouTube · Landing pages', width: 1920, height: 1080 },
  { key: 'square', label: 'Square 1:1', detail: 'Feed posts', width: 1080, height: 1080 },
  { key: 'raw', label: 'Original', detail: 'No reframing', width: 0, height: 0 },
] as const
