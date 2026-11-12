import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert,
  Button,
  Checkbox,
  Col,
  Divider,
  Empty,
  Input,
  Layout,
  Modal,
  Progress,
  Radio,
  Row,
  Space,
  Spin,
  Switch,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  ArrowLeftOutlined,
  CheckOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  FileTextOutlined,
  PlayCircleFilled,
  ReloadOutlined,
  ScissorOutlined,
  StarFilled,
  VideoCameraOutlined,
} from '@ant-design/icons'
import studioApi, { StudioClip, StudioJob } from '../services/studioApi'
import StatusPill from '../components/studio/StatusPill'
import {
  ASPECT_PRESETS,
  formatBytes,
  formatDuration,
  scoreLabel,
} from '../utils/format'

const { Content } = Layout
const { Title, Text, Paragraph } = Typography

const SCORE_LABELS: Record<string, string> = {
  hook: 'Opening hook',
  completeness: 'Complete story',
  pacing: 'Pacing',
  energy: 'Energy',
  emphasis: 'Emphasis',
  length: 'Clip length',
}

const StudioJobPage: React.FC = () => {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [job, setJob] = useState<StudioJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<string[]>([])
  const [preview, setPreview] = useState<StudioClip | null>(null)
  const [showSource, setShowSource] = useState(false)
  const [exportSettings, setExportSettings] = useState({
    mode: 'vertical' as 'vertical' | 'horizontal' | 'raw' | 'square',
    captions: true,
    auto_reframe: true,
    blurred_background: true,
    width: 1080,
    height: 1920,
  })
  const [renaming, setRenaming] = useState(false)
  const [newName, setNewName] = useState('')
  const pollRef = useRef<number | null>(null)

  const load = useCallback(async () => {
    try {
      const response = await studioApi.getJob(id)
      setJob(response.job)
      return response.job
    } catch (error: any) {
      message.error(error?.userMessage || 'Could not load this project')
      return null
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    load()
    studioApi
      .getSettings()
      .then((response) => {
        setExportSettings((current) => ({
          ...current,
          mode: (response.settings.mode as any) || 'vertical',
          captions: response.settings.captions,
          auto_reframe: response.settings.auto_reframe,
          blurred_background: response.settings.blurred_background,
          width: response.settings.width,
          height: response.settings.height,
        }))
      })
      .catch(() => undefined)
  }, [load])

  const busy = job?.status === 'analyzing' || job?.status === 'rendering'

  useEffect(() => {
    if (busy && pollRef.current === null) {
      pollRef.current = window.setInterval(load, 1200)
    }
    if (!busy && pollRef.current !== null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
    return () => {
      if (pollRef.current !== null && !busy) {
        window.clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [busy, load])

  useEffect(() => () => {
    if (pollRef.current !== null) window.clearInterval(pollRef.current)
  }, [])

  const clips = job?.clips || []
  const renderedCount = clips.filter((clip) => clip.status === 'done').length
  const candidates = useMemo(
    () => [...clips].sort((a, b) => b.score - a.score),
    [clips],
  )

  const startAnalysis = async () => {
    try {
      await studioApi.analyze(id, {})
      message.info('Analysing video…')
      load()
    } catch (error: any) {
      message.error(error?.userMessage || 'Could not start analysis')
    }
  }

  const startRender = async (clipIds: string[] | null) => {
    const payload: Record<string, unknown> = {
      mode: exportSettings.mode === 'square' ? 'vertical' : exportSettings.mode,
      captions: exportSettings.captions,
      auto_reframe: exportSettings.auto_reframe,
      blurred_background: exportSettings.blurred_background,
    }
    if (exportSettings.mode === 'square') {
      payload.width = 1080
      payload.height = 1080
    } else if (exportSettings.mode === 'vertical') {
      payload.width = 1080
      payload.height = 1920
    } else if (exportSettings.mode === 'horizontal') {
      payload.width = 1920
      payload.height = 1080
    }
    try {
      await studioApi.render(id, clipIds, payload)
      message.success(clipIds ? `Exporting ${clipIds.length} clip(s)` : 'Exporting all clips')
      load()
    } catch (error: any) {
      message.error(error?.userMessage || 'Could not start export')
    }
  }

  const handleDeleteClip = (clip: StudioClip) => {
    Modal.confirm({
      title: 'Delete this rendered file?',
      content: 'The candidate clip stays, only the exported MP4 is removed from disk.',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          const response = await studioApi.deleteClipFile(id, clip.id)
          setJob(response.job)
          message.success('File deleted')
        } catch (error: any) {
          message.error(error?.userMessage || 'Delete failed')
        }
      },
    })
  }

  const rename = async () => {
    if (!newName.trim()) return
    try {
      const response = await studioApi.renameJob(id, newName.trim())
      setJob(response.job)
      setRenaming(false)
      message.success('Renamed')
    } catch (error: any) {
      message.error(error?.userMessage || 'Rename failed')
    }
  }

  if (loading) {
    return (
      <Layout style={{ minHeight: '100vh', background: '#0f0f0f' }}>
        <Content style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '70vh' }}>
          <Spin size="large" />
        </Content>
      </Layout>
    )
  }

  if (!job) {
    return (
      <Layout style={{ minHeight: '100vh', background: '#0f0f0f' }}>
        <Content style={{ padding: 40 }}>
          <Empty description={<span style={{ color: '#8c8c8c' }}>Project not found</span>} />
          <div style={{ textAlign: 'center' }}>
            <Button onClick={() => navigate('/studio')}>Back to studio</Button>
          </div>
        </Content>
      </Layout>
    )
  }

  const transcriptLabel =
    job.transcript_source === 'none'
      ? 'audio only'
      : job.transcript_source.replace('_', ' ')

  return (
    <Layout style={{ minHeight: '100vh', background: '#0f0f0f' }}>
      <Content style={{ padding: '24px 24px 80px' }}>
        <div style={{ maxWidth: 1600, margin: '0 auto' }}>
          {/* Header */}
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, marginBottom: 20, flexWrap: 'wrap' }}>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/studio')} />
            <div style={{ flex: 1, minWidth: 260 }}>
              <Space align="center" size={10} wrap>
                <Title level={3} style={{ color: '#fff', margin: 0 }}>
                  {job.name}
                </Title>
                <Button
                  type="text"
                  size="small"
                  icon={<EditOutlined />}
                  onClick={() => {
                    setNewName(job.name)
                    setRenaming(true)
                  }}
                />
                <StatusPill status={job.status} />
              </Space>
              <Space size={14} wrap style={{ marginTop: 6, color: '#8c8c8c', fontSize: 13 }}>
                <span>
                  <ClockCircleOutlined /> {formatDuration(job.duration)}
                </span>
                <span>
                  <VideoCameraOutlined /> {job.width}×{job.height}
                </span>
                <span>{formatBytes(job.size)}</span>
                <span>
                  <FileTextOutlined /> {transcriptLabel}
                  {job.transcript_segments > 0 ? ` · ${job.transcript_segments} lines` : ''}
                </span>
                {job.scene_count > 0 && <span>🎬 {job.scene_count} scene cuts</span>}
              </Space>
            </div>
            <Space wrap>
              <Button icon={<PlayCircleFilled />} onClick={() => setShowSource(true)}>
                Source
              </Button>
              <Button icon={<ReloadOutlined />} onClick={startAnalysis} disabled={busy}>
                Re-analyse
              </Button>
              <Button
                type="primary"
                icon={<DownloadOutlined />}
                disabled={!renderedCount}
                onClick={() => window.open(studioApi.downloadAllUrl(id), '_blank')}
                style={{ background: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)', border: 'none' }}
              >
                Download all ({renderedCount})
              </Button>
            </Space>
          </div>

          {renaming && (
            <Space style={{ marginBottom: 16 }}>
              <Input
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                onPressEnter={rename}
                style={{ width: 320 }}
                autoFocus
              />
              <Button type="primary" onClick={rename} icon={<CheckOutlined />} />
              <Button onClick={() => setRenaming(false)}>Cancel</Button>
            </Space>
          )}

          {/* Progress */}
          {busy && (
            <div
              style={{
                background: 'rgba(79,172,254,0.08)',
                border: '1px solid rgba(79,172,254,0.28)',
                borderRadius: 14,
                padding: '16px 20px',
                marginBottom: 18,
              }}
            >
              <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                <Text style={{ color: '#fff', fontWeight: 600 }}>
                  {job.status === 'analyzing' ? 'Finding the best moments' : 'Rendering clips'}
                </Text>
                <Text style={{ color: '#4facfe' }}>{Math.round((job.progress || 0) * 100)}%</Text>
              </Space>
              <Progress
                percent={Math.round((job.progress || 0) * 100)}
                showInfo={false}
                strokeColor={{ from: '#4facfe', to: '#00f2fe' }}
                style={{ marginBlock: 6 }}
              />
              <Text style={{ color: '#8c8c8c', fontSize: 12.5 }}>{job.message}</Text>
            </div>
          )}

          {job.error && (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 16, borderRadius: 12 }}
              message={job.status === 'error' ? job.message || 'Something failed' : 'Note'}
              description={<span style={{ fontSize: 13 }}>{job.error}</span>}
            />
          )}

          {job.warnings?.length > 0 && !busy && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16, borderRadius: 12 }}
              message="Heads up"
              description={
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                  {job.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              }
            />
          )}

          <Row gutter={[22, 22]}>
            {/* Clips panel */}
            <Col xs={24} xl={17}>
              <div
                style={{
                  background: 'rgba(26,26,46,0.55)',
                  border: '1px solid rgba(255,255,255,0.07)',
                  borderRadius: 18,
                  padding: 20,
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    gap: 12,
                    flexWrap: 'wrap',
                    marginBottom: 16,
                  }}
                >
                  <Space>
                    <Title level={4} style={{ color: '#fff', margin: 0 }}>
                      Clip candidates
                    </Title>
                    <Tag style={{ borderRadius: 999 }}>{clips.length}</Tag>
                    {renderedCount > 0 && <Tag color="green" style={{ borderRadius: 999 }}>{renderedCount} exported</Tag>}
                  </Space>
                  <Space>
                    <Checkbox
                      indeterminate={selected.length > 0 && selected.length < clips.length}
                      checked={selected.length === clips.length && clips.length > 0}
                      onChange={(event) =>
                        setSelected(event.target.checked ? clips.map((clip) => clip.id) : [])
                      }
                    >
                      <span style={{ color: '#bfbfbf', fontSize: 13 }}>Select all</span>
                    </Checkbox>
                    <Button
                      type="primary"
                      icon={<ScissorOutlined />}
                      disabled={busy || !clips.length}
                      onClick={() => startRender(selected.length ? selected : null)}
                      style={{ background: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)', border: 'none' }}
                    >
                      {selected.length ? `Export selected (${selected.length})` : 'Export all'}
                    </Button>
                  </Space>
                </div>

                {clips.length === 0 ? (
                  <div style={{ padding: '48px 0' }}>
                    <Empty
                      image={Empty.PRESENTED_IMAGE_SIMPLE}
                      description={
                        <span style={{ color: '#8c8c8c' }}>
                          {job.status === 'analyzing'
                            ? 'Scanning the video…'
                            : 'No clips found yet. Run analysis or loosen the thresholds in Settings.'}
                        </span>
                      }
                    >
                      {!busy && <Button onClick={startAnalysis}>Analyse now</Button>}
                    </Empty>
                  </div>
                ) : (
                  <Space direction="vertical" size={12} style={{ width: '100%' }}>
                    {candidates.map((clip, index) => (
                      <ClipRow
                        key={clip.id}
                        jobId={id}
                        clip={clip}
                        rank={index + 1}
                        checked={selected.includes(clip.id)}
                        onCheck={(checked) =>
                          setSelected((current) =>
                            checked ? [...current, clip.id] : current.filter((item) => item !== clip.id),
                          )
                        }
                        onPreview={() => setPreview(clip)}
                        onRender={() => startRender([clip.id])}
                        onDelete={() => handleDeleteClip(clip)}
                        busy={busy}
                      />
                    ))}
                  </Space>
                )}
              </div>
            </Col>

            {/* Export panel */}
            <Col xs={24} xl={7}>
              <div
                style={{
                  background: 'rgba(26,26,46,0.55)',
                  border: '1px solid rgba(255,255,255,0.07)',
                  borderRadius: 18,
                  padding: 20,
                  position: 'sticky',
                  top: 88,
                }}
              >
                <Title level={5} style={{ color: '#fff', marginTop: 0 }}>
                  Export format
                </Title>
                <Radio.Group
                  value={exportSettings.mode}
                  onChange={(event) => setExportSettings((current) => ({ ...current, mode: event.target.value }))}
                  style={{ width: '100%' }}
                >
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    {ASPECT_PRESETS.map((preset) => (
                      <Radio.Button
                        key={preset.key}
                        value={preset.key}
                        style={{
                          width: '100%',
                          height: 'auto',
                          padding: '10px 14px',
                          borderRadius: 10,
                          display: 'flex',
                          alignItems: 'center',
                        }}
                      >
                        <div>
                          <div style={{ fontWeight: 600 }}>{preset.label}</div>
                          <div style={{ fontSize: 11, opacity: 0.65 }}>{preset.detail}</div>
                        </div>
                      </Radio.Button>
                    ))}
                  </Space>
                </Radio.Group>

                <Divider style={{ margin: '16px 0', borderColor: 'rgba(255,255,255,0.08)' }} />

                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  <SettingRow
                    label="Burn in captions"
                    hint="Word-by-word highlight"
                    control={
                      <Switch
                        size="small"
                        checked={exportSettings.captions}
                        onChange={(checked) => setExportSettings((c) => ({ ...c, captions: checked }))}
                      />
                    }
                  />
                  {exportSettings.mode === 'vertical' && (
                    <>
                      <SettingRow
                        label="Follow the speaker"
                        hint="Face-tracking crop"
                        control={
                          <Switch
                            size="small"
                            checked={exportSettings.auto_reframe}
                            onChange={(checked) => setExportSettings((c) => ({ ...c, auto_reframe: checked }))}
                          />
                        }
                      />
                      <SettingRow
                        label="Blurred background"
                        hint="When the subject can't fill 9:16"
                        control={
                          <Switch
                            size="small"
                            checked={exportSettings.blurred_background}
                            onChange={(checked) => setExportSettings((c) => ({ ...c, blurred_background: checked }))}
                          />
                        }
                      />
                    </>
                  )}
                </Space>

                <Button
                  block
                  size="large"
                  type="primary"
                  icon={<ScissorOutlined />}
                  style={{
                    marginTop: 20,
                    background: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)',
                    border: 'none',
                    fontWeight: 600,
                  }}
                  disabled={busy || !clips.length}
                  onClick={() => startRender(selected.length ? selected : null)}
                >
                  {selected.length ? `Export ${selected.length} clip(s)` : 'Export all clips'}
                </Button>
                <Text style={{ color: '#737373', fontSize: 11.5, display: 'block', marginTop: 8 }}>
                  Renders on this PC. Long videos take a few minutes.
                </Text>

                <Divider style={{ margin: '18px 0', borderColor: 'rgba(255,255,255,0.08)' }} />

                <Title level={5} style={{ color: '#fff', marginTop: 0 }}>
                  Transcript
                </Title>
                <Space direction="vertical" size={6} style={{ width: '100%' }}>
                  <Button
                    block
                    size="small"
                    icon={<DownloadOutlined />}
                    disabled={!job.transcript_segments}
                    onClick={() => window.open(studioApi.transcriptUrl(id, 'srt'), '_blank')}
                  >
                    Download .srt
                  </Button>
                  <Button
                    block
                    size="small"
                    icon={<FileTextOutlined />}
                    disabled={!job.transcript_segments}
                    onClick={() => window.open(studioApi.transcriptUrl(id, 'txt'), '_blank')}
                  >
                    Download plain text
                  </Button>
                </Space>
              </div>
            </Col>
          </Row>
        </div>
      </Content>

      {/* Clip preview modal */}
      <Modal
        open={Boolean(preview)}
        onCancel={() => setPreview(null)}
        footer={null}
        width={Math.min(900, typeof window !== 'undefined' ? window.innerWidth - 60 : 900)}
        destroyOnClose
        styles={{ body: { padding: 0 } }}
        title={
          preview ? (
            <Space>
              <StarFilled style={{ color: scoreLabel(preview.score).color }} />
              <span>{preview.title || preview.id}</span>
              <Tag color={scoreLabel(preview.score).color}>{Math.round(preview.score * 100)}/100</Tag>
            </Space>
          ) : null
        }
      >
        {preview && (
          <div>
            <video
              key={preview.id}
              src={studioApi.clipUrl(id, preview.id)}
              controls
              autoPlay
              style={{ width: '100%', maxHeight: '68vh', background: '#000', display: 'block' }}
            />
            <div style={{ padding: '14px 18px' }}>
              <Space size={10} wrap style={{ marginBottom: 8 }}>
                <Tag icon={<ClockCircleOutlined />}>
                  {formatDuration(preview.start)} → {formatDuration(preview.end)}
                </Tag>
                <Tag>{preview.duration?.toFixed(1)}s</Tag>
                {preview.captions && <Tag color="cyan">captions</Tag>}
                {preview.crop_mode === 'blur' && <Tag color="purple">blurred fill</Tag>}
                {preview.encoder && <Tag>{preview.encoder}</Tag>}
              </Space>
              {preview.hook_text && (
                <Paragraph style={{ color: '#e6e6e6', marginBottom: 8 }}>“{preview.hook_text}”</Paragraph>
              )}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 8 }}>
                {Object.entries(preview.breakdown || {}).map(([key, value]) => (
                  <div key={key}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#8c8c8c' }}>
                      <span>{SCORE_LABELS[key] || key}</span>
                      <span>{Math.round(value * 100)}</span>
                    </div>
                    <Progress
                      percent={Math.round(value * 100)}
                      showInfo={false}
                      size="small"
                      strokeColor={value >= 0.7 ? '#52c41a' : value >= 0.5 ? '#4facfe' : '#faad14'}
                    />
                  </div>
                ))}
              </div>
              <Space style={{ marginTop: 10 }} wrap>
                <Button
                  type="primary"
                  icon={<DownloadOutlined />}
                  onClick={() => window.open(studioApi.downloadClipUrl(id, preview.id), '_blank')}
                >
                  Download MP4
                </Button>
                <Button
                  icon={<FileTextOutlined />}
                  onClick={() => window.open(studioApi.clipSrtUrl(id, preview.id), '_blank')}
                >
                  Captions .srt
                </Button>
              </Space>
            </div>
          </div>
        )}
      </Modal>

      {/* Source preview */}
      <Modal
        open={showSource}
        onCancel={() => setShowSource(false)}
        footer={null}
        width={960}
        destroyOnClose
        title="Source video"
        styles={{ body: { padding: 0 } }}
      >
        <video src={studioApi.sourceUrl(id)} controls style={{ width: '100%', background: '#000' }} />
      </Modal>
    </Layout>
  )
}

