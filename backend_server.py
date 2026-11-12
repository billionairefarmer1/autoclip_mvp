#!/usr/bin/env python3
"""
FastAPI后端服务器 - 自动切片工具API服务
提供RESTful API接口，支持前端React应用的所有功能需求
"""

import os
import json
import uuid
import shutil
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

# 导入项目模块
import sys
sys.path.append(str(Path(__file__).parent))

from src.main import AutoClipsProcessor
from src.config import OUTPUT_DIR, CLIPS_DIR, COLLECTIONS_DIR, METADATA_DIR, DASHSCOPE_API_KEY, VideoCategory, VIDEO_CATEGORIES_CONFIG
# from src.upload.upload_manager import UploadManager, Platform, UploadStatus  # 已移除bilitool相关功能
from src.utils.bilibili_downloader import BilibiliDownloader, BilibiliVideoInfo, download_bilibili_video, get_bilibili_video_info

# 配置日志
logger = logging.getLogger(__name__)

# 数据模型
class ProjectStatus(BaseModel):
    status: str  # 'uploading', 'processing', 'completed', 'error'
    current_step: Optional[int] = None
    total_steps: Optional[int] = 6
    step_name: Optional[str] = None
    progress: Optional[float] = 0.0
    error_message: Optional[str] = None

class Clip(BaseModel):
    id: str
    title: Optional[str] = None
    start_time: str
    end_time: str
    final_score: float
    recommend_reason: str
    generated_title: Optional[str] = None
    outline: str
    content: List[str]
    chunk_index: Optional[int] = None

class Collection(BaseModel):
    id: str
    collection_title: str
    collection_summary: str
    clip_ids: List[str]
    collection_type: str = "ai_recommended"  # "ai_recommended" or "manual"
    created_at: Optional[str] = None

class Project(BaseModel):
    id: str
    name: str
    video_path: str
    status: str
    created_at: str
    updated_at: str
    video_category: str = "default"  # 新增视频分类字段
    clips: List[Clip] = []
    collections: List[Collection] = []
    current_step: Optional[int] = None
    total_steps: Optional[int] = 6
    error_message: Optional[str] = None

class ClipUpdate(BaseModel):
    title: Optional[str] = None
    recommend_reason: Optional[str] = None
    generated_title: Optional[str] = None

class CollectionUpdate(BaseModel):
    collection_title: Optional[str] = None
    collection_summary: Optional[str] = None
    clip_ids: Optional[List[str]] = None

class ApiSettings(BaseModel):
    dashscope_api_key: str = ""
    siliconflow_api_key: str = ""
    api_provider: str = "dashscope"
    model_name: str = "qwen-plus"
    siliconflow_model: str = "Qwen/Qwen2.5-72B-Instruct"
    chunk_size: int = 5000
    min_score_threshold: float = 0.7
    max_clips_per_collection: int = 5
    default_browser: Optional[str] = None

# 以下上传相关模型已移除bilitool相关功能
# class UploadRequest(BaseModel):
# class BilibiliCredential(BaseModel):
# class UploadTaskResponse(BaseModel):

class BilibiliVideoInfoModel(BaseModel):
    bvid: str
    title: str
    duration: float
    uploader: str
    description: str
    thumbnail_url: str
    view_count: int
    upload_date: str
    webpage_url: str

class BilibiliDownloadRequest(BaseModel):
    url: str
    project_name: Optional[str] = None
    video_category: str = "default"
    browser: Optional[str] = None

class BilibiliDownloadTask(BaseModel):
    task_id: str
    url: str
    status: str  # 'pending', 'downloading', 'processing', 'completed', 'error'
    progress: float
    status_message: str
    video_info: Optional[BilibiliVideoInfoModel] = None
    video_path: Optional[str] = None
    subtitle_path: Optional[str] = None
    project_id: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str

