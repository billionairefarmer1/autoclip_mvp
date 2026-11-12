import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Col,
  Divider,
  InputNumber,
  Layout,
  Row,
  Select,
  Slider,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ArrowLeftOutlined,
  CheckCircleFilled,
  CloseCircleFilled,
  ExperimentOutlined,
  SaveOutlined,
  UndoOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import studioApi, { HealthReport, StudioSettings } from '../services/studioApi'

const { Content } = Layout
const { Title, Text } = Typography

const Panel: React.FC<{ title: string; extra?: React.ReactNode; children: React.ReactNode }> = ({
  title,
  extra,
  children,
}) => (
  <div
    style={{
      background: 'rgba(26,26,46,0.55)',
      border: '1px solid rgba(255,255,255,0.07)',
      borderRadius: 18,
      padding: 22,
    }}
  >
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
      <Title level={5} style={{ color: '#fff', margin: 0 }}>
        {title}
      </Title>
      {extra}
    </div>
    {children}
  </div>
)

const Field: React.FC<{ label: string; hint?: string; children: React.ReactNode }> = ({
  label,
  hint,
  children,
}) => (
  <div style={{ marginBottom: 16 }}>
    <div style={{ color: '#e6e6e6', fontSize: 13, marginBottom: 4 }}>{label}</div>
    {children}
    {hint && <div style={{ color: '#737373', fontSize: 11.5, marginTop: 4, lineHeight: 1.5 }}>{hint}</div>}
  </div>
)

const Toggle: React.FC<{
  label: string
  hint?: string
  checked: boolean
  onChange: (value: boolean) => void
}> = ({ label, hint, checked, onChange }) => (
  <div
    style={{
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      gap: 12,
      padding: '9px 0',
      borderBottom: '1px solid rgba(255,255,255,0.05)',
    }}
  >
    <div>
      <div style={{ color: '#e6e6e6', fontSize: 13 }}>{label}</div>
      {hint && <div style={{ color: '#737373', fontSize: 11.5 }}>{hint}</div>}
    </div>
    <Switch size="small" checked={checked} onChange={onChange} />
  </div>
)

