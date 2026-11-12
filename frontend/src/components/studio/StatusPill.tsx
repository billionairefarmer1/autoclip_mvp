import React from 'react'
import { Tag } from 'antd'
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  ScissorOutlined,
  SearchOutlined,
} from '@ant-design/icons'
import { JobStatus } from '../../services/studioApi'

const MAP: Record<JobStatus, { label: string; color: string; icon: React.ReactNode }> = {
  uploading: { label: 'Uploading', color: 'blue', icon: <LoadingOutlined /> },
  uploaded: { label: 'Ready', color: 'default', icon: <ClockCircleOutlined /> },
  analyzing: { label: 'Analysing', color: 'processing', icon: <SearchOutlined /> },
  analyzed: { label: 'Clips found', color: 'cyan', icon: <CheckCircleOutlined /> },
  rendering: { label: 'Rendering', color: 'processing', icon: <ScissorOutlined /> },
  completed: { label: 'Exported', color: 'success', icon: <CheckCircleOutlined /> },
  error: { label: 'Error', color: 'error', icon: <CloseCircleOutlined /> },
  interrupted: { label: 'Interrupted', color: 'warning', icon: <ClockCircleOutlined /> },
}

const StatusPill: React.FC<{ status: JobStatus }> = ({ status }) => {
  const item = MAP[status] || MAP.uploaded
  const busy = status === 'analyzing' || status === 'rendering'
  return (
    <Tag
      color={item.color}
      icon={busy ? <LoadingOutlined /> : item.icon}
      style={{ borderRadius: 999, paddingInline: 10, margin: 0, fontWeight: 500 }}
    >
      {item.label}
    </Tag>
  )
}

export default StatusPill
