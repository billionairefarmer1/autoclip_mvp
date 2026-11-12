import React, { useState, useEffect } from 'react'
import { Layout, Card, Form, Input, Button, message, Typography, Space, Alert, Divider, Row, Col, Spin, Select } from 'antd'
import { KeyOutlined, SaveOutlined, SettingOutlined, InfoCircleOutlined } from '@ant-design/icons'
import { settingsApi } from '../services/api'
import './SettingsPage.css'

const { Content } = Layout
const { Title, Text, Paragraph } = Typography

interface BrowserInfo {
  name: string
  value: string
  available: boolean
  priority: number
}

interface ApiSettings {
  dashscope_api_key: string
  siliconflow_api_key: string
  api_provider: string
  model_name: string
  siliconflow_model: string
  chunk_size: number
  min_score_threshold: number
  max_clips_per_collection: number
  default_browser?: string
}

const SettingsPage: React.FC = () => {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [availableBrowsers, setAvailableBrowsers] = useState<BrowserInfo[]>([])
  const [detectingBrowsers, setDetectingBrowsers] = useState(false)
  const [selectedBrowser, setSelectedBrowser] = useState<string>('')
  const [selectedProvider, setSelectedProvider] = useState<string>('dashscope')

  useEffect(() => {
    loadSettings()
    detectAvailableBrowsers()
  }, [])

  // 检测可用浏览器
  const detectAvailableBrowsers = async () => {
    setDetectingBrowsers(true)
    try {
      const response = await fetch('http://localhost:8000/api/browsers/detect')
      if (response.ok) {
        const data = await response.json()
        const browsers: BrowserInfo[] = data.browsers
        setAvailableBrowsers(browsers)
        
        // 自动选择第一个可用的浏览器，优先选择Chrome
        const chromeBrowser = browsers.find(b => b.value === 'chrome' && b.available)
        const firstAvailable = chromeBrowser || browsers.find(b => b.available)
        if (firstAvailable) {
          form.setFieldValue('default_browser', firstAvailable.value)
          setSelectedBrowser(firstAvailable.value)
        }
      } else {
        // 如果API调用失败，使用默认配置
        const browsers: BrowserInfo[] = [
          { name: 'Chrome', value: 'chrome', available: true, priority: 1 },
          { name: 'Edge', value: 'edge', available: true, priority: 2 },
          { name: 'Firefox', value: 'firefox', available: true, priority: 3 },
          { name: 'Safari', value: 'safari', available: true, priority: 4 }
        ]
        setAvailableBrowsers(browsers)
        form.setFieldValue('default_browser', 'chrome')
        setSelectedBrowser('chrome')
      }
    } catch (error) {
      console.error('检测浏览器失败:', error)
      // 使用默认配置
      const browsers: BrowserInfo[] = [
        { name: 'Chrome', value: 'chrome', available: true, priority: 1 },
        { name: 'Edge', value: 'edge', available: true, priority: 2 },
        { name: 'Firefox', value: 'firefox', available: true, priority: 3 },
        { name: 'Safari', value: 'safari', available: true, priority: 4 }
      ]
      setAvailableBrowsers(browsers)
      form.setFieldValue('default_browser', 'chrome')
      setSelectedBrowser('chrome')
    } finally {
      setDetectingBrowsers(false)
    }
  }

  const loadSettings = async () => {
    try {
      const data = (await settingsApi.getSettings()) as Record<string, unknown>
      form.setFieldsValue(data as any)
      if (data.default_browser) setSelectedBrowser(String(data.default_browser))
      if (data.api_provider) setSelectedProvider(String(data.api_provider))
    } catch (error) {
      message.error('加载配置失败')
      console.error('Load settings error:', error)
    }
  }

  const handleSave = async (values: ApiSettings) => {
    setLoading(true)
    try {
      await settingsApi.updateSettings(values)
      message.success('配置保存成功')
    } catch (error) {
      message.error('保存配置失败')
      console.error('Save settings error:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleTestApi = async () => {
    const values = form.getFieldsValue()
    const provider = values.api_provider || selectedProvider
    
    let apiKey = ''
    let model = ''
    
    if (provider === 'dashscope') {
      apiKey = values.dashscope_api_key
      model = values.model_name
    } else if (provider === 'siliconflow') {
      apiKey = values.siliconflow_api_key
      model = values.siliconflow_model
    }
    
    if (!apiKey) {
      message.error('请先输入API密钥')
      return
    }
    
    setLoading(true)
    try {
      const result = await settingsApi.testApiKey(apiKey, provider, model)
      if (result.success) {
        message.success('API连接测试成功')
      } else {
        message.error(`API连接测试失败: ${result.error}`)
      }
    } catch (error) {
      message.error('API连接测试失败')
      console.error('Test API error:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleProviderChange = (value: string) => {
    setSelectedProvider(value)
    form.setFieldValue('api_provider', value)
  }

  return (
    <Content className="settings-page">
      <div className="settings-container">
        <Title level={2} className="settings-title">
          <SettingOutlined /> 系统配置
        </Title>
        
        <Card title="API 配置" className="settings-card">
          <Alert
            message="配置说明"
            description="请选择API提供商并配置相应的API密钥以启用AI自动切片功能。"
            type="info"
            showIcon
            className="settings-alert"
          />
          
          <Form
            form={form}
            layout="vertical"
            onFinish={handleSave}
            className="settings-form"
            initialValues={{
              api_provider: 'dashscope',
              model_name: 'qwen-plus',
              siliconflow_model: 'Qwen/Qwen2.5-72B-Instruct',
              chunk_size: 5000,
              min_score_threshold: 0.7,
              max_clips_per_collection: 5
            }}
          >
            {/* API提供商选择 */}
            <Form.Item
              label="API提供商"
              name="api_provider"
              className="form-item"
              rules={[{ required: true, message: '请选择API提供商' }]}
            >
              <Select 
                placeholder="请选择API提供商" 
                className="settings-input"
                onChange={handleProviderChange}
                value={selectedProvider}
              >
                <Select.Option value="dashscope">阿里云 (DashScope)</Select.Option>
                <Select.Option value="siliconflow">硅基流动 (SiliconFlow)</Select.Option>
              </Select>
            </Form.Item>

            {/* 通义千问配置 */}
            {selectedProvider === 'dashscope' && (
              <>
                <Form.Item
                  label="DashScope API Key"
                  name="dashscope_api_key"
                  className="form-item"
                  rules={[
                    { required: true, message: '请输入API密钥' },
                    { min: 10, message: 'API密钥长度不能少于10位' }
                  ]}
                >
                  <Input.Password
                    placeholder="请输入通义千问API密钥"
                    prefix={<KeyOutlined />}
                    className="settings-input"
                  />
                </Form.Item>

                <Form.Item
                  label="通义千问模型"
                  name="model_name"
                  className="form-item"
                  rules={[{ required: true, message: '请选择模型' }]}
                >
                  <Select placeholder="请选择模型" className="settings-input">
                    <Select.Option value="qwen-plus">Qwen Plus</Select.Option>
                    <Select.Option value="qwen-turbo">Qwen Turbo</Select.Option>
                    <Select.Option value="qwen-max">Qwen Max</Select.Option>
                  </Select>
                </Form.Item>
              </>
            )}

            {/* 硅基流动配置 */}
            {selectedProvider === 'siliconflow' && (
              <>
                <Form.Item
                  label="SiliconFlow API Key"
                  name="siliconflow_api_key"
                  className="form-item"
                  rules={[
                    { required: true, message: '请输入API密钥' },
                    { min: 10, message: 'API密钥长度不能少于10位' }
                  ]}
                >
                  <Input.Password
                    placeholder="请输入硅基流动API密钥"
                    prefix={<KeyOutlined />}
                    className="settings-input"
                  />
                </Form.Item>

                <Form.Item
                  label="硅基流动模型"
                  name="siliconflow_model"
                  className="form-item"
                  rules={[{ required: true, message: '请选择模型' }]}
                >
                  <Select placeholder="请选择模型" className="settings-input">
                    <Select.Option value="Qwen/Qwen2.5-72B-Instruct">Qwen2.5-72B-Instruct</Select.Option>
                    <Select.Option value="Qwen/Qwen3-8B">Qwen3-8B</Select.Option>
                    <Select.Option value="Pro/deepseek-ai/DeepSeek-R1">DeepSeek-R1</Select.Option>
                  </Select>
                </Form.Item>
              </>
            )}

            {/* 操作按钮 */}
            <Form.Item>
              <Space>
                <Button 
                  type="primary" 
                  icon={<SaveOutlined />} 
                  htmlType="submit" 
                  loading={loading}
                >
                  保存配置
                </Button>
                <Button 
                  type="default" 
                  onClick={handleTestApi}
                  loading={loading}
                >
                  测试API连接
                </Button>
              </Space>
            </Form.Item>

            <Divider className="settings-divider" />

            <Title level={4} className="section-title">处理参数配置</Title>
            
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item
                  label="文本分块大小"
                  name="chunk_size"
                  className="form-item"
                  rules={[{ required: true, message: '请输入分块大小' }]}
                >
                  <Input 
                    type="number" 
                    placeholder="5000" 
                    addonAfter="字符" 
                    className="settings-input"
                  />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item
                  label="最低评分阈值"
                  name="min_score_threshold"
                  className="form-item"
                  rules={[{ required: true, message: '请输入评分阈值' }]}
                >
                  <Input 
                    type="number" 
                    step="0.1" 
                    min="0" 
                    max="1" 
                    placeholder="0.7" 
                    className="settings-input"
                  />
                </Form.Item>
              </Col>
            </Row>

            <Row gutter={16}>
              <Col span={12}>
                <Form.Item
                  label="每个合集最大切片数"
                  name="max_clips_per_collection"
                  className="form-item"
                  rules={[{ required: true, message: '请输入最大切片数' }]}
                >
                  <Input 
                    type="number" 
                    placeholder="5" 
                    addonAfter="个" 
                    className="settings-input"
                  />
                </Form.Item>
              </Col>
            </Row>

            <Divider className="settings-divider" />

            <Title level={4} className="section-title">浏览器配置</Title>
            
            <Alert
              message="B站链接导入设置"
              description="配置默认浏览器用于获取B站登录状态，下载AI字幕。如不配置将只能下载公开字幕。"
              type="info"
              showIcon
              style={{
                background: 'rgba(79, 172, 254, 0.1)',
                border: '1px solid rgba(79, 172, 254, 0.3)',
                borderRadius: '8px',
                marginBottom: '16px'
              }}
            />

            {detectingBrowsers ? (
              <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '12px',
                background: 'rgba(79, 172, 254, 0.1)',
                borderRadius: '8px',
                border: '1px solid rgba(79, 172, 254, 0.3)',
                marginBottom: '16px'
              }}>
                <Spin size="small" />
                <Text style={{ color: '#4facfe', fontSize: '14px' }}>
                  正在检测可用浏览器...
                </Text>
              </div>
            ) : (
              <div style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: '8px',
                marginBottom: '16px'
              }}>
                {availableBrowsers.map(browser => {
                  const isSelected = selectedBrowser === browser.value
                  return (
                    <div
                      key={browser.value}
                      onClick={() => {
                        if (!browser.available) return
                        setSelectedBrowser(browser.value)
                        form.setFieldValue('default_browser', browser.value)
                      }}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px',
                        padding: '8px 12px',
                        borderRadius: '6px',
                        border: isSelected 
                          ? '2px solid #4facfe' 
                          : '2px solid rgba(255, 255, 255, 0.1)',
                        background: isSelected 
                          ? 'rgba(79, 172, 254, 0.2)' 
                          : 'rgba(255, 255, 255, 0.05)',
                        color: isSelected ? '#ffffff' : 'rgba(255, 255, 255, 0.8)',
                        cursor: browser.available ? 'pointer' : 'not-allowed',
                        transition: 'all 0.2s ease',
                        fontSize: '13px',
                        fontWeight: isSelected ? 600 : 400,
                        userSelect: 'none',
                        opacity: browser.available ? 1 : 0.5
                      }}
                      onMouseEnter={(e) => {
                        if (!isSelected && browser.available) {
                          e.currentTarget.style.background = 'rgba(255, 255, 255, 0.1)'
                          e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.2)'
                        }
                      }}
                      onMouseLeave={(e) => {
                        if (!isSelected) {
                          e.currentTarget.style.background = 'rgba(255, 255, 255, 0.05)'
                          e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.1)'
                        }
                      }}
                    >
                      {browser.name}
                      {!browser.available && <span style={{ fontSize: '10px', opacity: 0.6 }}> (未安装)</span>}
                    </div>
                  )
                })}
              </div>
            )}

            <Form.Item className="form-item">
              <Button
                type="primary"
                htmlType="submit"
                icon={<SaveOutlined />}
                loading={loading}
                size="large"
                className="save-button"
                onClick={() => {
                  // 保存时同步selectedBrowser和form
                  form.setFieldValue('default_browser', selectedBrowser)
                }}
              >
                保存配置
              </Button>
            </Form.Item>
          </Form>
        </Card>

        <Card title="使用说明" className="settings-card">
          <Space direction="vertical" size="large" className="instructions-space">
            <div className="instruction-item">
              <Title level={5} className="instruction-title">
                <InfoCircleOutlined /> 1. 获取API密钥
              </Title>
              <Paragraph className="instruction-text">
                <strong>通义千问：</strong>访问阿里云控制台 → 人工智能 → 通义千问 → API密钥管理，创建新的API密钥<br />
                <strong>硅基流动：</strong>访问 <a href="https://siliconflow.cn" target="_blank" rel="noopener noreferrer">SiliconCloud官网</a> → 登录 → API密钥页面 → 新建API密钥
              </Paragraph>
            </div>
            
            <div className="instruction-item">
              <Title level={5} className="instruction-title">
                <InfoCircleOutlined /> 2. 配置参数说明
              </Title>
              <Paragraph className="instruction-text">
                • <Text strong>文本分块大小</Text>：影响处理速度和精度，建议5000字符<br />
                • <Text strong>评分阈值</Text>：只有高于此分数的片段才会被保留<br />
                • <Text strong>合集切片数</Text>：控制每个主题合集包含的片段数量
              </Paragraph>
            </div>
            
          </Space>
        </Card>
      </div>
    </Content>
  )
}

export default SettingsPage