"""
配置管理系统单元测试
"""
import pytest
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.config import ConfigManager, Settings, APIConfig, ProcessingConfig, PathConfig


class TestSettings:
    """测试Settings类"""
    
    def test_settings_initialization(self):
        """测试设置初始化"""
        with patch.dict(os.environ, {
            'DASHSCOPE_API_KEY': 'test_key',
            'MODEL_NAME': 'qwen-turbo',
            'CHUNK_SIZE': '3000',
            'MIN_SCORE_THRESHOLD': '0.8'
        }):
            settings = Settings()
            assert settings.dashscope_api_key == 'test_key'
            assert settings.model_name == 'qwen-turbo'
            assert settings.chunk_size == 3000
            assert settings.min_score_threshold == 0.8
    
    def test_settings_default_values(self):
        """测试默认值"""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()
            assert settings.model_name == 'qwen-plus'
            assert settings.chunk_size == 5000
            assert settings.min_score_threshold == 0.7
    
    def test_settings_validation(self):
        """测试配置验证"""
        with patch.dict(os.environ, {'MIN_SCORE_THRESHOLD': '1.5'}):
            with pytest.raises(ValueError, match='评分阈值必须在0-1之间'):
                Settings()
        
        with patch.dict(os.environ, {'CHUNK_SIZE': '-1'}):
            with pytest.raises(ValueError, match='分块大小必须大于0'):
                Settings()


class TestConfigManager:
    """测试ConfigManager类"""
    
    def setup_method(self):
        """每个测试方法前的设置"""
        self.temp_dir = tempfile.mkdtemp()
        self.config_manager = ConfigManager()
    
    def test_config_manager_initialization(self):
        """测试配置管理器初始化"""
        assert self.config_manager.settings is not None
        assert hasattr(self.config_manager, 'prompt_files')
    
    def test_get_api_config(self):
        """测试获取API配置"""
        api_config = self.config_manager.get_api_config()
        assert isinstance(api_config, APIConfig)
        assert api_config.model_name == 'qwen-plus'
    
    def test_get_processing_config(self):
        """测试获取处理配置"""
        processing_config = self.config_manager.get_processing_config()
        assert isinstance(processing_config, ProcessingConfig)
        assert processing_config.chunk_size == 5000
        assert processing_config.min_score_threshold == 0.7
    
    def test_get_path_config(self):
        """测试获取路径配置"""
        path_config = self.config_manager.get_path_config()
        assert isinstance(path_config, PathConfig)
        assert path_config.project_root.exists()
    
    def test_update_api_key(self, tmp_path, monkeypatch):
        """测试更新API密钥 —— 写入临时目录，避免污染仓库内的 data/settings.json"""
        monkeypatch.setattr('src.config.PROJECT_ROOT', tmp_path)
        (tmp_path / 'data').mkdir()
        test_key = 'new_test_key'
        self.config_manager.update_api_key(test_key)
        assert self.config_manager.settings.dashscope_api_key == test_key
        assert os.environ.get('DASHSCOPE_API_KEY') == test_key
        saved = json.loads((tmp_path / 'data' / 'settings.json').read_text(encoding='utf-8'))
        assert saved['dashscope_api_key'] == test_key
    
    def test_export_config(self):
        """测试导出配置"""
        config_data = self.config_manager.export_config()
        assert 'api_config' in config_data
        assert 'processing_config' in config_data
        assert 'paths' in config_data
        assert config_data['api_config']['model_name'] == 'qwen-plus'


class TestAPIConfig:
    """测试APIConfig类"""
    
    def test_api_config_defaults(self):
        """测试API配置默认值"""
        config = APIConfig()
        assert config.model_name == 'qwen-plus'
        assert config.api_key is None
        assert config.base_url == 'https://dashscope.aliyuncs.com'
        assert config.max_tokens == 4096
    
    def test_api_config_custom_values(self):
        """测试API配置自定义值"""
        config = APIConfig(
            model_name='qwen-turbo',
            api_key='test_key',
            max_tokens=2048
        )
        assert config.model_name == 'qwen-turbo'
        assert config.api_key == 'test_key'
        assert config.max_tokens == 2048