const SettingRow: React.FC<{ label: string; hint?: string; control: React.ReactNode }> = ({
  label,
  hint,
  control,
}) => (
  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
    <div>
      <div style={{ color: '#e6e6e6', fontSize: 13 }}>{label}</div>
      {hint && <div style={{ color: '#737373', fontSize: 11.5 }}>{hint}</div>}
    </div>
    {control}
  </div>
)

interface ClipRowProps {
  jobId: string
  clip: StudioClip
  rank: number
  checked: boolean
  busy: boolean
  onCheck: (checked: boolean) => void
  onPreview: () => void
  onRender: () => void
  onDelete: () => void
}

const ClipRow: React.FC<ClipRowProps> = ({
  jobId,
  clip,
  rank,
  checked,
  busy,
  onCheck,
  onPreview,
  onRender,
  onDelete,
}) => {
  const score = scoreLabel(clip.score)
  const done = clip.status === 'done'
  return (
    <div
      style={{
        display: 'flex',
        gap: 14,
        background: checked ? 'rgba(79,172,254,0.09)' : 'rgba(255,255,255,0.025)',
        border: `1px solid ${checked ? 'rgba(79,172,254,0.4)' : 'rgba(255,255,255,0.06)'}`,
        borderRadius: 14,
        padding: 12,
        transition: 'all 0.15s ease',
      }}
    >
      <Checkbox checked={checked} onChange={(event) => onCheck(event.target.checked)} style={{ marginTop: 26 }} />

      <div
        onClick={onPreview}
        style={{
          position: 'relative',
          width: 108,
          height: 150,
          flexShrink: 0,
          borderRadius: 10,
          overflow: 'hidden',
          background: 'linear-gradient(135deg, #1b2436 0%, #12172a 100%)',
          cursor: 'pointer',
          border: '1px solid rgba(255,255,255,0.08)',
        }}
      >
        {clip.thumbnail ? (
          <img src={clip.thumbnail} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        ) : (
          <div
            style={{
              height: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'rgba(79,172,254,0.55)',
              fontSize: 26,
            }}
          >
            <PlayCircleFilled />
          </div>
        )}
        <div
          style={{
            position: 'absolute',
            bottom: 0,
            left: 0,
            right: 0,
            background: 'rgba(0,0,0,0.72)',
            color: '#fff',
            fontSize: 11,
            textAlign: 'center',
            padding: '3px 0',
          }}
        >
          {clip.duration?.toFixed(1)}s
        </div>
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
          <Tag
            color={score.color}
            style={{ borderRadius: 999, margin: 0, fontSize: 11, fontWeight: 600 }}
          >
            #{rank} · {score.text} {Math.round(clip.score * 100)}
          </Tag>
          {done && (
            <Tag color="green" style={{ borderRadius: 999, margin: 0, fontSize: 11 }}>
              exported
            </Tag>
          )}
          {clip.crop_mode === 'blur' && (
            <Tag color="purple" style={{ borderRadius: 999, margin: 0, fontSize: 11 }}>
              blur fill
            </Tag>
          )}
          <Text style={{ color: '#737373', fontSize: 11.5 }}>
            {formatDuration(clip.start)}–{formatDuration(clip.end)}
          </Text>
        </div>

        <div
          style={{
            color: '#fff',
            fontWeight: 600,
            fontSize: 14,
            lineHeight: 1.35,
            marginBottom: 4,
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {clip.title || `Clip ${clip.id}`}
        </div>

        {clip.hook_text && (
          <div
            style={{
              color: '#a6a6a6',
              fontSize: 12.5,
              lineHeight: 1.45,
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            “{clip.hook_text}”
          </div>
        )}

        <Space size={4} wrap style={{ marginTop: 6 }}>
          {(clip.reasons || []).slice(0, 3).map((reason) => (
            <Tag
              key={reason}
              style={{
                margin: 0,
                fontSize: 10.5,
                background: 'rgba(255,255,255,0.05)',
                border: '1px solid rgba(255,255,255,0.09)',
                color: '#b0b0b0',
                borderRadius: 6,
              }}
            >
              {reason}
            </Tag>
          ))}
        </Space>

        <Space size={6} style={{ marginTop: 8 }} wrap>
          <Button size="small" icon={<PlayCircleFilled />} onClick={onPreview}>
            Preview
          </Button>
          <Button
            size="small"
            icon={<ScissorOutlined />}
            onClick={onRender}
            disabled={busy}
            type={done ? 'default' : 'primary'}
          >
            {done ? 'Re-export' : 'Export'}
          </Button>
          {done && (
            <Tooltip title="Download MP4">
              <Button
                size="small"
                icon={<DownloadOutlined />}
                onClick={() => window.open(studioApi.downloadClipUrl(jobId, clip.id), '_blank')}
              />
            </Tooltip>
          )}
          {done && (
            <Tooltip title="Delete exported file">
              <Button size="small" danger icon={<DeleteOutlined />} onClick={onDelete} />
            </Tooltip>
          )}
          {clip.status === 'error' && (
            <Tooltip title={clip.error}>
              <Tag color="error" style={{ margin: 0, fontSize: 11 }}>
                export failed
              </Tag>
            </Tooltip>
          )}
        </Space>
      </div>
    </div>
  )
}

export default StudioJobPage
