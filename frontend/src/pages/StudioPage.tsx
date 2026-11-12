import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Col,
  Empty,
  Input,
  Layout,
  Modal,
  Row,
  Segmented,
  Skeleton,
  Space,
  Statistic,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  CheckCircleFilled,
  CloseCircleFilled,
  ExperimentOutlined,
  ReloadOutlined,
  SearchOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons'
import studioApi, { HealthReport, StudioJob, StudioStats } from '../services/studioApi'
import UploadPanel from '../components/studio/UploadPanel'
import JobCard from '../components/studio/JobCard'
import { formatDuration } from '../utils/format'

const { Content } = Layout
const { Title, Text } = Typography

type Filter = 'all' | 'active' | 'done' | 'error'

const StudioPage: React.FC = () => {
  const navigate = useNavigate()
  const [jobs, setJobs] = useState<StudioJob[]>([])
  const [stats, setStats] = useState<StudioStats | null>(null)
  const [health, setHealth] = useState<HealthReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const [dismissedHealth, setDismissedHealth] = useState(false)
  const pollRef = useRef<number | null>(null)

  const loadJobs = useCallback(async () => {
    try {
      const [jobsResponse, statsResponse] = await Promise.all([studioApi.listJobs(), studioApi.stats()])
      setJobs(jobsResponse.jobs || [])
      setStats(statsResponse)
    } catch (error: any) {
      message.error(error?.userMessage || 'Could not reach the AutoClip server')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadJobs()
    studioApi.health().then(setHealth).catch(() => setHealth(null))
  }, [loadJobs])

  // Poll only while something is actually running - no idle CPU burn.
  useEffect(() => {
    const busy = jobs.some((job) => job.status === 'analyzing' || job.status === 'rendering')
    if (busy && pollRef.current === null) {
      pollRef.current = window.setInterval(loadJobs, 1500)
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
  }, [jobs, loadJobs])

  useEffect(() => () => {
    if (pollRef.current !== null) window.clearInterval(pollRef.current)
  }, [])

  const handleCreated = async (job: StudioJob) => {
    setJobs((current) => [job, ...current])
    try {
      await studioApi.analyze(job.id, {})
      message.loading({ content: 'Scanning for the best moments…', key: job.id, duration: 2 })
      navigate(`/studio/${job.id}`)
    } catch (error: any) {
      message.error(error?.userMessage || 'Could not start analysis')
      navigate(`/studio/${job.id}`)
    }
  }

  const handleDelete = (job: StudioJob) => {
    Modal.confirm({
      title: `Delete "${job.name}"?`,
      content: 'The source video and every rendered clip for this project will be removed from disk.',
      okText: 'Delete',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await studioApi.deleteJob(job.id)
          setJobs((current) => current.filter((item) => item.id !== job.id))
          message.success('Project deleted')
          loadJobs()
        } catch (error: any) {
          message.error(error?.userMessage || 'Delete failed')
        }
      },
    })
  }

  const handleRenderAll = async (job: StudioJob) => {
    try {
      await studioApi.render(job.id, null, {})
      message.success('Exporting all clips')
      navigate(`/studio/${job.id}`)
    } catch (error: any) {
      message.error(error?.userMessage || 'Could not start export')
    }
  }

  const filtered = jobs.filter((job) => {
    const matchesFilter =
      filter === 'all' ||
      (filter === 'active' && ['uploaded', 'analyzing', 'analyzed', 'rendering'].includes(job.status)) ||
      (filter === 'done' && job.status === 'completed') ||
      (filter === 'error' && ['error', 'interrupted'].includes(job.status))
    const matchesSearch = !search.trim() || job.name.toLowerCase().includes(search.toLowerCase())
    return matchesFilter && matchesSearch
  })

  const asr = health?.transcription || {}
  const speechEngine = Object.entries(asr).find(
    ([key, value]) => ['faster_whisper', 'openai_whisper', 'whisper_cpp'].includes(key) && value.available,
  )
  const showSetupBanner = health && (!health.ok || (!speechEngine && !dismissedHealth))

  return (
    <Layout style={{ minHeight: '100vh', background: '#0f0f0f' }}>
      <Content style={{ padding: '32px 24px 64px' }}>
        <div style={{ maxWidth: 1500, margin: '0 auto' }}>
          {/* Header */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'flex-end',
              gap: 16,
              flexWrap: 'wrap',
              marginBottom: 26,
            }}
          >
            <div>
              <Space align="center" size={10}>
                <ThunderboltOutlined style={{ fontSize: 26, color: '#4facfe' }} />
                <Title level={2} style={{ color: '#fff', margin: 0, letterSpacing: -0.5 }}>
                  Clipping Studio
                </Title>
              </Space>
              <div style={{ color: '#8c8c8c', marginTop: 6, fontSize: 14 }}>
                Drop a long video, get short vertical clips with captions — all on your PC.
              </div>
            </div>
            <Space>
              <Button icon={<ReloadOutlined />} onClick={loadJobs}>
                Refresh
              </Button>
              <Button icon={<SettingOutlined />} onClick={() => navigate('/studio/settings')}>
                Settings
              </Button>
            </Space>
          </div>

          {/* Setup / capability banner */}
          {showSetupBanner && (
            <Alert
              type={health && !health.ok ? 'error' : 'info'}
              showIcon
              closable={health?.ok}
              onClose={() => setDismissedHealth(true)}
              style={{ marginBottom: 20, borderRadius: 12 }}
              message={
                health && !health.ok
                  ? 'FFmpeg is missing'
                  : 'Automatic speech recognition is not set up (optional)'
              }
              description={
                health && !health.ok ? (
                  <span>{health.ffmpeg?.error || 'Install FFmpeg to enable clipping.'}</span>
                ) : (
                  <div style={{ fontSize: 13, lineHeight: 1.7 }}>
                    Clips can still be found from audio energy and scene changes, but a speech model
                    picks much better moments and enables burned-in captions.
                    <div style={{ marginTop: 6 }}>
                      <Text code>pip install faster-whisper</Text> — then restart AutoClip. First run
                      downloads a small model (about 140 MB for <Text code>base</Text>).
                    </div>
                    <div style={{ marginTop: 4, color: '#8c8c8c' }}>
                      Already have captions? Drop a <Text code>.srt</Text> with your video instead.
                    </div>
                  </div>
                )
              }
            />
          )}
          {health?.ok && speechEngine && !dismissedHealth && (
            <Alert
              type="success"
              showIcon
              closable
              onClose={() => setDismissedHealth(true)}
              style={{ marginBottom: 20, borderRadius: 12 }}
              message={
                <span>
                  Ready: {speechEngine[1].label} detected
                  {health.fonts?.family ? ` · captions use ${health.fonts.family}` : ''}
                </span>
              }
              action={
                <Tag color="green" style={{ marginRight: 0 }}>
                  <CheckCircleFilled /> Fully offline
                </Tag>
              }
            />
          )}

          {/* Stats */}
          <Row gutter={[14, 14]} style={{ marginBottom: 22 }}>
            {[
              { title: 'Projects', value: stats?.jobs ?? jobs.length, icon: <VideoCameraOutlined /> },
              { title: 'Clips exported', value: stats?.rendered_clips ?? 0, icon: <CheckCircleFilled /> },
              {
                title: 'Footage analysed',
                value: formatDuration(stats?.source_seconds || 0),
                icon: <ExperimentOutlined />,
              },
              {
                title: 'Short-form produced',
                value: formatDuration(stats?.clips_seconds || 0),
                icon: <ThunderboltOutlined />,
              },
            ].map((item) => (
              <Col xs={12} md={6} key={item.title}>
                <div
                  style={{
                    background: 'rgba(26, 26, 46, 0.6)',
                    border: '1px solid rgba(255,255,255,0.07)',
                    borderRadius: 14,
                    padding: '14px 18px',
                  }}
                >
                  <Statistic
                    title={<span style={{ color: '#8c8c8c', fontSize: 12 }}>{item.title}</span>}
                    value={item.value}
                    prefix={<span style={{ color: '#4facfe', marginRight: 6 }}>{item.icon}</span>}
                    valueStyle={{ color: '#fff', fontSize: 22, fontWeight: 600 }}
                  />
                </div>
              </Col>
            ))}
          </Row>

          <Row gutter={[20, 20]}>
            <Col xs={24} lg={9} xl={8}>
              <UploadPanel onCreated={handleCreated} />
              <div
                style={{
                  marginTop: 16,
                  padding: '14px 16px',
                  borderRadius: 14,
                  background: 'rgba(255,255,255,0.03)',
                  border: '1px solid rgba(255,255,255,0.06)',
                  color: '#8c8c8c',
                  fontSize: 12.5,
                  lineHeight: 1.75,
                }}
              >
                <div style={{ color: '#cfcfcf', fontWeight: 600, marginBottom: 4 }}>How it works</div>
                1. Video is analysed on this PC (speech → scenes → audio energy).
                <br />
                2. Candidate moments are scored for hooks, pacing and completeness.
                <br />
                3. Pick clips and export 9:16 videos with face tracking + captions.
              </div>
            </Col>

            <Col xs={24} lg={15} xl={16}>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  gap: 12,
                  flexWrap: 'wrap',
                  marginBottom: 14,
                }}
              >
                <Space>
                  <Title level={4} style={{ color: '#fff', margin: 0 }}>
                    Projects
                  </Title>
                  <Tag style={{ borderRadius: 999 }}>{jobs.length}</Tag>
                </Space>
                <Space>
                  <Input
                    allowClear
                    placeholder="Search projects"
                    prefix={<SearchOutlined style={{ color: '#666' }} />}
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    style={{ width: 190 }}
                  />
                  <Segmented
                    value={filter}
                    onChange={(value) => setFilter(value as Filter)}
                    options={[
                      { label: 'All', value: 'all' },
                      { label: 'Active', value: 'active' },
                      { label: 'Done', value: 'done' },
                      { label: 'Issues', value: 'error' },
                    ]}
                  />
                </Space>
              </div>

              {loading ? (
                <Row gutter={[16, 16]}>
                  {[1, 2, 3, 4].map((key) => (
                    <Col xs={24} md={12} xl={8} key={key}>
                      <div style={{ background: 'rgba(26,26,46,0.5)', borderRadius: 16, padding: 16 }}>
                        <Skeleton active paragraph={{ rows: 3 }} />
                      </div>
                    </Col>
                  ))}
                </Row>
              ) : filtered.length === 0 ? (
                <div
                  style={{
                    background: 'rgba(26, 26, 46, 0.45)',
                    border: '1px dashed rgba(255,255,255,0.1)',
                    borderRadius: 16,
                    padding: '54px 20px',
                  }}
                >
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={
                      <span style={{ color: '#8c8c8c' }}>
                        {jobs.length === 0
                          ? 'No projects yet — drop a video on the left to get started.'
                          : 'No projects match this filter.'}
                      </span>
                    }
                  />
                  {jobs.length === 0 && health?.ok && (
                    <div style={{ textAlign: 'center', marginTop: 8 }}>
                      <Tooltip title="Creates a synthetic video with subtitles so you can try the whole flow">
                        <Button onClick={() => navigate('/studio/settings')} type="link">
                          No footage handy? Generate a demo video from Settings
                        </Button>
                      </Tooltip>
                    </div>
                  )}
                </div>
              ) : (
                <Row gutter={[16, 16]}>
                  {filtered.map((job) => (
                    <Col xs={24} md={12} xl={8} key={job.id}>
                      <JobCard
                        job={job}
                        onOpen={(item) => navigate(`/studio/${item.id}`)}
                        onDelete={handleDelete}
                        onRender={handleRenderAll}
                      />
                    </Col>
                  ))}
                </Row>
              )}
            </Col>
          </Row>

          {health && !health.ok && (
            <div style={{ marginTop: 22, color: '#f5222d', fontSize: 13 }}>
              <CloseCircleFilled /> {health.error || health.ffmpeg?.error}
            </div>
          )}
        </div>
      </Content>
    </Layout>
  )
}

export default StudioPage
