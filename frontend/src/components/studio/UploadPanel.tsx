import React, { useCallback, useRef, useState } from 'react'
import { Alert, Button, Input, Progress, Space, Tag, Typography, message } from 'antd'
import {
  CheckCircleFilled,
  CloudUploadOutlined,
  FileTextOutlined,
  InboxOutlined,
  CloseOutlined,
} from '@ant-design/icons'
import studioApi, { StudioJob } from '../../services/studioApi'
import { formatBytes } from '../../utils/format'

const { Text, Title } = Typography

const VIDEO_TYPES = '.mp4,.mov,.mkv,.avi,.webm,.m4v,.flv,.ts,.mpg,.mpeg,.wmv'

interface Props {
  onCreated: (job: StudioJob) => void
}

const UploadPanel: React.FC<Props> = ({ onCreated }) => {
  const [video, setVideo] = useState<File | null>(null)
  const [subtitle, setSubtitle] = useState<File | null>(null)
  const [name, setName] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [percent, setPercent] = useState(0)
  const videoInput = useRef<HTMLInputElement>(null)
  const srtInput = useRef<HTMLInputElement>(null)

  const pickVideo = useCallback((file: File | undefined | null) => {
    if (!file) return
    setVideo(file)
    if (!name) setName(file.name.replace(/\.[^.]+$/, ''))
  }, [name])

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragOver(false)
    const files = Array.from(event.dataTransfer.files)
    const videoFile = files.find((file) => /\.(mp4|mov|mkv|avi|webm|m4v|flv|ts|mpe?g|wmv)$/i.test(file.name))
    const srtFile = files.find((file) => /\.(srt|vtt)$/i.test(file.name))
    if (videoFile) pickVideo(videoFile)
    if (srtFile) setSubtitle(srtFile)
    if (!videoFile && !srtFile) {
      message.warning('Drop a video file (or a .srt alongside one)')
    }
  }

  const handleUpload = async () => {
    if (!video) {
      message.warning('Choose a video first')
      return
    }
    setUploading(true)
    setPercent(0)
    try {
      const response = await studioApi.uploadJob(video, name, subtitle, setPercent)
      message.success('Video uploaded - starting analysis')
      onCreated(response.job)
      setVideo(null)
      setSubtitle(null)
      setName('')
    } catch (error: any) {
      message.error(error?.userMessage || 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault()
        setDragOver(true)
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      style={{
        background: dragOver ? 'rgba(79, 172, 254, 0.12)' : 'rgba(26, 26, 46, 0.72)',
        border: `2px dashed ${dragOver ? '#4facfe' : 'rgba(79, 172, 254, 0.35)'}`,
        borderRadius: 20,
        padding: '34px 28px',
        transition: 'all 0.2s ease',
      }}
    >
      <input
        ref={videoInput}
        type="file"
        accept={VIDEO_TYPES}
        style={{ display: 'none' }}
        onChange={(event) => pickVideo(event.target.files?.[0])}
      />
      <input
        ref={srtInput}
        type="file"
        accept=".srt,.vtt"
        style={{ display: 'none' }}
        onChange={(event) => setSubtitle(event.target.files?.[0] || null)}
      />

      {!video ? (
        <div style={{ textAlign: 'center' }}>
          <InboxOutlined style={{ fontSize: 52, color: '#4facfe' }} />
          <Title level={4} style={{ color: '#fff', marginTop: 12, marginBottom: 4 }}>
            Drop a video to start clipping
          </Title>
          <Text style={{ color: '#9aa0a6' }}>
            MP4, MOV, MKV, AVI, WebM · everything stays on your computer
          </Text>
          <div style={{ marginTop: 18 }}>
            <Button
              type="primary"
              size="large"
              icon={<CloudUploadOutlined />}
              onClick={() => videoInput.current?.click()}
              style={{
                background: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)',
                border: 'none',
                borderRadius: 10,
                fontWeight: 600,
                paddingInline: 26,
              }}
            >
              Choose a video
            </Button>
          </div>
        </div>
      ) : (
        <div>
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 12,
                background: 'rgba(79, 172, 254, 0.08)',
                border: '1px solid rgba(79, 172, 254, 0.25)',
                borderRadius: 12,
                padding: '12px 14px',
              }}
            >
              <Space>
                <CheckCircleFilled style={{ color: '#52c41a', fontSize: 18 }} />
                <div>
                  <div style={{ color: '#fff', fontWeight: 600, wordBreak: 'break-all' }}>{video.name}</div>
                  <Text style={{ color: '#8c8c8c', fontSize: 12 }}>{formatBytes(video.size)}</Text>
                </div>
              </Space>
              {!uploading && (
                <Button
                  type="text"
                  icon={<CloseOutlined />}
                  onClick={() => {
                    setVideo(null)
                    setPercent(0)
                  }}
                />
              )}
            </div>

            <div>
              <Text style={{ color: '#bfbfbf', fontSize: 13 }}>Project name</Text>
              <Input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. Podcast ep. 42"
                size="large"
                style={{ marginTop: 6 }}
              />
            </div>

            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 12,
                background: 'rgba(255,255,255,0.03)',
                border: '1px solid rgba(255,255,255,0.08)',
                borderRadius: 12,
                padding: '10px 14px',
              }}
            >
              <Space>
                <FileTextOutlined style={{ color: subtitle ? '#52c41a' : '#8c8c8c' }} />
                <div>
                  <div style={{ color: '#e6e6e6', fontSize: 13 }}>
                    {subtitle ? subtitle.name : 'Optional: subtitle file (.srt / .vtt)'}
                  </div>
                  <Text style={{ color: '#737373', fontSize: 12 }}>
                    {subtitle
                      ? 'Clips will use these exact timings'
                      : 'Skip speech-to-text if you already have captions'}
                  </Text>
                </div>
              </Space>
              {subtitle ? (
                <Button type="text" icon={<CloseOutlined />} onClick={() => setSubtitle(null)} />
              ) : (
                <Button size="small" onClick={() => srtInput.current?.click()}>
                  Add
                </Button>
              )}
            </div>

            {uploading && (
              <div>
                <Progress
                  percent={percent}
                  strokeColor={{ from: '#4facfe', to: '#00f2fe' }}
                  status="active"
                />
                <Text style={{ color: '#8c8c8c', fontSize: 12 }}>
                  Uploading to your local AutoClip server…
                </Text>
              </div>
            )}

            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button onClick={() => videoInput.current?.click()} disabled={uploading}>
                Pick another
              </Button>
              <Button
                type="primary"
                size="large"
                loading={uploading}
                onClick={handleUpload}
                style={{
                  background: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)',
                  border: 'none',
                  borderRadius: 10,
                  fontWeight: 600,
                  paddingInline: 26,
                }}
              >
                Upload &amp; find clips
              </Button>
            </Space>

            <Alert
              type="info"
              showIcon
              banner
              style={{ borderRadius: 10, background: 'rgba(79,172,254,0.08)', border: 'none' }}
              message={
                <span style={{ fontSize: 12, color: '#bfbfbf' }}>
                  <Tag color="blue" style={{ marginRight: 8 }}>100% offline</Tag>
                  Videos are never uploaded to a server. Analysis and rendering happen on this PC.
                </span>
              }
            />
          </Space>
        </div>
      )}
    </div>
  )
}

export default UploadPanel