# 全局状态管理
class ProjectManager:
    def __init__(self):
        self.projects: Dict[str, Project] = {}
        self.processing_status: Dict[str, ProjectStatus] = {}
        self.data_dir = Path("./data")
        self.data_dir.mkdir(exist_ok=True)
        self.processing_lock = asyncio.Lock()  # 防止并发处理
        self.max_concurrent_processing = 1  # 最大并发处理数
        self.current_processing_count = 0
        # self.upload_manager = UploadManager()  # 已移除bilitool相关功能
        self.bilibili_tasks: Dict[str, BilibiliDownloadTask] = {}  # B站下载任务
        self.load_projects()
    
    def load_projects(self):
        """从磁盘加载项目数据"""
        projects_file = self.data_dir / "projects.json"
        if projects_file.exists():
            try:
                with open(projects_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 限制一次加载的项目数量，避免内存占用过大
                    if len(data) > 100:
                        logger.warning(f"项目数量过多({len(data)})，只加载最近的100个项目")
                        # 按更新时间排序，取最新的100个
                        data = sorted(data, key=lambda x: x.get('updated_at', ''), reverse=True)[:100]
                    
                    for project_data in data:
                        try:
                            project = Project(**project_data)
                            self.projects[project.id] = project
                        except Exception as e:
                            logger.error(f"加载项目 {project_data.get('id', 'unknown')} 失败: {e}")
                            continue
                            
                logger.info(f"成功加载 {len(self.projects)} 个项目")
            except Exception as e:
                logger.error(f"加载项目数据失败: {e}")
    
    def save_projects(self):
        """保存项目数据到磁盘"""
        projects_file = self.data_dir / "projects.json"
        try:
            with open(projects_file, 'w', encoding='utf-8') as f:
                projects_data = [project.dict() for project in self.projects.values()]
                json.dump(projects_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存项目数据失败: {e}")
    
    def create_project(self, name: str, video_path: str, project_id: str = None, video_category: str = "default") -> Project:
        """创建新项目"""
        if project_id is None:
            project_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        project = Project(
            id=project_id,
            name=name,
            video_path=video_path,
            status="uploading",
            created_at=now,
            updated_at=now,
            video_category=video_category
        )
        
        self.projects[project_id] = project
        self.save_projects()
        return project
    
    def get_project(self, project_id: str) -> Optional[Project]:
        """获取项目"""
        project = self.projects.get(project_id)
        if not project:
            return None
        
        # 动态加载最新的clips和collections数据
        try:
            project_dir = Path("./uploads") / project_id
            metadata_dir = project_dir / "output" / "metadata"
            
            # 加载clips数据
            clips_file = metadata_dir / "clips_metadata.json"
            if clips_file.exists():
                with open(clips_file, 'r', encoding='utf-8') as f:
                    clips_data = json.load(f)
                    project.clips = [Clip(**clip) for clip in clips_data]
            
            # 加载collections数据
            collections_file = metadata_dir / "collections_metadata.json"
            if collections_file.exists():
                with open(collections_file, 'r', encoding='utf-8') as f:
                    collections_data = json.load(f)
                    project.collections = [Collection(**collection) for collection in collections_data]
        except Exception as e:
            logger.error(f"加载项目 {project_id} 的最新数据失败: {e}")
        
        return project
    
    def update_project(self, project_id: str, **updates) -> Optional[Project]:
        """更新项目"""
        if project_id not in self.projects:
            return None
        
        project = self.projects[project_id]
        for key, value in updates.items():
            if hasattr(project, key):
                setattr(project, key, value)
        
        project.updated_at = datetime.now().isoformat()
        self.save_projects()
        return project
    
    def delete_project(self, project_id: str) -> bool:
        """删除项目"""
        if project_id not in self.projects:
            return False
        
        project = self.projects[project_id]
        
        # 删除项目文件夹（uploads目录下的项目文件夹）
        try:
            uploads_dir = Path("./uploads")
            project_dir = uploads_dir / project_id
            if project_dir.exists():
                shutil.rmtree(project_dir)
                logger.info(f"已删除项目目录: {project_dir}")
            else:
                logger.warning(f"项目目录不存在: {project_dir}")
        except Exception as e:
            logger.error(f"删除项目文件失败: {e}")
        
        # 删除项目记录
        del self.projects[project_id]
        if project_id in self.processing_status:
            del self.processing_status[project_id]
        
        self.save_projects()
        logger.info(f"项目已删除: {project_id}")
        return True
    
    def create_bilibili_download_task(self, url: str, project_name: Optional[str] = None, 
                                    video_category: str = "default", browser: Optional[str] = None) -> str:
        """创建B站下载任务"""
        task_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        task = BilibiliDownloadTask(
            task_id=task_id,
            url=url,
            status="pending",
            progress=0.0,
            status_message="等待开始下载",
            created_at=now,
            updated_at=now
        )
        
        self.bilibili_tasks[task_id] = task
        return task_id
    
    def get_bilibili_task(self, task_id: str) -> Optional[BilibiliDownloadTask]:
        """获取B站下载任务"""
        return self.bilibili_tasks.get(task_id)
    
    def update_bilibili_task(self, task_id: str, **updates) -> Optional[BilibiliDownloadTask]:
        """更新B站下载任务"""
        if task_id not in self.bilibili_tasks:
            return None
        
        task = self.bilibili_tasks[task_id]
        for key, value in updates.items():
            if hasattr(task, key):
                setattr(task, key, value)
        
        task.updated_at = datetime.now().isoformat()
        return task
    
    def list_bilibili_tasks(self) -> List[BilibiliDownloadTask]:
        """列出所有B站下载任务"""
        return list(self.bilibili_tasks.values())

# 初始化项目管理器
project_manager = ProjectManager()

# 处理状态存储
processing_status = {}

# 生命周期管理
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时
    print("🚀 FastAPI服务器启动")
    yield
    # 关闭时
    print("🛑 FastAPI服务器关闭")

# 创建FastAPI应用
app = FastAPI(
    title="自动切片工具 API",
    description="视频自动切片和智能推荐系统的后端API服务",
    version="1.0.0",
    lifespan=lifespan
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件服务（目录不存在时自动创建，避免首次启动失败）
for _dir in ("output", "uploads"):
    Path(_dir).mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory="output"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# 桌面剪辑工作台 API (Desktop clipping studio)
try:
    from src.studio.api import router as studio_router
    app.include_router(studio_router)
    print("✅ Studio API mounted at /api/studio")
except Exception as exc:  # pragma: no cover - keep the legacy app usable
    import traceback
    traceback.print_exc()
    print(f"⚠️  Studio API could not be mounted: {exc}")

# API路由

@app.get("/", include_in_schema=False)
async def root():
    """根路径：优先返回打包后的前端应用（桌面模式），否则返回 API 信息"""
    for index_path in ("./frontend/dist/index.html", "./frontend/index.html"):
        if os.path.exists(index_path):
            # 不缓存 HTML，避免升级前端后浏览器仍打开旧版本
            return FileResponse(
                index_path,
                headers={"Cache-Control": "no-store, must-revalidate"},
            )
    return {"message": "自动切片工具 API 服务", "version": "1.0.0", "studio": "/api/studio/health"}

@app.get("/api/video-categories")
async def get_video_categories():
    """获取视频分类配置"""
    categories = []
    for key, config in VIDEO_CATEGORIES_CONFIG.items():
        categories.append({
            "value": key,
            "name": config["name"],
            "description": config["description"],
            "icon": config["icon"],
            "color": config["color"]
        })
    
    return {
        "categories": categories,
        "default_category": VideoCategory.DEFAULT
    }

@app.get("/api/browsers/detect")
async def detect_available_browsers():
    """检测系统中可用的浏览器"""
    import subprocess
    import platform
    
    browsers = []
    
    # 检测Chrome
    try:
        if platform.system() == "Darwin":  # macOS
            # macOS上Chrome通常在Applications目录
            chrome_paths = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"
            ]
            available = any(Path(path).exists() for path in chrome_paths)
            browsers.append({"name": "Chrome", "value": "chrome", "available": available, "priority": 1})
        elif platform.system() == "Windows":
            # Windows Chrome 通常在固定位置
            chrome_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
            ]
            available = any(Path(path).exists() for path in chrome_paths)
            browsers.append({"name": "Chrome", "value": "chrome", "available": available, "priority": 1})
        else:  # Linux
            result = subprocess.run(["which", "google-chrome"], capture_output=True, text=True)
            if result.returncode == 0:
                browsers.append({"name": "Chrome", "value": "chrome", "available": True, "priority": 1})
            else:
                browsers.append({"name": "Chrome", "value": "chrome", "available": False, "priority": 1})
    except Exception:
        browsers.append({"name": "Chrome", "value": "chrome", "available": False, "priority": 1})
    
    # 检测Edge
    try:
        if platform.system() == "Darwin":  # macOS
            result = subprocess.run(["which", "microsoft-edge"], capture_output=True, text=True)
            if result.returncode == 0:
                browsers.append({"name": "Edge", "value": "edge", "available": True, "priority": 2})
            else:
                browsers.append({"name": "Edge", "value": "edge", "available": False, "priority": 2})
        elif platform.system() == "Windows":
            edge_paths = [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
            ]
            available = any(Path(path).exists() for path in edge_paths)
            browsers.append({"name": "Edge", "value": "edge", "available": available, "priority": 2})
        else:  # Linux
            result = subprocess.run(["which", "microsoft-edge"], capture_output=True, text=True)
            if result.returncode == 0:
                browsers.append({"name": "Edge", "value": "edge", "available": True, "priority": 2})
            else:
                browsers.append({"name": "Edge", "value": "edge", "available": False, "priority": 2})
    except Exception:
        browsers.append({"name": "Edge", "value": "edge", "available": False, "priority": 2})
    
    # 检测Firefox
    try:
        if platform.system() == "Darwin":  # macOS
            result = subprocess.run(["which", "firefox"], capture_output=True, text=True)
            if result.returncode == 0:
                browsers.append({"name": "Firefox", "value": "firefox", "available": True, "priority": 3})
            else:
                browsers.append({"name": "Firefox", "value": "firefox", "available": False, "priority": 3})
        elif platform.system() == "Windows":
            firefox_paths = [
                r"C:\Program Files\Mozilla Firefox\firefox.exe",
                r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe"
            ]
            available = any(Path(path).exists() for path in firefox_paths)
            browsers.append({"name": "Firefox", "value": "firefox", "available": available, "priority": 3})
        else:  # Linux
            result = subprocess.run(["which", "firefox"], capture_output=True, text=True)
            if result.returncode == 0:
                browsers.append({"name": "Firefox", "value": "firefox", "available": True, "priority": 3})
            else:
                browsers.append({"name": "Firefox", "value": "firefox", "available": False, "priority": 3})
    except Exception:
        browsers.append({"name": "Firefox", "value": "firefox", "available": False, "priority": 3})
    
    # Safari (仅macOS)
    if platform.system() == "Darwin":
        browsers.append({"name": "Safari", "value": "safari", "available": True, "priority": 4})
    else:
        browsers.append({"name": "Safari", "value": "safari", "available": False, "priority": 4})
    
    # 按优先级排序
    browsers.sort(key=lambda x: x["priority"])
    
    return {"browsers": browsers}

# B站视频相关API
@app.post("/api/bilibili/parse")
async def parse_bilibili_video(url: str = Form(...), browser: Optional[str] = Form(None)):
    """解析B站视频信息"""
    try:
        # 验证URL格式
        downloader = BilibiliDownloader(browser=browser)
        if not downloader.validate_bilibili_url(url):
            raise HTTPException(status_code=400, detail="无效的B站视频链接")
        
        # 获取视频信息，传递browser参数
        video_info = await get_bilibili_video_info(url, browser)
        
        return {
            "success": True,
            "video_info": video_info.to_dict()
        }
    except Exception as e:
        logger.error(f"解析B站视频失败: {e}")
        raise HTTPException(status_code=400, detail=f"解析视频信息失败: {str(e)}")

@app.post("/api/bilibili/download")
async def create_bilibili_download_task(
    background_tasks: BackgroundTasks,
    request: BilibiliDownloadRequest
):
    """创建B站视频下载任务"""
    try:
        # 验证URL格式
        downloader = BilibiliDownloader()
        if not downloader.validate_bilibili_url(request.url):
            raise HTTPException(status_code=400, detail="无效的B站视频链接")
        
        # 创建下载任务
        task_id = project_manager.create_bilibili_download_task(
            url=request.url,
            project_name=request.project_name,
            video_category=request.video_category,
            browser=request.browser
        )
        
        # 启动后台下载任务
        background_tasks.add_task(
            process_bilibili_download_task,
            task_id,
            request.url,
            request.project_name,
            request.video_category,
            request.browser
        )
        
        return {
            "success": True,
            "task_id": task_id,
            "message": "下载任务已创建"
        }
    except Exception as e:
        logger.error(f"创建B站下载任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建下载任务失败: {str(e)}")

@app.get("/api/bilibili/tasks/{task_id}")
async def get_bilibili_download_task(task_id: str):
    """获取B站下载任务状态"""
    task = project_manager.get_bilibili_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return task

@app.get("/api/bilibili/tasks")
async def list_bilibili_download_tasks():
    """列出所有B站下载任务"""
    tasks = project_manager.list_bilibili_tasks()
    # 按创建时间倒序排列
    tasks.sort(key=lambda x: x.created_at, reverse=True)
    return {"tasks": tasks}

@app.get("/api/projects", response_model=List[Project])
async def get_projects():
    """获取所有项目"""
    try:
        # 使用异步方式获取项目列表，避免阻塞
        projects = await asyncio.get_event_loop().run_in_executor(
            None, lambda: list(project_manager.projects.values())
        )
        return projects
    except Exception as e:
        logger.error(f"get_projects failed: {e}")
        return []

@app.get("/api/projects/{project_id}", response_model=Project)
async def get_project(project_id: str):
    """获取单个项目详情"""
    try:
        project = project_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        return project
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_project failed for {project_id}: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")

@app.put("/api/projects/{project_id}/category")
async def update_project_category(project_id: str, video_category: str = Form(...)):
    """更新项目的视频分类"""
    try:
        # 验证分类是否有效
        if video_category not in [category.value for category in VideoCategory]:
            raise HTTPException(status_code=400, detail="无效的视频分类")
        
        project = project_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        
        # 更新项目分类
        project.video_category = video_category
        project.updated_at = datetime.now().isoformat()
        
        # 保存项目
        project_manager.save_projects()
        
        return {"message": "项目分类更新成功", "video_category": video_category}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update_project_category failed for {project_id}: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")

@app.post("/api/upload")
async def upload_files(
    background_tasks: BackgroundTasks,
    video_file: UploadFile = File(...),
    srt_file: Optional[UploadFile] = File(None),
    project_name: str = Form(...),
    video_category: str = Form("default")
):
    """上传文件并创建项目"""
    # 验证文件类型
    if not video_file.filename or not video_file.filename.lower().endswith(('.mp4', '.avi', '.mov', '.mkv')):
        raise HTTPException(status_code=400, detail="不支持的视频格式")
    
    # 创建项目ID
    project_id = str(uuid.uuid4())
    project_dir = Path("./uploads") / project_id
    input_dir = project_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    
    # 保存视频文件到input子目录
    video_extension = video_file.filename.split('.')[-1]
    video_path = input_dir / f"input.{video_extension}"
    with open(video_path, "wb") as f:
        content = await video_file.read()
        f.write(content)
    
    # 保存字幕文件到input子目录
    if srt_file:
        srt_path = input_dir / "input.srt"
        with open(srt_path, "wb") as f:
            content = await srt_file.read()
            f.write(content)
    
    # 创建项目记录（video_path相对于项目根目录）
    relative_video_path = f"uploads/{project_id}/input/input.{video_extension}"
    project = project_manager.create_project(project_name, relative_video_path, project_id, video_category)
    
    return project

async def process_project_background(project_id: str, start_step: int = 1):
    """后台处理项目"""
    try:
        # 更新状态为处理中
        project_manager.update_project(project_id, status="processing")
        processing_status[project_id] = {
            "status": "processing",
            "current_step": start_step,
            "total_steps": 6,
            "step_name": f"从步骤{start_step}开始处理",
            "progress": ((start_step - 1) / 6) * 100
        }
        
        # 获取项目信息
        project = project_manager.get_project(project_id)
        if not project:
            return
        
        # 定义进度回调函数
        def update_progress(current_step, total_steps, step_name, progress):
            processing_status[project_id].update({
                "status": "processing",
                "current_step": current_step,
                "total_steps": total_steps,
                "step_name": step_name,
                "progress": progress
            })
        
        # 创建处理器并运行
        processor = AutoClipsProcessor(project_id)
        
        # 根据起始步骤选择处理方式
        try:
            if start_step == 1:
                # 从头开始运行完整流水线
                result = processor.run_full_pipeline(update_progress)
            else:
                # 从指定步骤开始运行
                result = processor.run_from_step(start_step, update_progress)
        except Exception as e:
            logger.error(f"处理器运行失败: {str(e)}")
            result = {'success': False, 'error': str(e)}
        
        if result.get('success'):
            # 读取final_results.json并提取clips和collections数据
            try:
                final_results_path = Path(f"uploads/{project_id}/output/metadata/final_results.json")
                if final_results_path.exists():
                    with open(final_results_path, 'r', encoding='utf-8') as f:
                        final_results = json.load(f)
                    
                    # 提取clips数据
                    clips = final_results.get('step3_scoring', [])
                    collections = final_results.get('step5_collections', [])
                    
                    # 修复clips数据：将generated_title映射为title字段
                    for clip in clips:
                        if 'generated_title' in clip and clip['generated_title']:
                            clip['title'] = clip['generated_title']
                        elif 'title' not in clip or clip['title'] is None:
                            # 如果没有generated_title，使用outline作为fallback
                            clip['title'] = clip.get('outline', f"片段 {clip.get('id', '')}")
                    
                    # 更新项目状态，包含clips和collections数据
                    project_manager.update_project(
                        project_id, 
                        status="completed",
                        clips=clips,
                        collections=collections
                    )
                else:
                    # 如果没有final_results.json，只更新状态
                    project_manager.update_project(project_id, status="completed")
            except Exception as e:
                logger.error(f"读取final_results.json失败: {e}")
                # 即使读取失败，也要更新项目状态
                project_manager.update_project(project_id, status="completed")
            
            processing_status[project_id].update({
                "status": "completed",
                "current_step": 6,
                "total_steps": 6,
                "step_name": "处理完成",
                "progress": 100.0
            })
        else:
            # 处理失败
            error_msg = result.get('error', '处理过程中发生未知错误')
            project_manager.update_project(project_id, status="error", error_message=error_msg)
            processing_status[project_id] = {
                "status": "error",
                "current_step": processing_status[project_id].get("current_step", 0),
                "total_steps": 6,
                "step_name": "处理失败",
                "progress": 0,
                "error_message": error_msg
            }
    
    except Exception as e:
        # 处理异常
        error_msg = f"处理失败: {str(e)}"
        project_manager.update_project(project_id, status="error", error_message=error_msg)
        processing_status[project_id] = {
            "status": "error",
            "current_step": processing_status[project_id].get("current_step", 0),
            "total_steps": 6,
            "step_name": "处理失败",
            "progress": 0,
            "error_message": error_msg
        }

async def process_project_background_with_lock(project_id: str, start_step: int = 1):
    """带资源锁的后台处理项目"""
    try:
        await process_project_background(project_id, start_step)
    finally:
        # 无论成功还是失败，都要释放处理锁
        async with project_manager.processing_lock:
            if project_manager.current_processing_count > 0:
                project_manager.current_processing_count -= 1
        logger.info(f"项目 {project_id} 处理完成，当前并发处理数: {project_manager.current_processing_count}")

async def process_bilibili_download_task(
    task_id: str, 
    url: str, 
    project_name: Optional[str] = None,
    video_category: str = "default",
    browser: Optional[str] = None
):
    """处理B站视频下载任务"""
    try:
        # 更新任务状态
        project_manager.update_bilibili_task(
            task_id,
            status="downloading",
            status_message="正在获取视频信息..."
        )
        
        # 获取视频信息
        video_info = await get_bilibili_video_info(url, browser)
        
        # 更新任务信息
        project_manager.update_bilibili_task(
            task_id,
            video_info=BilibiliVideoInfoModel(**video_info.to_dict()),
            status_message="开始下载视频和字幕..."
        )
        
        # 创建临时下载目录
        temp_download_dir = Path("./temp_downloads") / task_id
        temp_download_dir.mkdir(parents=True, exist_ok=True)
        
        # 定义进度回调函数
        def progress_callback(status_msg: str, progress: float):
            project_manager.update_bilibili_task(
                task_id,
                progress=progress,
                status_message=status_msg
            )
        
        # 下载视频和字幕
        downloader = BilibiliDownloader(temp_download_dir, browser)
        download_result = await downloader.download_video_and_subtitle(url, progress_callback)
        
        if not download_result['video_path']:
            raise Exception("视频下载失败")
        
        # 更新任务状态
        project_manager.update_bilibili_task(
            task_id,
            status="processing",
            status_message="正在创建项目...",
            video_path=download_result['video_path'],
            subtitle_path=download_result['subtitle_path'],
            progress=90
        )
        
        # 创建项目
        project_id = str(uuid.uuid4())
        project_dir = Path("./uploads") / project_id
        input_dir = project_dir / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        
        # 移动文件到项目目录
        video_src = Path(download_result['video_path'])
        video_dst = input_dir / "input.mp4"
        shutil.move(str(video_src), str(video_dst))
        
        subtitle_dst = None
        if download_result['subtitle_path']:
            subtitle_src = Path(download_result['subtitle_path'])
            subtitle_dst = input_dir / "input.srt"
            shutil.move(str(subtitle_src), str(subtitle_dst))
        
        # 清理临时目录
        try:
            shutil.rmtree(temp_download_dir)
        except Exception as e:
            logger.warning(f"清理临时目录失败: {e}")
        
        # 创建项目记录
        final_project_name = project_name or video_info.title
        relative_video_path = f"uploads/{project_id}/input/input.mp4"
        project = project_manager.create_project(
            final_project_name, 
            relative_video_path, 
            project_id, 
            video_category
        )
        
        # 更新任务状态为完成
        project_manager.update_bilibili_task(
            task_id,
            status="completed",
            status_message="项目创建完成",
            project_id=project_id,
            progress=100
        )
        
        logger.info(f"B站视频下载任务完成: {task_id}, 项目ID: {project_id}")
        
    except Exception as e:
        error_msg = f"下载失败: {str(e)}"
        logger.error(f"B站视频下载任务失败 {task_id}: {error_msg}")
        
        # 更新任务状态为失败
        project_manager.update_bilibili_task(
            task_id,
            status="error",
            status_message=error_msg,
            error=error_msg,
            progress=0
        )
        
        # 清理临时文件
        try:
            temp_download_dir = Path("./temp_downloads") / task_id
            if temp_download_dir.exists():
                shutil.rmtree(temp_download_dir)
        except Exception as cleanup_error:
            logger.warning(f"清理临时文件失败: {cleanup_error}")

@app.post("/api/projects/{project_id}/process")
async def start_processing(project_id: str, background_tasks: BackgroundTasks):
    """开始处理项目"""
    project = project_manager.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    if project.status != "uploading":
        raise HTTPException(status_code=400, detail="项目状态不允许处理")
    
    # 检查并发处理限制
    async with project_manager.processing_lock:
        if project_manager.current_processing_count >= project_manager.max_concurrent_processing:
            raise HTTPException(status_code=429, detail="系统正在处理其他项目，请稍后再试")
        
        project_manager.current_processing_count += 1
    
    # 添加后台任务
    background_tasks.add_task(process_project_background_with_lock, project_id)
    
    return {"message": "开始处理项目"}

@app.post("/api/projects/{project_id}/retry")
async def retry_project_processing(project_id: str, background_tasks: BackgroundTasks):
    """重试处理项目"""
    project = project_manager.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    if project.status != "error":
        raise HTTPException(status_code=400, detail="只有失败的项目才能重试")
    
    # 获取失败时的步骤，从该步骤开始重试
    failed_step = 1
    if project_id in processing_status:
        failed_step = processing_status[project_id].get("current_step", 1)
    elif hasattr(project, 'current_step') and project.current_step:
        failed_step = project.current_step
    
    # 清除之前的错误信息，但保持当前步骤信息
    project_manager.update_project(project_id, status="uploading", error_message=None)
    
    # 清除处理状态
    if project_id in processing_status:
        del processing_status[project_id]
    
    # 检查并发处理限制
    async with project_manager.processing_lock:
        if project_manager.current_processing_count >= project_manager.max_concurrent_processing:
            raise HTTPException(status_code=429, detail="系统正在处理其他项目，请稍后再试")
        
        project_manager.current_processing_count += 1
    
    # 添加后台任务从失败步骤开始重新处理
    background_tasks.add_task(process_project_background_with_lock, project_id, failed_step)
    
    return {"message": f"开始从步骤 {failed_step} 重试处理项目"}

@app.get("/api/system/status")
async def get_system_status():
    """获取系统状态"""
    return {
        "current_processing_count": project_manager.current_processing_count,
        "max_concurrent_processing": project_manager.max_concurrent_processing,
        "total_projects": len(project_manager.projects),
        "processing_projects": [
            project_id for project_id, status in processing_status.items() 
            if status.get("status") == "processing"
        ]
    }

@app.post("/api/projects/{project_id}/collections")
async def create_collection(project_id: str, collection_data: dict):
    """创建新合集"""
    try:
        project = project_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        
        # 验证请求数据
        if not collection_data.get("collection_title"):
            raise HTTPException(status_code=400, detail="合集标题不能为空")
        
        if not collection_data.get("clip_ids") or not isinstance(collection_data["clip_ids"], list):
            raise HTTPException(status_code=400, detail="必须选择至少一个片段")
        
        # 验证片段ID是否存在
        valid_clip_ids = [clip.id for clip in project.clips]
        for clip_id in collection_data["clip_ids"]:
            if clip_id not in valid_clip_ids:
                raise HTTPException(status_code=400, detail=f"片段ID {clip_id} 不存在")
        
        # 创建新合集
        collection_id = str(uuid.uuid4())
        new_collection = Collection(
            id=collection_id,
            collection_title=collection_data["collection_title"],
            collection_summary=collection_data.get("collection_summary", ""),
            clip_ids=collection_data["clip_ids"],
            collection_type="manual",
            created_at=datetime.now().isoformat()
        )
        
        # 添加到项目中
        project.collections.append(new_collection)
        
        # 保存项目
        project_manager.save_projects()
        
        # 更新项目的合集元数据文件
        try:
            metadata_dir = Path("./uploads") / project_id / "output" / "metadata"
            metadata_dir.mkdir(parents=True, exist_ok=True)
            
            collections_metadata_file = metadata_dir / "collections_metadata.json"
            collections_metadata = []
            
            # 如果文件已存在，读取现有数据
            if collections_metadata_file.exists():
                with open(collections_metadata_file, 'r', encoding='utf-8') as f:
                    collections_metadata = json.load(f)
            
            # 添加新合集到元数据
            collection_metadata = {
                "id": collection_id,
                "collection_title": new_collection.collection_title,
                "collection_summary": new_collection.collection_summary,
                "clip_ids": new_collection.clip_ids,
                "collection_type": new_collection.collection_type,
                "created_at": new_collection.created_at
            }
            collections_metadata.append(collection_metadata)
            
            # 保存更新后的元数据
            with open(collections_metadata_file, 'w', encoding='utf-8') as f:
                json.dump(collections_metadata, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            logger.warning(f"保存合集元数据失败: {e}")
        
        return new_collection
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建合集失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建合集失败: {str(e)}")

@app.delete("/api/projects/{project_id}/collections/{collection_id}")
async def delete_collection(project_id: str, collection_id: str):
    """删除合集"""
    try:
        project = project_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        
        # 查找指定的合集
        collection_index = None
        for i, coll in enumerate(project.collections):
            if coll.id == collection_id:
                collection_index = i
                break
        
        if collection_index is None:
            raise HTTPException(status_code=404, detail="合集不存在")
        
        # 从项目中删除合集
        deleted_collection = project.collections.pop(collection_index)
        
        # 保存项目
        project_manager.save_projects()
        
        # 删除合集元数据文件中的记录
        try:
            metadata_dir = Path("./uploads") / project_id / "output" / "metadata"
            collections_metadata_file = metadata_dir / "collections_metadata.json"
            
            if collections_metadata_file.exists():
                with open(collections_metadata_file, 'r', encoding='utf-8') as f:
                    collections_metadata = json.load(f)
                
                # 过滤掉被删除的合集
                collections_metadata = [c for c in collections_metadata if c.get("id") != collection_id]
                
                # 保存更新后的元数据
                with open(collections_metadata_file, 'w', encoding='utf-8') as f:
                    json.dump(collections_metadata, f, ensure_ascii=False, indent=2)
                    
        except Exception as e:
            logger.warning(f"删除合集元数据失败: {e}")
        
        # 删除合集生成的视频文件（如果存在）
        try:
            collection_video_path = Path(f"./uploads/{project_id}/output/collections/{collection_id}.mp4")
            if collection_video_path.exists():
                collection_video_path.unlink()
                logger.info(f"已删除合集视频文件: {collection_video_path}")
        except Exception as e:
            logger.warning(f"删除合集视频文件失败: {e}")
        
        return {"message": "合集删除成功", "deleted_collection": deleted_collection.collection_title}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除合集失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除合集失败: {str(e)}")

@app.patch("/api/projects/{project_id}/collections/{collection_id}")
async def update_collection(project_id: str, collection_id: str, updates: dict):
    """更新合集信息"""
    try:
        project = project_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        
        # 查找指定的合集
        collection = None
        collection_index = None
        for i, coll in enumerate(project.collections):
            if coll.id == collection_id:
                collection = coll
                collection_index = i
                break
        
        if not collection:
            raise HTTPException(status_code=404, detail="合集不存在")
        
        # 验证更新数据
        if "clip_ids" in updates:
            if not isinstance(updates["clip_ids"], list):
                raise HTTPException(status_code=400, detail="clip_ids必须是数组")
            
            # 验证片段ID是否存在
            valid_clip_ids = [clip.id for clip in project.clips]
            for clip_id in updates["clip_ids"]:
                if clip_id not in valid_clip_ids:
                    raise HTTPException(status_code=400, detail=f"片段ID {clip_id} 不存在")
        
        # 更新合集信息
        if "collection_title" in updates:
            collection.collection_title = updates["collection_title"]
        if "collection_summary" in updates:
            collection.collection_summary = updates["collection_summary"]
        if "clip_ids" in updates:
            collection.clip_ids = updates["clip_ids"]
        
        # 保存项目
        project_manager.save_projects()
        
        # 更新合集元数据文件
        try:
            metadata_dir = Path("./uploads") / project_id / "output" / "metadata"
            metadata_dir.mkdir(parents=True, exist_ok=True)
            
            collections_metadata_file = metadata_dir / "collections_metadata.json"
            collections_metadata = []
            
            # 如果文件已存在，读取现有数据
            if collections_metadata_file.exists():
                with open(collections_metadata_file, 'r', encoding='utf-8') as f:
                    collections_metadata = json.load(f)
            
            # 更新对应的合集元数据
            updated = False
            for i, metadata in enumerate(collections_metadata):
                if metadata.get("id") == collection_id:
                    collections_metadata[i] = {
                        "id": collection.id,
                        "collection_title": collection.collection_title,
                        "collection_summary": collection.collection_summary,
                        "clip_ids": collection.clip_ids,
                        "collection_type": collection.collection_type,
                        "created_at": collection.created_at
                    }
                    updated = True
                    break
            
            # 如果没有找到对应的元数据，添加新的
            if not updated:
                collections_metadata.append({
                    "id": collection.id,
                    "collection_title": collection.collection_title,
                    "collection_summary": collection.collection_summary,
                    "clip_ids": collection.clip_ids,
                    "collection_type": collection.collection_type,
                    "created_at": collection.created_at
                })
            
            # 保存更新后的元数据
            with open(collections_metadata_file, 'w', encoding='utf-8') as f:
                json.dump(collections_metadata, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            logger.warning(f"更新合集元数据失败: {e}")
        
        return collection
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新合集失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新合集失败: {str(e)}")

@app.post("/api/projects/{project_id}/collections/{collection_id}/generate")
async def generate_collection_video(project_id: str, collection_id: str, background_tasks: BackgroundTasks):
    """生成合集视频"""
    project = project_manager.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 查找指定的合集
    collection = None
    for coll in project.collections:
        if coll.id == collection_id:
            collection = coll
            break
    
    if not collection:
        raise HTTPException(status_code=404, detail="合集不存在")
    
    # 添加后台任务生成合集视频
    background_tasks.add_task(generate_collection_video_background, project_id, collection_id)
    
    return {"message": "开始生成合集视频"}

async def generate_collection_video_background(project_id: str, collection_id: str):
    """后台生成合集视频"""
    try:
        from src.utils.video_processor import VideoProcessor
        import subprocess
        import shutil
        
        project = project_manager.get_project(project_id)
        if not project:
            return
        
        # 查找指定的合集
        collection = None
        for coll in project.collections:
            if coll.id == collection_id:
                collection = coll
                break
        
        if not collection:
            return
        
        # 获取合集中的所有切片视频路径，按照collection.clip_ids的顺序
        clips_dir = Path(f"./uploads/{project_id}/output/clips")
        collection_clips_dir = Path(f"./uploads/{project_id}/output/collections")
        collection_clips_dir.mkdir(exist_ok=True)
        
        clip_paths = []
        for clip_id in collection.clip_ids:
            # 查找对应的切片视频文件
            clip_files = list(clips_dir.glob(f"{clip_id}_*.mp4"))
            if clip_files:
                # 使用绝对路径
                clip_paths.append(str(clip_files[0].absolute()))
                logger.info(f"找到切片 {clip_id}: {clip_files[0].name}")
            else:
                logger.warning(f"未找到切片 {clip_id} 的视频文件")
        
        if not clip_paths:
            logger.error(f"合集 {collection_id} 中没有找到有效的切片视频")
            return
        
        # 生成合集视频文件路径，使用合集标题作为文件名
        safe_title = VideoProcessor.sanitize_filename(collection.collection_title)
        output_path = collection_clips_dir / f"{safe_title}.mp4"
        
        # 创建临时文件列表
        temp_list_file = collection_clips_dir / f"{collection_id}_list.txt"
        with open(temp_list_file, 'w', encoding='utf-8') as f:
            for clip_path in clip_paths:
                f.write(f"file '{clip_path}'\n")
        
        logger.info(f"开始生成合集视频，包含 {len(clip_paths)} 个切片")
        logger.info(f"切片顺序: {[Path(p).stem for p in clip_paths]}")
        
        # 使用ffmpeg合并视频
        cmd = [
            'ffmpeg',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(temp_list_file),
            '-c', 'copy',
            '-y',  # 覆盖输出文件
            str(output_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        # 清理临时文件
        if temp_list_file.exists():
            temp_list_file.unlink()
        
        if result.returncode == 0:
            logger.info(f"合集视频生成成功: {output_path}")
        else:
            logger.error(f"合集视频生成失败: {result.stderr}")
            
    except Exception as e:
        logger.error(f"生成合集视频时发生错误: {str(e)}")

@app.get("/api/projects/{project_id}/status")
async def get_processing_status(project_id: str):
    """获取处理状态"""
    project = project_manager.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 返回处理状态
    if project_id in processing_status:
        return processing_status[project_id]
    else:
        # 如果没有处理状态记录，根据项目状态返回默认状态
        if project.status == "completed":
            return {
                "status": "completed",
                "current_step": 6,
                "total_steps": 6,
                "step_name": "处理完成",
                "progress": 100.0
            }
        elif project.status == "error":
            return {
                "status": "error",
                "current_step": 0,
                "total_steps": 6,
                "step_name": "处理失败",
                "progress": 0,
                "error_message": project.error_message or "处理过程中发生错误"
            }
        else:
            return {
                "status": "processing",
                "current_step": 0,
                "total_steps": 6,
                "step_name": "准备处理",
                "progress": 0
            }

@app.get("/api/projects/{project_id}/logs")
async def get_project_logs(project_id: str, lines: int = 50):
    """获取项目处理日志"""
    try:
        project = project_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        
        log_file = "auto_clips.log"
        if not os.path.exists(log_file):
            return {"logs": []}
        
        # 读取日志文件的最后N行
        with open(log_file, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
        
        # 获取最后N行
        recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
        
        # 解析日志行
        logs = []
        for line in recent_lines:
            line = line.strip()
            if line:
                # 解析日志格式: 时间戳 - 模块 - 级别 - 消息
                parts = line.split(' - ', 3)
                if len(parts) >= 4:
                    timestamp = parts[0]
                    module = parts[1]
                    level = parts[2]
                    message = parts[3]
                    logs.append({
                        "timestamp": timestamp,
                        "module": module,
                        "level": level,
                        "message": message
                    })
                else:
                    # 如果格式不匹配，直接作为消息
                    logs.append({
                        "timestamp": "",
                        "module": "",
                        "level": "INFO",
                        "message": line
                    })
        
        return {"logs": logs}
    except Exception as e:
        logger.error(f"get_project_logs failed for {project_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects/{project_id}/download")
async def download_project_video(project_id: str, clip_id: str = None, collection_id: str = None):
    """下载项目视频文件"""
    project = project_manager.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    if clip_id:
        # 下载切片视频
        clip_files = list(CLIPS_DIR.glob(f"{clip_id}_*.mp4"))
        if not clip_files:
            raise HTTPException(status_code=404, detail="切片视频不存在")
        file_path = clip_files[0]
        filename = f"clip_{clip_id}.mp4"
    elif collection_id:
        # 下载合集视频 - 查找以合集标题命名的文件
        project = project_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        
        # 查找指定的合集
        collection = None
        for coll in project.collections:
            if coll.id == collection_id:
                collection = coll
                break
        
        if not collection:
            raise HTTPException(status_code=404, detail="合集不存在")
        
        # 使用项目特定的合集目录路径
        collection_clips_dir = Path(f"./uploads/{project_id}/output/collections")
        
        # 首先尝试使用合集标题查找文件
        from src.utils.video_processor import VideoProcessor
        safe_title = VideoProcessor.sanitize_filename(collection.collection_title)
        file_path = collection_clips_dir / f"{safe_title}.mp4"
        
        # 如果找不到，尝试使用collection_id
        if not file_path.exists():
            file_path = collection_clips_dir / f"{collection_id}.mp4"
        
        # 如果还是找不到，尝试查找任何以合集标题开头的文件
        if not file_path.exists():
            matching_files = list(collection_clips_dir.glob(f"*{collection.collection_title}*.mp4"))
            if matching_files:
                file_path = matching_files[0]
        
        # 如果还是找不到，尝试查找任何mp4文件
        if not file_path.exists():
            mp4_files = list(collection_clips_dir.glob("*.mp4"))
            if mp4_files:
                file_path = mp4_files[0]
        
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="合集视频文件不存在")
        
        # 使用实际存在的文件名作为下载文件名
        filename = file_path.name
    else:
        # 下载原始视频
        file_path = Path(project.video_path)
        filename = f"project_{project_id}.mp4"
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    
    # 关键：支持中文文件名下载
    filename_header = f"attachment; filename*=UTF-8''{quote(filename)}"
    
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type='application/octet-stream',
        headers={
            'Content-Disposition': filename_header
        }
    )

@app.get("/api/projects/{project_id}/download-all")
async def download_project_all(project_id: str):
    """打包下载项目的所有视频文件"""
    import zipfile
    import tempfile
    import shutil
    from pathlib import Path
    import os

    logger.info(f"开始处理打包下载请求: {project_id}")
    
    project = project_manager.get_project(project_id)
    if not project:
        logger.error(f"项目不存在: {project_id}")
        raise HTTPException(status_code=404, detail="项目不存在")
    
    if project.status != 'completed':
        logger.error(f"项目状态不是completed: {project.status}")
        raise HTTPException(status_code=400, detail="项目尚未完成处理，无法下载")
    
    logger.info(f"项目信息: {project.name}, 状态: {project.status}")
    
    try:
        # 创建临时目录
        logger.info("创建临时目录")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            zip_path = temp_path / f"{project.name}_完整项目.zip"
            
            logger.info(f"临时目录: {temp_dir}")
            logger.info(f"ZIP文件路径: {zip_path}")
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                project_dir = Path(f"./uploads/{project_id}")
                logger.info(f"项目目录: {project_dir}")
                logger.info(f"项目目录是否存在: {project_dir.exists()}")
                
                # 添加原始视频
                video_path = Path(project.video_path)
                logger.info(f"原始视频路径: {video_path}")
                logger.info(f"原始视频是否存在: {video_path.exists()}")
                if video_path.exists():
                    logger.info(f"添加原始视频: {video_path}")
                    zipf.write(video_path, f"原始视频/{video_path.name}")
                else:
                    logger.warning(f"原始视频不存在: {video_path}")
                
                # 添加切片视频
                clips_dir = project_dir / "output" / "clips"
                logger.info(f"切片目录: {clips_dir}")
                logger.info(f"切片目录是否存在: {clips_dir.exists()}")
                if clips_dir.exists():
                    clip_files = list(clips_dir.glob("*.mp4"))
                    logger.info(f"找到 {len(clip_files)} 个切片文件")
                    for clip_file in clip_files:
                        logger.info(f"处理切片文件: {clip_file}")
                        # 获取对应的切片信息
                        clip_id = clip_file.stem.split('_')[0]
                        clip_info = next((clip for clip in project.clips if clip.id == clip_id), None)
                        if clip_info:
                            # 使用切片标题作为文件名
                            title = clip_info.title or clip_info.generated_title or f"切片_{clip_id}"
                            safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
                            safe_title = safe_title[:50]  # 限制长度
                            zipf.write(clip_file, f"视频切片/{safe_title}.mp4")
                            logger.info(f"添加切片: {safe_title}")
                        else:
                            zipf.write(clip_file, f"视频切片/{clip_file.name}")
                            logger.info(f"添加切片: {clip_file.name}")
                else:
                    logger.warning(f"切片目录不存在: {clips_dir}")
                
                # 添加合集视频
                collections_dir = project_dir / "output" / "collections"
                logger.info(f"合集目录: {collections_dir}")
                logger.info(f"合集目录是否存在: {collections_dir.exists()}")
                if collections_dir.exists():
                    collection_files = list(collections_dir.glob("*.mp4"))
                    logger.info(f"找到 {len(collection_files)} 个合集文件")
                    for collection_file in collection_files:
                        logger.info(f"处理合集文件: {collection_file}")
                        # 获取对应的合集信息
                        collection_title = collection_file.stem
                        collection_info = next((coll for coll in project.collections if coll.collection_title == collection_title), None)
                        if collection_info:
                            safe_title = "".join(c for c in collection_info.collection_title if c.isalnum() or c in (' ', '-', '_')).rstrip()
                            safe_title = safe_title[:50]  # 限制长度
                            zipf.write(collection_file, f"合集视频/{safe_title}.mp4")
                            logger.info(f"添加合集: {safe_title}")
                        else:
                            zipf.write(collection_file, f"合集视频/{collection_file.name}")
                            logger.info(f"添加合集: {collection_file.name}")
                else:
                    logger.warning(f"合集目录不存在: {collections_dir}")
                
                # 添加项目信息文件
                project_info = {
                    "项目名称": project.name,
                    "创建时间": project.created_at,
                    "更新时间": project.updated_at,
                    "视频分类": project.video_category,
                    "切片数量": len(project.clips),
                    "合集数量": len(project.collections),
                    "切片列表": [
                        {
                            "ID": clip.id,
                            "标题": clip.title or clip.generated_title,
                            "开始时间": clip.start_time,
                            "结束时间": clip.end_time,
                            "评分": clip.final_score,
                            "推荐理由": clip.recommend_reason
                        } for clip in project.clips
                    ],
                    "合集列表": [
                        {
                            "ID": coll.id,
                            "标题": coll.collection_title,
                            "简介": coll.collection_summary,
                            "类型": coll.collection_type,
                            "包含切片": coll.clip_ids
                        } for coll in project.collections
                    ]
                }
                
                import json
                info_file = temp_path / "项目信息.json"
                with open(info_file, 'w', encoding='utf-8') as f:
                    json.dump(project_info, f, ensure_ascii=False, indent=2)
                zipf.write(info_file, "项目信息.json")
                logger.info("添加项目信息文件")
            
            # 复制到持久目录
            persist_dir = Path("./uploads/tmp")
            persist_dir.mkdir(parents=True, exist_ok=True)
            persist_zip_path = persist_dir / zip_path.name
            shutil.copy(zip_path, persist_zip_path)

        # 返回zip文件
        filename_header = f"attachment; filename*=UTF-8''{quote(persist_zip_path.name)}"
        logger.info(f"打包完成，文件大小: {persist_zip_path.stat().st_size} bytes")
        return FileResponse(
            path=persist_zip_path,
            filename=persist_zip_path.name,
            media_type='application/zip',
            headers={
                'Content-Disposition': filename_header
            }
        )
    except Exception as e:
        logger.error(f"打包下载项目 {project_id} 失败: {e}")
        import traceback
        logger.error(f"错误详情: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"打包下载失败: {str(e)}")

@app.get("/api/test-zip")
async def test_zip():
    """测试zip文件创建"""
    import zipfile
    import tempfile
    
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            zip_path = temp_path / "test.zip"
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # 创建一个测试文件
                test_file = temp_path / "test.txt"
                with open(test_file, 'w') as f:
                    f.write('test content')
                zipf.write(test_file, "test.txt")
            
            return FileResponse(
                path=zip_path,
                filename="test.zip",
                media_type='application/zip'
            )
    except Exception as e:
        logger.error(f"测试zip创建失败: {e}")
        raise HTTPException(status_code=500, detail=f"测试失败: {str(e)}")

@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    """删除项目"""
    success = project_manager.delete_project(project_id)
    if not success:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"message": "项目删除成功"}

@app.get("/api/projects/{project_id}/files/{file_path:path}")
async def get_project_file(project_id: str, file_path: str):
    """获取项目文件"""
    project = project_manager.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 构建文件路径
    full_file_path = Path("./uploads") / project_id / file_path
    
    if not full_file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    
    # 检查文件是否在项目目录内（安全检查）
    try:
        full_file_path.resolve().relative_to(Path("./uploads").resolve() / project_id)
    except ValueError:
        raise HTTPException(status_code=403, detail="访问被拒绝")
    
    return FileResponse(path=full_file_path)

@app.get("/api/projects/{project_id}/clips/{clip_id}")
async def get_clip_video(project_id: str, clip_id: str):
    """根据clipId获取切片视频文件"""
    project = project_manager.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 查找clips目录下以clip_id开头的mp4文件
    clips_dir = Path("./uploads") / project_id / "output" / "clips"
    if not clips_dir.exists():
        raise HTTPException(status_code=404, detail="切片目录不存在")
    
    # 查找匹配的文件
    matching_files = list(clips_dir.glob(f"{clip_id}_*.mp4"))
    if not matching_files:
        raise HTTPException(status_code=404, detail="切片视频文件不存在")
    
    # 返回第一个匹配的文件
    video_file = matching_files[0]
    return FileResponse(
        path=video_file, 
        media_type='video/mp4',
        headers={
            'Accept-Ranges': 'bytes',
            'Cache-Control': 'no-cache'
        }
    )

# 设置相关API
@app.get("/api/settings")
async def get_settings():
    """获取系统配置"""
    try:
        settings_file = Path("./data/settings.json")
        if settings_file.exists():
            with open(settings_file, 'r', encoding='utf-8') as f:
                settings = json.load(f)
        else:
            # 返回默认配置
            settings = {
                "dashscope_api_key": DASHSCOPE_API_KEY or "",
                "model_name": "qwen-plus",
                "chunk_size": 5000,
                "min_score_threshold": 0.7,
                "max_clips_per_collection": 5,
                "default_browser": "chrome"
            }
        return settings
    except Exception as e:
        logger.error(f"获取设置失败: {e}")
        raise HTTPException(status_code=500, detail="获取设置失败")

@app.post("/api/settings")
async def update_settings(settings: ApiSettings):
    """更新系统配置"""
    try:
        settings_file = Path("./data/settings.json")
        settings_file.parent.mkdir(exist_ok=True)
        
        with open(settings_file, 'w', encoding='utf-8') as f:
            json.dump(settings.dict(), f, ensure_ascii=False, indent=2)
        
        # 更新环境变量
        os.environ["DASHSCOPE_API_KEY"] = settings.dashscope_api_key
        os.environ["SILICONFLOW_API_KEY"] = settings.siliconflow_api_key
        os.environ["API_PROVIDER"] = settings.api_provider
        os.environ["SILICONFLOW_MODEL"] = settings.siliconflow_model
        
        return {"message": "配置更新成功"}
    except Exception as e:
        logger.error(f"更新设置失败: {e}")
        raise HTTPException(status_code=500, detail="更新设置失败")

@app.post("/api/settings/test-api-key")
async def test_api_key(request: dict):
    """测试API密钥"""
    try:
        api_key = request.get("api_key")
        provider = request.get("provider", "dashscope")
        model = request.get("model")
        
        logger.info(f"测试API密钥: provider={provider}, model={model}, api_key={'已设置' if api_key else '未设置'}")
        
        if not api_key:
            return {"success": False, "error": "API密钥不能为空"}
        
        # 创建临时LLM客户端测试连接
        try:
            from src.utils.llm_factory import LLMFactory
            success = LLMFactory.test_connection(provider=provider, api_key=api_key, model=model)
            if success:
                logger.info("API连接测试成功")
                return {"success": True}
            else:
                logger.error("API连接测试失败")
                return {"success": False, "error": "API连接测试失败"}
        except Exception as e:
            logger.error(f"API密钥测试失败: {e}")
            return {"success": False, "error": f"API密钥测试失败: {str(e)}"}
    except Exception as e:
        logger.error(f"测试API密钥失败: {e}")
        return {"success": False, "error": "测试过程中发生错误"}

# ==================== 上传相关API ====================

# 以下bilibili上传相关API端点已移除bilitool相关功能
# @app.post("/api/upload/bilibili/credential")
# @app.get("/api/upload/bilibili/verify")
# @app.get("/api/upload/bilibili/categories")

# 以下上传相关API端点已移除bilitool相关功能
# @app.post("/api/upload/create")
# @app.get("/api/upload/tasks/{task_id}")
# @app.get("/api/upload/tasks")
# @app.post("/api/upload/tasks/{task_id}/cancel")
# @app.post("/api/upload/clips/{clip_id}")

# 健康检查
@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

# 静态文件服务 - 提供前端构建文件
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

# 挂载静态文件目录
if os.path.exists("./frontend/dist"):
    app.mount("/static", StaticFiles(directory="./frontend/dist"), name="static")

# SPA路由兜底 - 处理前端路由
@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """SPA路由兜底，所有非API路径都返回前端页面"""
    # 如果是API路径，返回404
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API路径不存在")
    
    # 如果是静态资源路径，返回404
    if full_path.startswith(("static/", "uploads/")):
        raise HTTPException(status_code=404, detail="静态资源不存在")
    
    # 检查是否是静态资源请求
    if full_path.startswith("assets/") or full_path.endswith((".js", ".css", ".ico", ".png", ".jpg", ".jpeg", ".gif", ".svg")):
        static_file_path = f"./frontend/dist/{full_path}"
        if os.path.exists(static_file_path):
            return FileResponse(static_file_path)
        else:
            raise HTTPException(status_code=404, detail="静态资源不存在")
    
    # 其他所有路径都返回前端index.html（优先使用打包后的前端）
    for index_path in ("./frontend/dist/index.html", "./frontend/index.html"):
        if os.path.exists(index_path):
            return FileResponse(index_path)

    # 前端尚未构建：给出可执行的提示，而不是跳转到可能未启动的dev服务器
    return JSONResponse(
        status_code=503,
        content={
            "error": "Frontend is not built",
            "hint": "Run `npm install && npm run build` inside ./frontend, "
                    "or start the app with `python start.py`.",
        },
    )

if __name__ == "__main__":
    # 确保必要的目录存在
    Path("./uploads").mkdir(exist_ok=True)
    Path("./data").mkdir(exist_ok=True)
    Path("./output").mkdir(exist_ok=True)
    
    # 加载配置文件并设置环境变量
    try:
        settings_file = Path("./data/settings.json")
        if settings_file.exists():
            with open(settings_file, 'r', encoding='utf-8') as f:
                settings = json.load(f)
                if settings.get("dashscope_api_key"):
                    os.environ["DASHSCOPE_API_KEY"] = settings["dashscope_api_key"]
                    logger.info("已从配置文件加载 DASHSCOPE_API_KEY")
                if settings.get("siliconflow_api_key"):
                    os.environ["SILICONFLOW_API_KEY"] = settings["siliconflow_api_key"]
                    logger.info("已从配置文件加载 SILICONFLOW_API_KEY")
                if settings.get("api_provider"):
                    os.environ["API_PROVIDER"] = settings["api_provider"]
                    logger.info(f"已从配置文件加载 API_PROVIDER: {settings['api_provider']}")
                if settings.get("siliconflow_model"):
                    os.environ["SILICONFLOW_MODEL"] = settings["siliconflow_model"]
                    logger.info(f"已从配置文件加载 SILICONFLOW_MODEL: {settings['siliconflow_model']}")
                
                # 检查是否有有效的API密钥
                if not settings.get("dashscope_api_key") and not settings.get("siliconflow_api_key"):
                    logger.warning("配置文件中未找到有效的API密钥")
        else:
            logger.warning("配置文件不存在，请在前端设置 API 密钥")
    except Exception as e:
        logger.error(f"加载配置文件失败: {e}")
    
    # 启动服务器
    uvicorn.run(
        "backend_server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,  # Docker环境中禁用热重载
        log_level="info"
    )