class TestProcessingConfig:
    """测试ProcessingConfig类"""
    
    def test_processing_config_defaults(self):
        """测试处理配置默认值"""
        config = ProcessingConfig()
        assert config.chunk_size == 5000
        assert config.min_score_threshold == 0.7
        assert config.max_clips_per_collection == 5
        assert config.max_retries == 3
        assert config.timeout_seconds == 30
    
    def test_processing_config_custom_values(self):
        """测试处理配置自定义值"""
        config = ProcessingConfig(
            chunk_size=3000,
            min_score_threshold=0.8,
            max_retries=5
        )
        assert config.chunk_size == 3000
        assert config.min_score_threshold == 0.8
        assert config.max_retries == 5


class TestPathConfig:
    """测试PathConfig类"""
    
    def test_path_config_defaults(self):
        """测试路径配置默认值"""
        config = PathConfig()
        # 自动创建目录
        os.makedirs(config.project_root, exist_ok=True)
        os.makedirs(config.data_dir, exist_ok=True)
        os.makedirs(config.uploads_dir, exist_ok=True)
        os.makedirs(config.prompt_dir, exist_ok=True)
        os.makedirs(config.output_dir, exist_ok=True)
        os.makedirs(config.temp_dir, exist_ok=True)
        assert config.project_root.exists()
        assert config.data_dir.exists()
        assert config.uploads_dir.exists()
        assert config.prompt_dir.exists()
        assert config.output_dir.exists()
        assert config.temp_dir.exists()
    
    def test_path_config_custom_values(self):
        """测试路径配置自定义值"""
        temp_dir = Path(tempfile.mkdtemp())
        config = PathConfig(
            project_root=temp_dir,
            data_dir=temp_dir / 'data',
            uploads_dir=temp_dir / 'uploads'
        )
        assert config.project_root == temp_dir
        assert config.data_dir == temp_dir / 'data'
        assert config.uploads_dir == temp_dir / 'uploads'


class TestLegacyConfig:
    """测试向后兼容配置"""
    
    def test_legacy_config_compatibility(self):
        """测试向后兼容配置"""
        from src.config import get_legacy_config
        legacy_config = get_legacy_config()
        # 检查必要的配置项
        required_keys = [
            'PROJECT_ROOT', 'INPUT_DIR', 'OUTPUT_DIR',
            'CLIPS_DIR', 'COLLECTIONS_DIR', 'METADATA_DIR',
            'PROMPT_DIR', 'PROMPT_FILES', 'DASHSCOPE_API_KEY',
            'MODEL_NAME', 'CHUNK_SIZE', 'MIN_SCORE_THRESHOLD',
            'MAX_CLIPS_PER_COLLECTION'
        ]
        for key in required_keys:
            assert key in legacy_config, f"缺少配置项: {key}"
        # 自动创建目录
        for key in ['PROJECT_ROOT', 'INPUT_DIR', 'OUTPUT_DIR', 'CLIPS_DIR', 'COLLECTIONS_DIR', 'METADATA_DIR', 'PROMPT_DIR']:
            os.makedirs(legacy_config[key], exist_ok=True)
        # 检查路径配置
        assert legacy_config['PROJECT_ROOT'].exists()
        assert legacy_config['INPUT_DIR'].exists()
        assert legacy_config['OUTPUT_DIR'].exists()
        # 检查提示词文件配置
        assert isinstance(legacy_config['PROMPT_FILES'], dict)
        assert 'outline' in legacy_config['PROMPT_FILES']
        assert 'timeline' in legacy_config['PROMPT_FILES']


if __name__ == '__main__':
    pytest.main([__file__]) 