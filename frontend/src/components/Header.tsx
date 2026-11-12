import React from 'react'
import { Layout, Button, Segmented } from 'antd'
import { SettingOutlined, ScissorOutlined, AppstoreOutlined } from '@ant-design/icons'
import { useNavigate, useLocation } from 'react-router-dom'

const { Header: AntHeader } = Layout

const Header: React.FC = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const inStudio = !location.pathname.startsWith('/project')
  const section = inStudio ? 'studio' : 'projects'

  return (
    <AntHeader
      className="glass-effect"
      style={{
        padding: '0 24px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 16,
        height: '72px',
        position: 'sticky',
        top: 0,
        zIndex: 1000,
        backdropFilter: 'blur(20px)',
        background: 'rgba(26, 26, 26, 0.9)',
        borderBottom: '1px solid rgba(255, 255, 255, 0.1)'
      }}
    >
      {/* Logo */}
      <div
        style={{ display: 'flex', alignItems: 'center', gap: 16, minWidth: 0 }}
      >
        <span
          onClick={() => navigate('/')}
          style={{
            cursor: 'pointer',
            fontSize: '24px',
            fontWeight: '700',
            background: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            backgroundClip: 'text',
            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
            letterSpacing: '-0.5px',
            textShadow: '0 0 20px rgba(79, 172, 254, 0.3)',
            filter: 'drop-shadow(0 2px 4px rgba(79, 172, 254, 0.2))',
            transition: 'transform 0.2s ease'
          }}
          onMouseEnter={(e) => { e.currentTarget.style.transform = 'scale(1.05)' }}
          onMouseLeave={(e) => { e.currentTarget.style.transform = 'scale(1)' }}
        >
          AutoClip
        </span>

        <Segmented
          size="middle"
          value={section}
          onChange={(value) => navigate(value === 'studio' ? '/' : '/projects')}
          options={[
            { label: <span style={{ padding: '0 4px' }}><ScissorOutlined /> Studio</span>, value: 'studio' },
            { label: <span style={{ padding: '0 4px' }}><AppstoreOutlined /> AI Projects</span>, value: 'projects' },
          ]}
          style={{ background: 'rgba(0,0,0,0.3)' }}
        />
      </div>

      {/* Navigation Buttons */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <Button
          type="text"
          icon={<SettingOutlined />}
          onClick={() => navigate(inStudio ? '/studio/settings' : '/legacy-settings')}
          style={{
            color: '#cccccc',
            border: '1px solid transparent',
            borderRadius: '8px',
            height: '40px',
            padding: '0 16px'
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = '#2d2d2d'
            e.currentTarget.style.borderColor = '#404040'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = 'transparent'
            e.currentTarget.style.borderColor = 'transparent'
          }}
        >
          Settings
        </Button>
      </div>
    </AntHeader>
  )
}

export default Header
