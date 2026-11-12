import React from 'react'
import { Button, Dropdown, Progress, Space, Tooltip, Typography } from 'antd'
import {
  DeleteOutlined,
  DownloadOutlined,
  MoreOutlined,
  PlayCircleOutlined,
  ScissorOutlined,
} from '@ant-design/icons'
import { StudioJob } from '../../services/studioApi'
import StatusPill from './StatusPill'
import { formatDuration, formatRelative } from '../../utils/format'

const { Text } = Typography

interface Props {
  job: StudioJob
  onOpen: (job: StudioJob) => void
  onDelete: (job: StudioJob) => void
  onRender: (job: StudioJob) => void
}

const JobCard: React.FC<Props> = ({ job, onOpen, onDelete, onRender }) => {
  const busy = job.status === 'analyzing' || job.status === 'rendering'
  const rendered = job.clips.filter((clip) => clip.status === 'done').length
  const thumbnail = job.clips.find((clip) => clip.thumbnail)?.thumbnail

  return (
    <div
      onClick={() => onOpen(job)}
      style={{
        background: 'rgba(26, 26, 46, 0.65)',
        border: '1px solid rgba(255,255,255,0.07)',
        borderRadius: 16,
        overflow: 'hidden',
        cursor: 'pointer',
        transition: 'all 0.2s ease',
        display: 'flex',
        flexDirection: 'column',
      }}
      onMouseEnter={(event) => {
        event.currentTarget.style.borderColor = 'rgba(79, 172, 254, 0.45)'
        event.currentTarget.style.transform = 'translateY(-2px)'
      }}
      onMouseLeave={(event) => {
        event.currentTarget.style.borderColor = 'rgba(255,255,255,0.07)'
        event.currentTarget.style.transform = 'translateY(0)'
      }}
    >
      <div
        style={{
          position: 'relative',
          aspectRatio: '16 / 9',
          background: 'linear-gradient(135deg, #171726 0%, #1f2a40 100%)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          overflow: 'hidden',
        }}
      >
        {thumbnail ? (
          <img
            src={thumbnail}
            alt=""
            style={{ width: '100%', height: '100%', objectFit: 'cover', opacity: 0.9 }}
          />
        ) : (
          <PlayCircleOutlined style={{ fontSize: 40, color: 'rgba(79,172,254,0.5)' }} />
        )}
        <div style={{ position: 'absolute', top: 10, left: 10 }}>
          <StatusPill status={job.status} />
        </div>
        {job.duration > 0 && (
          <div
            style={{
              position: 'absolute',
              bottom: 10,
              right: 10,
              background: 'rgba(0,0,0,0.72)',
              color: '#fff',
              fontSize: 12,
              padding: '2px 8px',
              borderRadius: 6,
            }}
          >
            {formatDuration(job.duration)}
          </div>
        )}
      </div>

      <div style={{ padding: '14px 16px 16px', display: 'flex', flexDirection: 'column', gap: 8, flex: 1 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
          <Text
            style={{
              color: '#fff',
              fontWeight: 600,
              fontSize: 15,
              overflow: 'hidden',
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
            }}
          >
            {job.name}
          </Text>
          <Dropdown
            trigger={['click']}
            menu={{
              items: [
                { key: 'open', label: 'Open', icon: <ScissorOutlined /> },
                { key: 'render', label: 'Export all clips', icon: <DownloadOutlined />, disabled: !job.clips.length },
                { type: 'divider' },
                { key: 'delete', label: 'Delete project', icon: <DeleteOutlined />, danger: true },
              ],
              onClick: ({ key, domEvent }) => {
                domEvent.stopPropagation()
                if (key === 'open') onOpen(job)
                if (key === 'render') onRender(job)
                if (key === 'delete') onDelete(job)
              },
            }}
          >
            <Button
              type="text"
              size="small"
              icon={<MoreOutlined />}
              onClick={(event) => event.stopPropagation()}
            />
          </Dropdown>
        </div>

        {busy ? (
          <div>
            <Progress
              percent={Math.round((job.progress || 0) * 100)}
              size="small"
              strokeColor={{ from: '#4facfe', to: '#00f2fe' }}
              showInfo={false}
            />
            <Text style={{ color: '#8c8c8c', fontSize: 12 }}>{job.message || 'Working…'}</Text>
          </div>
        ) : (
          <Space size={6} wrap>
            <Text style={{ color: '#8c8c8c', fontSize: 12 }}>
              {job.clips.length} candidate{job.clips.length === 1 ? '' : 's'}
            </Text>
            {rendered > 0 && (
              <Tooltip title="Rendered clips ready to post">
                <Text style={{ color: '#52c41a', fontSize: 12 }}>· {rendered} exported</Text>
              </Tooltip>
            )}
            <Text style={{ color: '#595959', fontSize: 12 }}>· {formatRelative(job.created_at)}</Text>
          </Space>
        )}
      </div>
    </div>
  )
}

export default JobCard
