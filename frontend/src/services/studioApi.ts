/**
 * Typed API client for the AutoClip Studio backend.
 *
 * All calls are relative ("/api/studio/...") on purpose: the FastAPI server
 * serves both the API and the built UI, so the app works offline on
 * http://localhost:8000 with no CORS or hard-coded host names.
 */
import axios from 'axios'

export type JobStatus =
  | 'uploaded'
  | 'analyzing'
  | 'analyzed'
  | 'rendering'
  | 'completed'
  | 'error'
  | 'interrupted'
  | 'uploading'

export interface ClipBreakdown {
  hook: number
  completeness: number
  pacing: number
  energy: number
  emphasis: number
  length: number
}

export interface StudioClip {
  id: string
  start: number
  end: number
  duration: number
  score: number
  breakdown: ClipBreakdown
  reasons: string[]
  title: string
  hook: string
  hook_text: string
  text: string
  words: number
  status: 'candidate' | 'pending' | 'done' | 'error'
  file?: string
  filename?: string
  size?: number
  width?: number
  height?: number
  encoder?: string
  hardware?: boolean
  captions?: boolean
  crop_mode?: string
  thumbnail?: string
  error?: string
}

export interface StudioJob {
  id: string
  name: string
  source: string
  status: JobStatus
  message: string
  progress: number
  created_at: string
  updated_at: string
  duration: number
  width: number
  height: number
  size: number
  transcript_source: string
  transcript_language: string
  transcript_segments: number
  scene_count: number
  warnings: string[]
  clips: StudioClip[]
  error?: string | null
  options: Record<string, unknown>
  is_running?: boolean
}

export interface StudioSettings {
  transcription_backend: string
  whisper_model: string
  transcription_language: string
  min_duration: number
  max_duration: number
  ideal_duration: number
  max_clips: number
  min_score: number
  mode: 'vertical' | 'horizontal' | 'raw'
  width: number
  height: number
  fps: number
  captions: boolean
  caption_font: string
  caption_font_size: number
  caption_colour: string
  caption_highlight_colour: string
  caption_uppercase: boolean
  caption_karaoke: boolean
  caption_margin_v: number
  auto_reframe: boolean
  blurred_background: boolean
  loudnorm: boolean
  prefer_hardware: boolean
  crf: number
  encoder: string
  ffmpeg_path: string
  [key: string]: unknown
}

export interface BackendStatus {
  available: boolean
  label: string
  install: string
}

export interface HealthReport {
  ok: boolean
  ffmpeg: { available: boolean; path?: string; version?: string; error?: string }
  transcription?: Record<string, BackendStatus>
  speech_transcription_ready?: boolean
  encoders: { name: string; label: string; hardware: boolean }[]
  fonts: { family: string; fontsdir?: string | null; file?: string | null }
  studio_root?: string
  error?: string
}

export interface StudioStats {
  jobs: number
  completed_jobs: number
  rendered_clips: number
  source_seconds: number
  clips_seconds: number
}

const client = axios.create({ baseURL: '/api/studio', timeout: 600000 })

client.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const detail =
      error?.response?.data?.detail ||
      error?.message ||
      'Something went wrong talking to the AutoClip server'
    error.userMessage = typeof detail === 'string' ? detail : JSON.stringify(detail)
    return Promise.reject(error)
  },
)

export const studioApi = {
  health: (): Promise<HealthReport> => client.get('/health'),

  getSettings: (): Promise<{ settings: StudioSettings; defaults: StudioSettings }> =>
    client.get('/settings'),
  updateSettings: (values: Partial<StudioSettings>): Promise<{ settings: StudioSettings }> =>
    client.put('/settings', { values }),
  resetSettings: (): Promise<{ settings: StudioSettings }> => client.post('/settings/reset'),

  listJobs: (): Promise<{ jobs: StudioJob[] }> => client.get('/jobs'),
  getJob: (id: string): Promise<{ job: StudioJob }> => client.get(`/jobs/${id}`),
  renameJob: (id: string, name: string): Promise<{ job: StudioJob }> =>
    client.patch(`/jobs/${id}`, { name }),
  deleteJob: (id: string): Promise<{ ok: boolean }> => client.delete(`/jobs/${id}`),

  uploadJob: (
    file: File,
    name: string,
    subtitle?: File | null,
    onProgress?: (percent: number) => void,
  ): Promise<{ job: StudioJob }> => {
    const form = new FormData()
    form.append('video', file)
    form.append('name', name || file.name.replace(/\.[^.]+$/, ''))
    if (subtitle) form.append('subtitle', subtitle)
    return client.post('/jobs', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 0,
      onUploadProgress: (event) => {
        if (onProgress && event.total) {
          onProgress(Math.round((event.loaded / event.total) * 100))
        }
      },
    })
  },

  uploadSubtitle: (id: string, file: File): Promise<{ ok: boolean; path: string }> => {
    const form = new FormData()
    form.append('subtitle', file)
    return client.post(`/jobs/${id}/subtitle`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  analyze: (id: string, options: Record<string, unknown> = {}): Promise<{ ok: boolean }> =>
    client.post(`/jobs/${id}/analyze`, { options }),
  render: (
    id: string,
    clipIds: string[] | null,
    options: Record<string, unknown> = {},
  ): Promise<{ ok: boolean }> => client.post(`/jobs/${id}/render`, { clip_ids: clipIds, options }),

  deleteClipFile: (id: string, clipId: string): Promise<{ ok: boolean; job: StudioJob }> =>
    client.delete(`/jobs/${id}/clips/${clipId}/file`),

  stats: (): Promise<StudioStats> => client.get('/stats'),

  // Media URLs (used directly in <video>/<img> tags)
  sourceUrl: (id: string) => `/api/studio/jobs/${id}/source`,
  clipUrl: (id: string, clipId: string) => `/api/studio/jobs/${id}/clips/${clipId}/video`,
  thumbUrl: (id: string, clipId: string) => `/api/studio/jobs/${id}/clips/${clipId}/thumbnail`,
  downloadClipUrl: (id: string, clipId: string) =>
    `/api/studio/jobs/${id}/clips/${clipId}/download`,
  downloadAllUrl: (id: string) => `/api/studio/jobs/${id}/download-all`,
  clipSrtUrl: (id: string, clipId: string) => `/api/studio/jobs/${id}/clips/${clipId}/srt`,
  transcriptUrl: (id: string, format: 'json' | 'srt' | 'txt' = 'json') =>
    `/api/studio/jobs/${id}/transcript?format=${format}`,
}

export default studioApi