const StudioSettingsPage: React.FC = () => {
  const navigate = useNavigate()
  const [settings, setSettings] = useState<StudioSettings | null>(null)
  const [health, setHealth] = useState<HealthReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    Promise.all([studioApi.getSettings(), studioApi.health().catch(() => null)])
      .then(([settingsResponse, healthResponse]) => {
        setSettings(settingsResponse.settings)
        setHealth(healthResponse)
      })
      .catch((error) => message.error(error?.userMessage || 'Could not load settings'))
      .finally(() => setLoading(false))
  }, [])

  const patch = (values: Partial<StudioSettings>) =>
    setSettings((current) => (current ? { ...current, ...values } : current))

  const save = async () => {
    if (!settings) return
    setSaving(true)
    try {
      const response = await studioApi.updateSettings(settings)
      setSettings(response.settings)
      message.success('Settings saved')
    } catch (error: any) {
      message.error(error?.userMessage || 'Could not save settings')
    } finally {
      setSaving(false)
    }
  }

  const reset = async () => {
    try {
      const response = await studioApi.resetSettings()
      setSettings(response.settings)
      message.success('Settings restored to defaults')
    } catch (error: any) {
      message.error(error?.userMessage || 'Could not reset settings')
    }
  }

  if (loading || !settings) {
    return (
      <Layout style={{ minHeight: '100vh', background: '#0f0f0f' }}>
        <Content style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '60vh' }}>
          <Spin size="large" />
        </Content>
      </Layout>
    )
  }

  const asr = health?.transcription || {}
  const asrEntries = Object.entries(asr)

  return (
    <Layout style={{ minHeight: '100vh', background: '#0f0f0f' }}>
      <Content style={{ padding: '24px 24px 80px' }}>
        <div style={{ maxWidth: 1180, margin: '0 auto' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 22, flexWrap: 'wrap' }}>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/studio')} />
            <Title level={3} style={{ color: '#fff', margin: 0, flex: 1 }}>
              Studio settings
            </Title>
            <Space>
              <Button icon={<UndoOutlined />} onClick={reset}>
                Reset
              </Button>
              <Button
                type="primary"
                icon={<SaveOutlined />}
                loading={saving}
                onClick={save}
                style={{ background: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)', border: 'none' }}
              >
                Save changes
              </Button>
            </Space>
          </div>

          {/* System status */}
          <div style={{ marginBottom: 20 }}>
            <Alert
              type={health?.ok ? 'success' : 'error'}
              showIcon
              style={{ borderRadius: 12 }}
              message={
                health?.ok ? (
                  <span>
                    System ready · FFmpeg detected
                    {health.ffmpeg?.version ? ` (${health.ffmpeg.version.split('Copyright')[0].trim()})` : ''}
                  </span>
                ) : (
                  'FFmpeg not found'
                )
              }
              description={
                health?.ok ? (
                  <Space direction="vertical" size={6} style={{ fontSize: 12.5 }}>
                    <span>
                      Encoders:{' '}
                      {health.encoders?.length
                        ? health.encoders.map((encoder) => (
                            <Tag key={encoder.name} color={encoder.hardware ? 'green' : 'default'} style={{ marginInlineEnd: 4 }}>
                              {encoder.label}
                            </Tag>
                          ))
                        : 'software only'}
                    </span>
                    <span>
                      Caption font:{' '}
                      <Tag>{health.fonts?.family || 'not found'}</Tag>
                      <Text style={{ color: '#8c8c8c' }}>{health.fonts?.fontsdir}</Text>
                    </span>
                    <span style={{ color: '#8c8c8c' }}>Working folder: {health.studio_root}</span>
                  </Space>
                ) : (
                  <span style={{ fontSize: 13 }}>{health?.ffmpeg?.error || health?.error}</span>
                )
              }
            />
          </div>

          <Row gutter={[20, 20]}>
            <Col xs={24} lg={12}>
              <Space direction="vertical" size={20} style={{ width: '100%' }}>
                {/* Transcription */}
                <Panel title="Speech to text">
                  <div style={{ marginBottom: 12 }}>
                    {asrEntries.map(([key, info]) => (
                      <div
                        key={key}
                        style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '5px 0' }}
                      >
                        <span style={{ color: '#bfbfbf', fontSize: 12.5 }}>{info.label}</span>
                        {info.available ? (
                          <CheckCircleFilled style={{ color: '#52c41a' }} />
                        ) : (
                          <CloseCircleFilled style={{ color: '#595959' }} />
                        )}
                      </div>
                    ))}
                  </div>

                  <Field
                    label="Transcription engine"
                    hint="Auto uses the fastest installed Whisper backend. Audio-only still works without one."
                  >
                    <Select
                      value={settings.transcription_backend}
                      onChange={(value) => patch({ transcription_backend: value })}
                      style={{ width: '100%' }}
                      options={[
                        { value: 'auto', label: 'Automatic (recommended)' },
                        { value: 'faster_whisper', label: 'faster-whisper (local)' },
                        { value: 'openai_whisper', label: 'openai-whisper (local)' },
                        { value: 'whisper_cpp', label: 'whisper.cpp binary' },
                        { value: 'openai_api', label: 'Cloud API (optional)' },
                      ]}
                    />
                  </Field>

                  <Row gutter={12}>
                    <Col span={12}>
                      <Field label="Model size" hint="Bigger is more accurate but slower.">
                        <Select
                          value={settings.whisper_model}
                          onChange={(value) => patch({ whisper_model: value })}
                          style={{ width: '100%' }}
                          options={[
                            { value: 'tiny', label: 'tiny · fastest' },
                            { value: 'base', label: 'base · balanced' },
                            { value: 'small', label: 'small · better' },
                            { value: 'medium', label: 'medium · slow' },
                            { value: 'large-v3', label: 'large-v3 · slowest' },
                          ]}
                        />
                      </Field>
                    </Col>
                    <Col span={12}>
                      <Field label="Language" hint="Empty = detect automatically.">
                        <Select
                          allowClear
                          placeholder="Auto"
                          value={settings.transcription_language || undefined}
                          onChange={(value) => patch({ transcription_language: value || '' })}
                          style={{ width: '100%' }}
                          options={[
                            { value: 'en', label: 'English' },
                            { value: 'es', label: 'Spanish' },
                            { value: 'fr', label: 'French' },
                            { value: 'de', label: 'German' },
                            { value: 'pt', label: 'Portuguese' },
                            { value: 'hi', label: 'Hindi' },
                            { value: 'yo', label: 'Yoruba' },
                            { value: 'ig', label: 'Igbo' },
                            { value: 'ha', label: 'Hausa' },
                            { value: 'zh', label: 'Chinese' },
                            { value: 'ja', label: 'Japanese' },
                            { value: 'ar', label: 'Arabic' },
                          ]}
                        />
                      </Field>
                    </Col>
                  </Row>

                  <Alert
                    type="info"
                    showIcon
                    style={{ borderRadius: 10, background: 'rgba(79,172,254,0.07)', border: 'none', fontSize: 12.5 }}
                    message="Local Whisper: pip install faster-whisper"
                    description="Models download once on first use and are cached. Nothing is sent off this machine."
                  />
                </Panel>

                {/* Clip selection */}
                <Panel title="What counts as a good clip">
                  <Field label={`Minimum length · ${settings.min_duration}s`}>
                    <Slider
                      min={5}
                      max={60}
                      value={settings.min_duration}
                      onChange={(value) => patch({ min_duration: value })}
                      tooltip={{ formatter: (value) => `${value}s` }}
                    />
                  </Field>
                  <Field label={`Target length · ${settings.ideal_duration}s`} hint="Clips are scored highest near this length.">
                    <Slider
                      min={10}
                      max={120}
                      value={settings.ideal_duration}
                      onChange={(value) => patch({ ideal_duration: value })}
                      tooltip={{ formatter: (value) => `${value}s` }}
                    />
                  </Field>
                  <Field label={`Maximum length · ${settings.max_duration}s`}>
                    <Slider
                      min={15}
                      max={180}
                      value={settings.max_duration}
                      onChange={(value) => patch({ max_duration: value })}
                      tooltip={{ formatter: (value) => `${value}s` }}
                    />
                  </Field>
                  <Row gutter={12}>
                    <Col span={12}>
                      <Field label="Max clips per video">
                        <InputNumber
                          min={1}
                          max={100}
                          value={settings.max_clips}
                          onChange={(value) => patch({ max_clips: value || 20 })}
                          style={{ width: '100%' }}
                        />
                      </Field>
                    </Col>
                    <Col span={12}>
                      <Field label={`Minimum score · ${Math.round(settings.min_score * 100)}`} hint="Hide weaker candidates.">
                        <Slider
                          min={0}
                          max={0.9}
                          step={0.05}
                          value={settings.min_score}
                          onChange={(value) => patch({ min_score: value })}
                        />
                      </Field>
                    </Col>
                  </Row>
                  <Toggle
                    label="Snap cuts to scene changes"
                    hint="Clips start and end on visual cuts where possible"
                    checked={Boolean(settings.snap_to_scenes)}
                    onChange={(value) => patch({ snap_to_scenes: value })}
                  />
                </Panel>
              </Space>
            </Col>

            <Col xs={24} lg={12}>
              <Space direction="vertical" size={20} style={{ width: '100%' }}>
                {/* Export */}
                <Panel title="Default export">
                  <Field label="Format">
                    <Select
                      value={settings.mode}
                      onChange={(value) => patch({ mode: value })}
                      style={{ width: '100%' }}
                      options={[
                        { value: 'vertical', label: 'Vertical 9:16 — Shorts, Reels, TikTok' },
                        { value: 'horizontal', label: 'Horizontal 16:9 — YouTube' },
                        { value: 'raw', label: 'Original — no reframing' },
                      ]}
                    />
                  </Field>
                  <Row gutter={12}>
                    <Col span={8}>
                      <Field label="Width">
                        <InputNumber
                          min={256}
                          max={3840}
                          step={2}
                          value={settings.width}
                          onChange={(value) => patch({ width: value || 1080 })}
                          style={{ width: '100%' }}
                        />
                      </Field>
                    </Col>
                    <Col span={8}>
                      <Field label="Height">
                        <InputNumber
                          min={256}
                          max={3840}
                          step={2}
                          value={settings.height}
                          onChange={(value) => patch({ height: value || 1920 })}
                          style={{ width: '100%' }}
                        />
                      </Field>
                    </Col>
                    <Col span={8}>
                      <Field label="FPS">
                        <Select
                          value={settings.fps}
                          onChange={(value) => patch({ fps: value })}
                          options={[24, 25, 30, 50, 60].map((fps) => ({ value: fps, label: `${fps}` }))}
                          style={{ width: '100%' }}
                        />
                      </Field>
                    </Col>
                  </Row>
                  <Toggle
                    label="Follow the speaker (face tracking)"
                    hint="Keeps the face centred in vertical crops"
                    checked={Boolean(settings.auto_reframe)}
                    onChange={(value) => patch({ auto_reframe: value })}
                  />
                  <Toggle
                    label="Blurred background fallback"
                    hint="Used when the shot cannot fill a 9:16 frame"
                    checked={Boolean(settings.blurred_background)}
                    onChange={(value) => patch({ blurred_background: value })}
                  />
                  <Toggle
                    label="Normalise loudness"
                    hint={`Target ${-14} LUFS, the common social-video level`}
                    checked={Boolean(settings.loudnorm)}
                    onChange={(value) => patch({ loudnorm: value })}
                  />
                  <Toggle
                    label="Use hardware encoder when available"
                    hint="Much faster exports on NVIDIA / Apple / Intel / AMD GPUs"
                    checked={Boolean(settings.prefer_hardware)}
                    onChange={(value) => patch({ prefer_hardware: value })}
                  />
                  <div style={{ marginTop: 14 }}>
                    <Field label={`Quality (CRF ${settings.crf})`} hint="Lower is higher quality and bigger files.">
                      <Slider
                        min={16}
                        max={30}
                        value={settings.crf}
                        onChange={(value) => patch({ crf: value })}
                      />
                    </Field>
                  </div>
                </Panel>

                {/* Captions */}
                <Panel title="Captions">
                  <Toggle
                    label="Burn captions into the video"
                    checked={Boolean(settings.captions)}
                    onChange={(value) => patch({ captions: value })}
                  />
                  <Toggle
                    label="Word-by-word highlight"
                    hint="Karaoke style, needs word timings from Whisper"
                    checked={Boolean(settings.caption_karaoke)}
                    onChange={(value) => patch({ caption_karaoke: value })}
                  />
                  <Toggle
                    label="UPPERCASE captions"
                    checked={Boolean(settings.caption_uppercase)}
                    onChange={(value) => patch({ caption_uppercase: value })}
                  />
                  <Row gutter={12} style={{ marginTop: 12 }}>
                    <Col span={12}>
                      <Field label="Highlight colour">
                        <Select
                          value={settings.caption_highlight_colour}
                          onChange={(value) => patch({ caption_highlight_colour: value })}
                          style={{ width: '100%' }}
                          options={[
                            { value: 'yellow', label: 'Yellow' },
                            { value: 'green', label: 'Green' },
                            { value: 'cyan', label: 'Cyan' },
                            { value: 'orange', label: 'Orange' },
                            { value: 'pink', label: 'Pink' },
                            { value: 'white', label: 'White' },
                          ]}
                        />
                      </Field>
                    </Col>
                    <Col span={12}>
                      <Field label={`Font size · ${settings.caption_font_size}`}>
                        <Slider
                          min={40}
                          max={110}
                          value={settings.caption_font_size}
                          onChange={(value) => patch({ caption_font_size: value })}
                        />
                      </Field>
                    </Col>
                  </Row>
                  <Field
                    label="Caption font family"
                    hint={`Leave empty to use the best installed font. Detected: ${health?.fonts?.family || 'unknown'}`}
                  >
                    <Select
                      allowClear
                      placeholder="Automatic"
                      value={settings.caption_font || undefined}
                      onChange={(value) => patch({ caption_font: value || '' })}
                      style={{ width: '100%' }}
                      options={[
                        { value: 'Arial', label: 'Arial' },
                        { value: 'Impact', label: 'Impact' },
                        { value: 'Montserrat', label: 'Montserrat' },
                        { value: 'Roboto', label: 'Roboto' },
                        { value: 'Inter', label: 'Inter' },
                        { value: 'DejaVu Sans', label: 'DejaVu Sans' },
                      ]}
                    />
                  </Field>
                </Panel>

                {/* Tooling */}
                <Panel
                  title="Tools"
                  extra={<Tag icon={<ThunderboltOutlined />} color="blue">advanced</Tag>}
                >
                  <Field label="FFmpeg path" hint="Empty = auto-detect (bundled, PATH, or common install locations).">
                    <Select
                      allowClear
                      placeholder="Automatic"
                      value={settings.ffmpeg_path || undefined}
                      onChange={(value) => patch({ ffmpeg_path: value || '' })}
                      style={{ width: '100%' }}
                      options={
                        health?.ffmpeg?.path
                          ? [{ value: health.ffmpeg.path, label: health.ffmpeg.path }]
                          : []
                      }
                    />
                  </Field>
                  <Divider style={{ margin: '8px 0 14px', borderColor: 'rgba(255,255,255,0.06)' }} />
                  <Alert
                    type="info"
                    showIcon
                    icon={<ExperimentOutlined />}
                    style={{ borderRadius: 10, background: 'rgba(255,255,255,0.03)', border: 'none', fontSize: 12.5 }}
                    message="Need test footage?"
                    description={
                      <span>
                        Run <Text code>python scripts/make_demo_video.py</Text> in the AutoClip folder to
                        generate a 90-second sample video (with subtitles) and try the whole flow.
                      </span>
                    }
                  />
                </Panel>
              </Space>
            </Col>
          </Row>
        </div>
      </Content>
    </Layout>
  )
}

export default StudioSettingsPage
