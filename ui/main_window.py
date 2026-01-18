# -*- coding: utf-8 -*-
"""
SuperPicky - 主窗口
PySide6 版本 - 极简艺术风格
"""

import os
import sys
import threading
import subprocess
from pathlib import Path


def get_resource_path(relative_path):
    """获取资源文件路径（兼容 PyInstaller 打包环境）"""
    # PyInstaller 打包后会设置 _MEIPASS
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    # 开发环境
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), relative_path)

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QSlider, QProgressBar,
    QTextEdit, QGroupBox, QCheckBox, QMenuBar, QMenu,
    QFileDialog, QMessageBox, QSizePolicy, QFrame, QSpacerItem
)
from PySide6.QtCore import Qt, Signal, QObject, Slot, QTimer, QPropertyAnimation, QEasingCurve, QMimeData
from PySide6.QtGui import QFont, QPixmap, QIcon, QAction, QTextCursor, QColor, QDragEnterEvent, QDropEvent

from i18n import get_i18n
from advanced_config import get_advanced_config
from ui.styles import (
    GLOBAL_STYLE, TITLE_STYLE, SUBTITLE_STYLE, VERSION_STYLE, VALUE_STYLE,
    COLORS, FONTS, LOG_COLORS, PROGRESS_INFO_STYLE, PROGRESS_PERCENT_STYLE
)
from ui.custom_dialogs import StyledMessageBox


# V3.9: 支持拖放的目录输入框
class DropLineEdit(QLineEdit):
    """支持拖放目录的 QLineEdit"""
    pathDropped = Signal(str)  # 拖放目录后发射此信号
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
    
    def dragEnterEvent(self, event: QDragEnterEvent):
        """验证拖入的内容"""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].isLocalFile():
                path = urls[0].toLocalFile()
                if os.path.isdir(path):
                    event.acceptProposedAction()
                    return
        event.ignore()
    
    def dropEvent(self, event: QDropEvent):
        """处理拖放"""
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isdir(path):
                self.setText(path)
                self.pathDropped.emit(path)
                event.acceptProposedAction()
                return
        event.ignore()


class WorkerSignals(QObject):
    """工作线程信号"""
    progress = Signal(int)
    log = Signal(str, str)  # message, tag
    finished = Signal(dict)
    error = Signal(str)


class WorkerThread(threading.Thread):
    """处理线程"""

    def __init__(self, dir_path, ui_settings, signals, i18n=None):
        super().__init__(daemon=True)
        self.dir_path = dir_path
        self.ui_settings = ui_settings
        self.signals = signals
        self.i18n = i18n
        self._stop_event = threading.Event()
        self.caffeinate_process = None

        self.stats = {
            'total': 0,
            'star_3': 0,
            'picked': 0,
            'star_2': 0,
            'star_1': 0,
            'star_0': 0,
            'no_bird': 0,
            'start_time': 0,
            'end_time': 0,
            'total_time': 0,
            'avg_time': 0
        }

    def run(self):
        """执行处理"""
        try:
            self._start_caffeinate()
            self.process_files()
            self.signals.finished.emit(self.stats)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self._stop_caffeinate()

    def _start_caffeinate(self):
        """启动防休眠"""
        if sys.platform != 'darwin':
            return  # 目前仅在 macOS 上支持 caffeinate
            
        try:
            # V3.8.1: 先清理残留的 caffeinate 进程，避免累积
            try:
                subprocess.run(['killall', 'caffeinate'], 
                              stdout=subprocess.DEVNULL, 
                              stderr=subprocess.DEVNULL,
                              timeout=2)
            except Exception:
                pass  # 如果没有残留进程，忽略错误
            
            self.caffeinate_process = subprocess.Popen(
                ['caffeinate', '-d', '-i'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            if self.i18n:
                self.signals.log.emit(self.i18n.t("logs.caffeinate_started"), "info")
        except Exception as e:
            if self.i18n:
                self.signals.log.emit(self.i18n.t("logs.caffeinate_failed", error=str(e)), "warning")

    def _stop_caffeinate(self):
        """停止防休眠"""
        if self.caffeinate_process:
            try:
                self.caffeinate_process.terminate()
                self.caffeinate_process.wait(timeout=2)
            except Exception:
                try:
                    self.caffeinate_process.kill()
                except Exception:
                    pass
            finally:
                self.caffeinate_process = None

    def process_files(self):
        """处理文件"""
        from core.photo_processor import (
            PhotoProcessor,
            ProcessingSettings,
            ProcessingCallbacks
        )

        settings = ProcessingSettings(
            ai_confidence=self.ui_settings[0],
            sharpness_threshold=self.ui_settings[1],
            nima_threshold=self.ui_settings[2],
            save_crop=self.ui_settings[3] if len(self.ui_settings) > 3 else False,
            normalization_mode=self.ui_settings[4] if len(self.ui_settings) > 4 else 'log_compression',
            detect_flight=self.ui_settings[5] if len(self.ui_settings) > 5 else True,
            detect_exposure=self.ui_settings[6] if len(self.ui_settings) > 6 else False,  # V3.8: 默认关闭
            detect_burst=self.ui_settings[7] if len(self.ui_settings) > 7 else True  # V4.0: 默认开启
        )

        def log_callback(msg, level="info"):
            self.signals.log.emit(msg, level)

        def progress_callback(value):
            self.signals.progress.emit(int(value))

        callbacks = ProcessingCallbacks(
            log=log_callback,
            progress=progress_callback
        )

        processor = PhotoProcessor(
            dir_path=self.dir_path,
            settings=settings,
            callbacks=callbacks
        )

        result = processor.process(
            organize_files=True,
            cleanup_temp=True
        )

        # V4.0: 连拍检测（处理完成后执行）
        if settings.detect_burst:
            from core.burst_detector import BurstDetector
            from exiftool_manager import get_exiftool_manager
            
            log_callback("📷 正在执行连拍检测...", "info")
            
            detector = BurstDetector(use_phash=True)
            rating_dirs = ['3星_优选', '2星_良好']
            total_groups = 0
            total_moved = 0
            
            exiftool_mgr = get_exiftool_manager()
            
            for rating_dir in rating_dirs:
                import os
                subdir = os.path.join(self.dir_path, rating_dir)
                if not os.path.exists(subdir):
                    continue
                
                extensions = {'.nef', '.rw2', '.arw', '.cr2', '.cr3', '.orf', '.dng'}
                filepaths = []
                for entry in os.scandir(subdir):
                    if entry.is_file():
                        ext = os.path.splitext(entry.name)[1].lower()
                        if ext in extensions:
                            filepaths.append(entry.path)
                
                if not filepaths:
                    continue
                
                photos = detector.read_timestamps(filepaths)
                csv_path = os.path.join(self.dir_path, '.superpicky', 'report.csv')
                photos = detector.enrich_from_csv(photos, csv_path)
                groups = detector.detect_groups(photos)
                groups = detector.select_best_in_groups(groups)
                
                burst_stats = detector.process_burst_groups(groups, subdir, exiftool_mgr, log_callback=log_callback)
                total_groups += burst_stats['groups_processed']
                total_moved += burst_stats['photos_moved']
            
            if total_groups > 0:
                log_callback(f"✅ 连拍检测完成: {total_groups} 组, 移动 {total_moved} 张照片", "success")
            else:
                log_callback("ℹ️ 未检测到连拍组", "info")

        self.stats = result.stats


class SuperPickyMainWindow(QMainWindow):
    """SuperPicky 主窗口 - 极简艺术风格"""

    # V3.6: 重置操作的信号
    reset_log_signal = Signal(str)
    reset_complete_signal = Signal(bool, dict, dict)
    reset_error_signal = Signal(str)

    def __init__(self):
        super().__init__()

        # 初始化配置和国际化
        self.config = get_advanced_config()
        self.i18n = get_i18n(self.config.language)

        # 状态变量
        self.directory_path = ""
        self.worker = None
        self.worker_signals = None
        self.current_progress = 0
        self.total_files = 0

        # 设置窗口
        self._setup_window()
        self._setup_menu()
        self._setup_ui()
        self._show_initial_help()

        # 连接重置信号
        self.reset_log_signal.connect(self._log)
        self.reset_complete_signal.connect(self._on_reset_complete)
        self.reset_error_signal.connect(self._on_reset_error)

        # V3.9.5: 启动时检查更新（延迟2秒，避免阻塞UI）
        QTimer.singleShot(2000, self._check_for_updates)

    def _get_app_icon(self):
        """获取应用图标"""
        icon_path = os.path.join(os.path.dirname(__file__), "..", "img", "icon.png")
        if os.path.exists(icon_path):
            return QIcon(icon_path)
        return None

    def _show_message(self, title, message, msg_type="info"):
        """显示消息框"""
        if msg_type == "info":
            return StyledMessageBox.information(self, title, message)
        elif msg_type == "warning":
            return StyledMessageBox.warning(self, title, message)
        elif msg_type == "error":
            return StyledMessageBox.critical(self, title, message)
        elif msg_type == "question":
            return StyledMessageBox.question(self, title, message)
        else:
            return StyledMessageBox.information(self, title, message)

    def _setup_window(self):
        """设置窗口属性"""
        self.setWindowTitle(self.i18n.t("app.window_title"))
        self.setMinimumSize(720, 680)
        self.resize(820, 760)

        # 应用全局样式表
        self.setStyleSheet(GLOBAL_STYLE)

        # 设置图标
        icon_path = get_resource_path("img/icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

    def _setup_menu(self):
        """设置菜单栏"""
        menubar = self.menuBar()

        # 工具菜单
        tools_menu = menubar.addMenu("工具")

        # 鸟类识别 GUI
        birdid_gui_action = QAction("鸟类识别...", self)
        birdid_gui_action.triggered.connect(self._open_birdid_gui)
        tools_menu.addAction(birdid_gui_action)

        # 启动识鸟 API 服务
        self.birdid_server_action = QAction("启动识鸟 API 服务", self)
        self.birdid_server_action.triggered.connect(self._toggle_birdid_server)
        tools_menu.addAction(self.birdid_server_action)

        tools_menu.addSeparator()

        # 设置菜单
        settings_menu = menubar.addMenu(self.i18n.t("menu.settings"))
        advanced_action = QAction(self.i18n.t("menu.advanced_settings"), self)
        advanced_action.triggered.connect(self._show_advanced_settings)
        settings_menu.addAction(advanced_action)

        # 帮助菜单
        help_menu = menubar.addMenu(self.i18n.t("menu.help"))
        about_action = QAction(self.i18n.t("menu.about"), self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _setup_ui(self):
        """设置主 UI"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(0)

        # 头部区域
        self._create_header_section(main_layout)
        main_layout.addSpacing(24)

        # 目录选择
        self._create_directory_section(main_layout)
        main_layout.addSpacing(20)

        # 参数设置
        self._create_parameters_section(main_layout)
        main_layout.addSpacing(20)

        # 日志区域
        self._create_log_section(main_layout)
        main_layout.addSpacing(16)

        # 进度区域
        self._create_progress_section(main_layout)
        main_layout.addSpacing(20)

        # 控制按钮
        self._create_button_section(main_layout)

    def _create_header_section(self, parent_layout):
        """创建头部区域 - 品牌展示"""
        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)

        # 左侧: 品牌
        brand_layout = QHBoxLayout()
        brand_layout.setSpacing(16)

        # 品牌图标
        icon_path = get_resource_path("img/icon.png")
        if os.path.exists(icon_path):
            icon_container = QFrame()
            icon_container.setFixedSize(48, 48)
            icon_container.setStyleSheet(f"""
                QFrame {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 {COLORS['accent']}, stop:1 #00a080);
                    border-radius: 12px;
                }}
            """)
            icon_inner_layout = QHBoxLayout(icon_container)
            icon_inner_layout.setContentsMargins(2, 2, 2, 2)

            icon_label = QLabel()
            pixmap = QPixmap(icon_path).scaled(44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            icon_label.setPixmap(pixmap)
            icon_inner_layout.addWidget(icon_label)
            brand_layout.addWidget(icon_container)

        # 品牌文字
        brand_text_layout = QVBoxLayout()
        brand_text_layout.setSpacing(2)

        title_label = QLabel(self.i18n.t("app.brand_name"))
        title_label.setStyleSheet(TITLE_STYLE)
        brand_text_layout.addWidget(title_label)

        subtitle_label = QLabel(self.i18n.t("labels.subtitle"))
        subtitle_label.setStyleSheet(SUBTITLE_STYLE)
        brand_text_layout.addWidget(subtitle_label)

        brand_layout.addLayout(brand_text_layout)
        header_layout.addLayout(brand_layout)

        header_layout.addStretch()

        # 右侧: 版本号 + commit hash
        version_text = "V3.9.5"
        try:
            # V3.9.3: 优先从构建信息读取（发布版本）
            from core.build_info import COMMIT_HASH
            if COMMIT_HASH:
                version_text = f"V3.9.5\n{COMMIT_HASH}"
            else:
                # 回退到 git 命令（开发环境）
                import subprocess
                result = subprocess.run(
                    ['git', 'rev-parse', '--short', 'HEAD'],
                    capture_output=True, 
                    text=True, 
                    encoding='utf-8',
                    timeout=2,
                    cwd=os.path.dirname(os.path.dirname(__file__))
                )
                if result.returncode == 0:
                    commit_hash = result.stdout.strip()
                    version_text = f"V3.9.5\n{commit_hash}"
        except:
            pass  # 使用默认版本号
        version_label = QLabel(version_text)
        version_label.setStyleSheet(VERSION_STYLE)
        version_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        header_layout.addWidget(version_label)


        parent_layout.addWidget(header)

    def _create_directory_section(self, parent_layout):
        """创建目录选择区域"""
        # Section 标签
        section_label = QLabel(self.i18n.t("labels.photo_directory").upper())
        section_label.setObjectName("sectionLabel")
        parent_layout.addWidget(section_label)
        parent_layout.addSpacing(8)

        # 输入区域
        dir_layout = QHBoxLayout()
        dir_layout.setSpacing(8)

        # V3.9: 使用支持拖放的 DropLineEdit
        self.dir_input = DropLineEdit()
        self.dir_input.setPlaceholderText(self.i18n.t("labels.dir_placeholder"))
        self.dir_input.returnPressed.connect(self._on_path_entered)
        self.dir_input.editingFinished.connect(self._on_path_entered)  # V3.9: 失焦时也验证
        self.dir_input.pathDropped.connect(self._on_path_dropped)  # V3.9: 拖放目录
        dir_layout.addWidget(self.dir_input, 1)

        browse_btn = QPushButton(self.i18n.t("labels.browse"))
        browse_btn.setObjectName("browse")
        browse_btn.setMinimumWidth(100)
        browse_btn.clicked.connect(self._browse_directory)
        dir_layout.addWidget(browse_btn)

        parent_layout.addLayout(dir_layout)

    def _create_parameters_section(self, parent_layout):
        """创建参数设置区域"""
        # 参数卡片容器
        params_frame = QFrame()
        params_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_elevated']};
                border-radius: 10px;
            }}
        """)

        params_layout = QVBoxLayout(params_frame)
        params_layout.setContentsMargins(20, 16, 20, 16)
        params_layout.setSpacing(16)

        # 头部: 标题 + 飞鸟检测开关
        header_layout = QHBoxLayout()

        params_title = QLabel(self.i18n.t("labels.selection_params"))
        params_title.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 13px; font-weight: 500;")
        header_layout.addWidget(params_title)

        header_layout.addStretch()

        # 飞鸟检测开关
        flight_layout = QHBoxLayout()
        flight_layout.setSpacing(10)

        flight_label = QLabel(self.i18n.t("labels.flight_detection"))
        flight_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
        flight_layout.addWidget(flight_label)

        self.flight_check = QCheckBox()
        self.flight_check.setChecked(True)
        flight_layout.addWidget(self.flight_check)

        header_layout.addLayout(flight_layout)
        
        # V4.0: 连拍检测开关
        burst_layout = QHBoxLayout()
        burst_layout.setSpacing(10)
        
        burst_label = QLabel("连拍")
        burst_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
        burst_layout.addWidget(burst_label)
        
        self.burst_check = QCheckBox()
        self.burst_check.setChecked(True)  # 默认开启
        burst_layout.addWidget(self.burst_check)
        
        header_layout.addLayout(burst_layout)
        
        # V3.8: 曝光检测开关
        exposure_layout = QHBoxLayout()
        exposure_layout.setSpacing(10)
        
        exposure_label = QLabel("曝光")  # V4.0: 简化为"曝光"
        exposure_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
        exposure_layout.addWidget(exposure_label)
        
        self.exposure_check = QCheckBox()
        self.exposure_check.setChecked(True)  # V4.1: 默认开启（曝光检测实用）
        exposure_layout.addWidget(self.exposure_check)
        
        header_layout.addLayout(exposure_layout)
        
        params_layout.addLayout(header_layout)

        # 隐藏变量
        self.ai_confidence = 50
        self.norm_mode = "log_compression"

        # 滑块区域
        sliders_layout = QVBoxLayout()
        sliders_layout.setSpacing(16)

        # 锐度阈值
        sharp_layout = QHBoxLayout()
        sharp_layout.setSpacing(16)

        sharp_label = QLabel(self.i18n.t("labels.sharpness_short"))
        sharp_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 13px; min-width: 80px;")
        sharp_layout.addWidget(sharp_label)

        self.sharp_slider = QSlider(Qt.Horizontal)
        self.sharp_slider.setRange(200, 600)  # 新范围 200-600
        self.sharp_slider.setValue(400)  # 新默认值
        self.sharp_slider.setSingleStep(10)  # V4.0: 更精细的调节（键盘方向键）
        self.sharp_slider.setPageStep(10)    # V4.0: 点击滑块轨道的步进值
        self.sharp_slider.valueChanged.connect(self._on_sharp_changed)
        sharp_layout.addWidget(self.sharp_slider)

        self.sharp_value = QLabel("400")  # 新默认值
        self.sharp_value.setStyleSheet(VALUE_STYLE)
        self.sharp_value.setFixedWidth(50)
        self.sharp_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sharp_layout.addWidget(self.sharp_value)

        sliders_layout.addLayout(sharp_layout)

        # 美学阈值
        nima_layout = QHBoxLayout()
        nima_layout.setSpacing(16)

        nima_label = QLabel(self.i18n.t("labels.aesthetics"))
        nima_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 13px; min-width: 80px;")
        nima_layout.addWidget(nima_label)

        self.nima_slider = QSlider(Qt.Horizontal)
        self.nima_slider.setRange(40, 70)  # 新范围 4.0-7.0
        self.nima_slider.setValue(50)  # 默认值 5.0
        self.nima_slider.valueChanged.connect(self._on_nima_changed)
        nima_layout.addWidget(self.nima_slider)

        self.nima_value = QLabel("5.0")  # 默认值
        self.nima_value.setStyleSheet(VALUE_STYLE)
        self.nima_value.setFixedWidth(50)
        self.nima_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        nima_layout.addWidget(self.nima_value)

        sliders_layout.addLayout(nima_layout)

        params_layout.addLayout(sliders_layout)
        parent_layout.addWidget(params_frame)

    def _create_log_section(self, parent_layout):
        """创建日志区域"""
        # 日志头部
        log_header = QHBoxLayout()

        log_label = QLabel(self.i18n.t("labels.console").upper())
        log_label.setObjectName("sectionLabel")
        log_header.addWidget(log_label)

        log_header.addStretch()

        # 状态指示器
        status_layout = QHBoxLayout()
        status_layout.setSpacing(6)

        self.status_dot = QLabel()
        self.status_dot.setFixedSize(6, 6)
        self.status_dot.setStyleSheet(f"""
            QLabel {{
                background-color: {COLORS['accent']};
                border-radius: 3px;
            }}
        """)
        status_layout.addWidget(self.status_dot)

        self.status_label = QLabel(self.i18n.t("labels.ready"))
        self.status_label.setStyleSheet(f"color: {COLORS['text_tertiary']}; font-size: 11px;")
        status_layout.addWidget(self.status_label)

        log_header.addLayout(status_layout)
        parent_layout.addLayout(log_header)
        parent_layout.addSpacing(8)

        # 日志文本框
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(260)
        parent_layout.addWidget(self.log_text, 1)

    def _create_progress_section(self, parent_layout):
        """创建进度区域"""
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(3)
        parent_layout.addWidget(self.progress_bar)

        parent_layout.addSpacing(8)

        # 进度信息
        progress_info_layout = QHBoxLayout()

        self.progress_info_label = QLabel("")
        self.progress_info_label.setStyleSheet(PROGRESS_INFO_STYLE)
        progress_info_layout.addWidget(self.progress_info_label)

        progress_info_layout.addStretch()

        self.progress_percent_label = QLabel("")
        self.progress_percent_label.setStyleSheet(PROGRESS_PERCENT_STYLE)
        progress_info_layout.addWidget(self.progress_percent_label)

        parent_layout.addLayout(progress_info_layout)

    def _create_button_section(self, parent_layout):
        """创建按钮区域"""
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.setSpacing(8)

        # 重置按钮 (幽灵按钮)
        self.reset_btn = QPushButton(self.i18n.t("labels.reset_short"))
        self.reset_btn.setObjectName("tertiary")
        self.reset_btn.setMinimumWidth(100)
        self.reset_btn.setMinimumHeight(40)
        self.reset_btn.setEnabled(False)
        self.reset_btn.clicked.connect(self._reset_directory)
        btn_layout.addWidget(self.reset_btn)

        # V4.1: 重新评星按钮暂时禁用（计算逻辑复杂度高，预览结果不一致）
        # TODO: 未来版本重构后恢复此功能
        # self.post_da_btn = QPushButton(self.i18n.t("labels.re_rate"))
        # self.post_da_btn.setObjectName("secondary")
        # self.post_da_btn.setMinimumWidth(100)
        # self.post_da_btn.setMinimumHeight(40)
        # self.post_da_btn.setEnabled(False)
        # self.post_da_btn.clicked.connect(self._open_post_adjustment)
        # btn_layout.addWidget(self.post_da_btn)

        # 开始按钮 (主按钮)
        self.start_btn = QPushButton(self.i18n.t("labels.start_processing"))
        self.start_btn.setMinimumWidth(140)
        self.start_btn.setMinimumHeight(40)
        self.start_btn.clicked.connect(self._start_processing)
        btn_layout.addWidget(self.start_btn)

        parent_layout.addLayout(btn_layout)

    # ========== 槽函数 ==========

    @Slot()
    def _on_sharp_changed(self):
        """锐度滑块变化"""
        value = self.sharp_slider.value()
        rounded = round(value / 10) * 10  # V4.0: 改为 10 步进
        self.sharp_slider.blockSignals(True)
        self.sharp_slider.setValue(rounded)
        self.sharp_slider.blockSignals(False)
        self.sharp_value.setText(str(rounded))

    @Slot()
    def _on_nima_changed(self):
        """NIMA 滑块变化"""
        value = self.nima_slider.value() / 10.0
        self.nima_value.setText(f"{value:.1f}")

    @Slot()
    def _on_path_entered(self):
        """路径输入回车或失焦"""
        directory = self.dir_input.text().strip()
        if directory and os.path.isdir(directory):
            # V3.9: 防止重复处理（editingFinished 和 returnPressed 可能同时触发）
            normalized = os.path.normpath(directory)
            if normalized != os.path.normpath(self.directory_path or ""):
                self._handle_directory_selection(directory)
        elif directory:
            StyledMessageBox.critical(
                self,
                self.i18n.t("errors.error_title"),
                self.i18n.t("errors.dir_not_exist", directory=directory)
            )

    @Slot()
    def _browse_directory(self):
        """浏览目录"""
        directory = QFileDialog.getExistingDirectory(
            self,
            self.i18n.t("labels.select_photo_dir"),
            "",
            QFileDialog.ShowDirsOnly
        )
        if directory:
            self._handle_directory_selection(directory)
    
    @Slot(str)
    def _on_path_dropped(self, directory: str):
        """V3.9: 处理拖放的目录"""
        if directory and os.path.isdir(directory):
            self._handle_directory_selection(directory)

    def _handle_directory_selection(self, directory):
        """处理目录选择"""
        # V3.9: 归一化路径并防止重复
        directory = os.path.normpath(directory)
        if directory == os.path.normpath(self.directory_path or ""):
            return  # 同一个目录，跳过
        
        self.directory_path = directory
        self.dir_input.setText(directory)

        self._log(self.i18n.t("messages.dir_selected", directory=directory))

        self.start_btn.setEnabled(True)
        self.reset_btn.setEnabled(True)

        self._check_report_csv()

        # V4.1: 检测历史记录 - 只问是否重置（重新评星功能已禁用）
        history_csv = os.path.join(directory, ".superpicky", "report.csv")
        history_manifest = os.path.join(directory, ".superpicky_manifest.json")

        if os.path.exists(history_csv) or os.path.exists(history_manifest):
            reply = StyledMessageBox.question(
                self,
                self.i18n.t("messages.history_detected_title"),
                self.i18n.t("messages.history_reset_msg"),
                yes_text=self.i18n.t("labels.yes"),
                no_text=self.i18n.t("labels.no")
            )
            if reply == StyledMessageBox.Yes:
                QTimer.singleShot(100, self._reset_directory)

    def _check_report_csv(self):
        """检查是否有 report.csv"""
        if not self.directory_path:
            # self.post_da_btn.setEnabled(False)  # V4.1: 重新评星按钮已禁用
            return

        report_path = os.path.join(self.directory_path, ".superpicky", "report.csv")
        if os.path.exists(report_path):
            # self.post_da_btn.setEnabled(True)  # V4.1: 重新评星按钮已禁用
            self._log(self.i18n.t("messages.report_detected"))
        else:
            pass  # self.post_da_btn.setEnabled(False)  # V4.1: 重新评星按钮已禁用

    def _update_status(self, text, color=None):
        """更新状态指示器"""
        self.status_label.setText(text)
        if color:
            self.status_dot.setStyleSheet(f"""
                QLabel {{
                    background-color: {color};
                    border-radius: 3px;
                }}
            """)

    @Slot()
    def _start_processing(self):
        """开始处理"""
        if not self.directory_path:
            StyledMessageBox.warning(
                self,
                self.i18n.t("messages.hint"),
                self.i18n.t("messages.select_dir_first")
            )
            return

        if self.worker and self.worker.is_alive():
            StyledMessageBox.warning(
                self,
                self.i18n.t("messages.hint"),
                self.i18n.t("messages.processing")
            )
            return

        # 确认弹窗
        reply = StyledMessageBox.question(
            self,
            self.i18n.t("dialogs.file_organization_title"),
            self.i18n.t("dialogs.file_organization_msg"),
            yes_text=self.i18n.t("labels.yes"),
            no_text=self.i18n.t("labels.no")
        )

        if reply != StyledMessageBox.Yes:
            return

        # 清空日志和进度
        self.log_text.clear()
        self.progress_bar.setValue(0)
        self.progress_info_label.setText("")
        self.progress_percent_label.setText("")

        self._update_status(self.i18n.t("labels.processing"), COLORS['warning'])
        self._log(self.i18n.t("logs.processing_start"))

        # 准备 UI 设置
        ui_settings = [
            self.ai_confidence,
            self.sharp_slider.value(),
            self.nima_slider.value() / 10.0,
            False,
            self.norm_mode,
            self.flight_check.isChecked(),
            self.exposure_check.isChecked(),  # V3.8: 曝光检测开关
            self.burst_check.isChecked()      # V4.0: 连拍检测开关
        ]

        # 创建信号
        self.worker_signals = WorkerSignals()
        self.worker_signals.progress.connect(self._on_progress)
        self.worker_signals.log.connect(self._on_log)
        self.worker_signals.finished.connect(self._on_finished)
        self.worker_signals.error.connect(self._on_error)

        # 禁用按钮
        self.start_btn.setEnabled(False)
        self.reset_btn.setEnabled(False)

        # 启动工作线程
        self.worker = WorkerThread(
            self.directory_path,
            ui_settings,
            self.worker_signals,
            self.i18n
        )
        self.worker.start()

    @Slot(int)
    def _on_progress(self, value):
        """进度更新"""
        self.progress_bar.setValue(value)
        self.progress_percent_label.setText(f"{value}%")

    @Slot(str, str)
    def _on_log(self, message, tag):
        """日志更新"""
        self._log(message, tag)

    @Slot(dict)
    def _on_finished(self, stats):
        """处理完成"""
        self.start_btn.setEnabled(True)
        self.reset_btn.setEnabled(True)
        # self.post_da_btn.setEnabled(True)  # V4.1: 重新评星按钮已禁用
        self.progress_bar.setValue(100)
        self.progress_percent_label.setText("100%")
        self.progress_info_label.setText(self.i18n.t("labels.complete"))

        self._update_status(self.i18n.t("labels.complete"), COLORS['success'])

        # 显示报告（不清空之前的日志）
        report = self._format_statistics_report(stats)
        self._log(report)

        # 显示 Lightroom 指南
        self._show_lightroom_guide()

        # 播放完成音效
        self._play_completion_sound()

        # 打开目录
        if self.directory_path and os.path.exists(self.directory_path):
            if sys.platform == 'darwin':
                subprocess.Popen(['open', self.directory_path])
            elif sys.platform.startswith('win'):
                os.startfile(self.directory_path)
            else:
                try:
                    subprocess.Popen(['xdg-open', self.directory_path])
                except Exception:
                    pass

    @Slot(str)
    def _on_error(self, error_msg):
        """处理错误"""
        self._log(f"Error: {error_msg}", "error")
        self._update_status("Error", COLORS['error'])
        self.start_btn.setEnabled(True)
        self.reset_btn.setEnabled(True)

    @Slot()
    def _reset_directory(self):
        """重置目录"""
        if not self.directory_path:
            StyledMessageBox.warning(
                self,
                self.i18n.t("messages.hint"),
                self.i18n.t("messages.select_dir_first")
            )
            return

        reply = StyledMessageBox.question(
            self,
            self.i18n.t("messages.reset_confirm_title"),
            self.i18n.t("messages.reset_confirm"),
            yes_text=self.i18n.t("labels.yes"),
            no_text=self.i18n.t("labels.no")
        )

        if reply != StyledMessageBox.Yes:
            return

        self.log_text.clear()
        self.reset_btn.setEnabled(False)
        self.start_btn.setEnabled(False)

        self._update_status(self.i18n.t("labels.resetting"), COLORS['warning'])
        self._log(self.i18n.t("logs.reset_start"))

        directory_path = self.directory_path
        i18n = self.i18n
        log_signal = self.reset_log_signal
        complete_signal = self.reset_complete_signal
        error_signal = self.reset_error_signal

        def run_reset():
            restore_stats = {'restored': 0, 'failed': 0}
            exif_stats = {'success': 0, 'failed': 0}

            def emit_log(msg):
                log_signal.emit(msg)

            try:
                from exiftool_manager import get_exiftool_manager
                from find_bird_util import reset
                import shutil

                exiftool_mgr = get_exiftool_manager()
                
                # V3.9: 先清理 burst_XXX 子目录
                emit_log("步骤0: 清理连拍子目录...")
                rating_dirs = ['3星_优选', '2星_良好', '1星_普通', '0星_放弃']
                burst_stats = {'dirs_removed': 0, 'files_restored': 0}
                
                for rating_dir in rating_dirs:
                    rating_path = os.path.join(directory_path, rating_dir)
                    if not os.path.exists(rating_path):
                        continue
                    
                    for entry in os.listdir(rating_path):
                        if entry.startswith('burst_'):
                            burst_path = os.path.join(rating_path, entry)
                            if os.path.isdir(burst_path):
                                # 将文件移回评分目录
                                for filename in os.listdir(burst_path):
                                    src = os.path.join(burst_path, filename)
                                    dst = os.path.join(rating_path, filename)
                                    if os.path.isfile(src):
                                        try:
                                            if os.path.exists(dst):
                                                os.remove(dst)
                                            shutil.move(src, dst)
                                            burst_stats['files_restored'] += 1
                                        except Exception as e:
                                            emit_log(f"  ⚠️ 移动失败: {filename}: {e}")
                                
                                # 删除空的 burst 目录
                                try:
                                    if not os.listdir(burst_path):
                                        os.rmdir(burst_path)
                                    else:
                                        shutil.rmtree(burst_path)
                                    burst_stats['dirs_removed'] += 1
                                except Exception as e:
                                    emit_log(f"  ⚠️ 删除目录失败: {entry}: {e}")
                
                if burst_stats['dirs_removed'] > 0:
                    emit_log(f"  ✅ 已清理 {burst_stats['dirs_removed']} 个连拍目录，恢复 {burst_stats['files_restored']} 个文件")
                else:
                    emit_log("  ℹ️ 无连拍子目录需要清理")

                emit_log(i18n.t("logs.reset_step1"))
                restore_stats = exiftool_mgr.restore_files_from_manifest(
                    directory_path, log_callback=emit_log
                )

                restored_count = restore_stats.get('restored', 0)
                if restored_count > 0:
                    emit_log(i18n.t("logs.restored_files", count=restored_count))
                else:
                    emit_log(i18n.t("logs.no_files_to_restore"))

                emit_log("\n" + i18n.t("logs.reset_step2"))
                success = reset(directory_path, log_callback=emit_log, i18n=i18n)
                
                # V3.9: 删除空的评分目录
                emit_log("\n步骤3: 清理空目录...")
                deleted_dirs = 0
                for rating_dir in rating_dirs:
                    rating_path = os.path.join(directory_path, rating_dir)
                    if os.path.exists(rating_path) and os.path.isdir(rating_path):
                        # 检查是否为空（或只包含隐藏文件/目录）
                        contents = [f for f in os.listdir(rating_path) if not f.startswith('.')]
                        if len(contents) == 0:
                            try:
                                shutil.rmtree(rating_path)
                                emit_log(f"  🗑️ 已删除空目录: {rating_dir}")
                                deleted_dirs += 1
                            except Exception as e:
                                emit_log(f"  ⚠️ 删除目录失败: {rating_dir}: {e}")
                
                if deleted_dirs > 0:
                    emit_log(f"  ✅ 已清理 {deleted_dirs} 个空评分目录")
                else:
                    emit_log("  ℹ️ 无空目录需要清理")

                emit_log("\n" + i18n.t("logs.reset_complete"))
                complete_signal.emit(success, restore_stats, exif_stats)

            except Exception as e:
                import traceback
                error_msg = str(e)
                emit_log(f"\n{i18n.t('errors.error_title')}: {error_msg}")
                traceback.print_exc()
                error_signal.emit(error_msg)

        threading.Thread(target=run_reset, daemon=True).start()

    def _on_reset_complete(self, success, restore_stats=None, exif_stats=None):
        """重置完成"""
        if success:
            self._update_status(self.i18n.t("labels.ready"), COLORS['accent'])
            self._log(self.i18n.t("messages.reset_complete_log"))

            msg_parts = [self.i18n.t("messages.reset_complete_msg") + "\n"]

            if restore_stats:
                restored = restore_stats.get('restored', 0)
                if restored > 0:
                    msg_parts.append(self.i18n.t("messages.files_restored", count=restored))

            if exif_stats:
                exif_success = exif_stats.get('success', 0)
                if exif_success > 0:
                    msg_parts.append(self.i18n.t("messages.exif_reset_count", count=exif_success))

            msg_parts.append("\n" + self.i18n.t("messages.ready_for_analysis"))

            self._show_message(
                self.i18n.t("messages.reset_complete_title"),
                "\n".join(msg_parts),
                "info"
            )
        else:
            self._update_status(self.i18n.t("labels.error"), COLORS['error'])
            self._log(self.i18n.t("messages.reset_failed_log"))

        self.reset_btn.setEnabled(True)
        self.start_btn.setEnabled(True)
        self._check_report_csv()

    def _on_reset_error(self, error_msg):
        """重置错误"""
        self._log(f"Error: {error_msg}", "error")
        self._update_status("Error", COLORS['error'])
        self._show_message(
            self.i18n.t("errors.error_title"),
            error_msg,
            "error"
        )
        self.reset_btn.setEnabled(True)
        self.start_btn.setEnabled(True)

    @Slot()
    def _open_post_adjustment(self):
        """打开重新评星对话框"""
        if not self.directory_path:
            self._show_message(
                self.i18n.t("messages.hint"),
                self.i18n.t("messages.select_dir_first"),
                "warning"
            )
            return

        report_path = os.path.join(self.directory_path, ".superpicky", "report.csv")
        if not os.path.exists(report_path):
            StyledMessageBox.warning(
                self,
                self.i18n.t("messages.hint"),
                self.i18n.t("messages.no_report_csv")
            )
            return

        from .post_adjustment_dialog import PostAdjustmentDialog
        dialog = PostAdjustmentDialog(
            self,
            self.directory_path,
            current_sharpness=self.sharp_slider.value(),
            current_nima=self.nima_slider.value() / 10.0,
            on_complete_callback=self._on_post_adjustment_complete,
            log_callback=self._log
        )
        dialog.exec()

    def _on_post_adjustment_complete(self):
        """重新评星完成回调"""
        self._log(self.i18n.t("messages.post_adjust_complete"))

    @Slot()
    def _show_advanced_settings(self):
        """显示高级设置"""
        from .advanced_settings_dialog import AdvancedSettingsDialog
        dialog = AdvancedSettingsDialog(self)
        dialog.exec()

    @Slot()
    def _show_about(self):
        """显示关于对话框"""
        from .about_dialog import AboutDialog
        dialog = AboutDialog(self, self.i18n)
        dialog.exec()

    @Slot()
    def _open_birdid_gui(self):
        """打开鸟类识别 GUI"""
        try:
            from birdid_gui import BirdIDWindow
            self.birdid_window = BirdIDWindow()
            self.birdid_window.show()
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "错误", f"无法打开鸟类识别界面:\n{e}")

    @Slot()
    def _toggle_birdid_server(self):
        """启动/停止识鸟 API 服务"""
        import subprocess
        import sys as system_module

        if not hasattr(self, '_birdid_server_process') or self._birdid_server_process is None:
            # 启动服务
            try:
                script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'birdid_server.py')
                self._birdid_server_process = subprocess.Popen(
                    [system_module.executable, script_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                self.birdid_server_action.setText("停止识鸟 API 服务")
                self._log("识鸟 API 服务已启动 (端口 5156)", "success")
            except Exception as e:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "错误", f"无法启动识鸟 API 服务:\n{e}")
        else:
            # 停止服务
            try:
                self._birdid_server_process.terminate()
                self._birdid_server_process = None
                self.birdid_server_action.setText("启动识鸟 API 服务")
                self._log("识鸟 API 服务已停止", "info")
            except Exception as e:
                self._log(f"停止服务时出错: {e}", "error")

    # ========== 辅助方法 ==========

    def _log(self, message, tag=None):
        """输出日志"""
        from datetime import datetime

        print(message)

        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.End)

        # 根据标签选择颜色
        if tag == "error":
            color = LOG_COLORS['error']
        elif tag == "warning":
            color = LOG_COLORS['warning']
        elif tag == "success":
            color = LOG_COLORS['success']
        elif tag == "info":
            color = LOG_COLORS['info']
        else:
            color = LOG_COLORS['default']

        # 时间戳
        timestamp = datetime.now().strftime("%H:%M:%S")
        time_color = LOG_COLORS['time']

        # V3.9: 格式化消息（转义 HTML 特殊字符，防止 < > & 被解释为 HTML）
        import html
        html_message = html.escape(message).replace('\n', '<br>')

        # 对于简短消息添加时间戳
        if len(message) < 100 and '\n' not in message:
            cursor.insertHtml(
                f'<span style="color: {time_color};">{timestamp}</span> '
                f'<span style="color: {color};">{html_message}</span><br>'
            )
        else:
            cursor.insertHtml(f'<span style="color: {color};">{html_message}</span><br>')

        self.log_text.setTextCursor(cursor)
        self.log_text.ensureCursorVisible()

    def _show_initial_help(self):
        """显示初始帮助信息"""
        t = self.i18n.t
        help_text = f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  {t("help.welcome_title")}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{t("help.usage_steps_title")}
  1. {t("help.step1")}
  2. {t("help.step2")}
  3. {t("help.step3")}
  4. {t("help.step4")}

{t("help.rating_rules_title")}
  {t("help.rule_3_star")}
    {t("help.rule_picked", percentage=self.config.picked_top_percentage)}
  {t("help.rule_2_star")}
  {t("help.rule_1_star")}
  {t("help.rule_0_star")}
  {t("help.rule_flying")}
  {t("help.rule_focus")}
  {t("help.rule_exposure")}
  {t("help.burst_info")}

{t("help.ready")}"""
        self._log(help_text)

    def _format_statistics_report(self, stats):
        """格式化统计报告"""
        t = self.i18n.t
        total = stats.get('total', 0)
        star_3 = stats.get('star_3', 0)
        star_2 = stats.get('star_2', 0)
        star_1 = stats.get('star_1', 0)
        star_0 = stats.get('star_0', 0)
        no_bird = stats.get('no_bird', 0)
        total_time = stats.get('total_time', 0)
        avg_time = stats.get('avg_time', 0)
        picked = stats.get('picked', 0)
        flying = stats.get('flying', 0)

        bird_total = star_3 + star_2 + star_1 + star_0

        report = "\n" + "━" * 50 + "\n"
        report += f"  {t('report.title')}\n"
        report += "━" * 50 + "\n\n"

        report += t("report.total_photos", total=total) + "\n"
        report += t("report.total_time", time_sec=total_time, time_min=total_time/60) + "\n"
        report += t("report.avg_time", avg=avg_time) + "\n\n"

        if total > 0:
            report += f"  ⭐⭐⭐  {star_3:>4}  ({star_3/total*100:>5.1f}%)\n"
            if picked > 0 and star_3 > 0:
                report += f"    └─ 🏆  {picked} ({picked/star_3*100:.0f}%)\n"
            report += f"  ⭐⭐    {star_2:>4}  ({star_2/total*100:>5.1f}%)\n"
            report += f"  ⭐      {star_1:>4}  ({star_1/total*100:>5.1f}%)\n"
            if star_0 > 0:
                report += f"  0⭐     {star_0:>4}  ({star_0/total*100:>5.1f}%)\n"
            report += f"  ❌      {no_bird:>4}  ({no_bird/total*100:>5.1f}%)\n\n"
            report += t("report.bird_total", count=bird_total, percent=bird_total/total*100) + "\n"

            if flying > 0:
                report += f"{t('help.rule_flying')}: {flying}\n"

        report += "\n" + "━" * 50
        return report

    def _show_lightroom_guide(self):
        """显示 Lightroom 指南"""
        t = self.i18n.t
        guide = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  {t("lightroom_guide.title")}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{t("lightroom_guide.method1_title")}
  1. {t("lightroom_guide.method1_step1")}
  2. {t("lightroom_guide.method1_step2")}
  3. {t("lightroom_guide.method1_step3")}
  4. {t("lightroom_guide.method1_step4")}
  5. {t("lightroom_guide.method1_step5")}

{t("lightroom_guide.sort_title")}
  · {t("lightroom_guide.sort_step3_city")}
  · {t("lightroom_guide.sort_step3_state")}
  · {t("lightroom_guide.field_caption")}

{t("lightroom_guide.debug_title")}
  {t("lightroom_guide.debug_tip")}
  · {t("lightroom_guide.debug_explain1")}
  · {t("lightroom_guide.debug_explain2")}
  · {t("lightroom_guide.debug_explain3")}
  · {t("lightroom_guide.debug_explain4")}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        self._log(guide)

    def _play_completion_sound(self):
        """播放完成音效"""
        sound_path = os.path.join(
            os.path.dirname(__file__), "..",
            "img", "toy-story-short-happy-audio-logo-short-cartoony-intro-outro-music-125627.mp3"
        )

        if os.path.exists(sound_path) and sys.platform == 'darwin':
            try:
                subprocess.Popen(
                    ['afplay', sound_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                pass

    def closeEvent(self, event):
        """窗口关闭事件"""
        if self.worker and self.worker.is_alive():
            reply = StyledMessageBox.question(
                self,
                self.i18n.t("messages.exit_title"),
                self.i18n.t("messages.exit_confirm"),
                yes_text=self.i18n.t("buttons.cancel"),
                no_text=self.i18n.t("labels.yes")
            )

            if reply == StyledMessageBox.No:  # 用户点击"是"退出
                self.worker._stop_event.set()
                self.worker._stop_caffeinate()  # V3.8.1: 确保终止 caffeinate 进程
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

    # ========== V3.9.5: 更新检测功能 ==========

    def _check_for_updates(self):
        """后台检查更新（不阻塞UI）"""
        try:
            from update_checker import check_update_async, CURRENT_VERSION
            check_update_async(self._on_update_check_complete, CURRENT_VERSION)
        except ImportError as e:
            print(f"⚠️ 更新检测模块加载失败: {e}")

    @Slot(bool, object)
    def _on_update_check_complete(self, has_update: bool, update_info):
        """更新检测完成的回调（在主线程中调用）"""
        if not has_update or update_info is None:
            return
        
        # 使用 QTimer 确保在主线程中显示对话框
        QTimer.singleShot(0, lambda: self._show_update_dialog(update_info))

    def _show_update_dialog(self, update_info: dict):
        """显示更新对话框"""
        from update_checker import UpdateChecker
        import webbrowser
        
        version = update_info.get('version', 'Unknown')
        download_url = update_info.get('download_url') or update_info.get('release_url', '')
        platform_name = UpdateChecker.get_platform_name()
        
        # 构建消息
        message = f"{self.i18n.t('update.new_version_available', version=version)}\n\n"
        message += f"{self.i18n.t('update.current_version', version='3.9.5')}\n"
        message += f"{self.i18n.t('update.latest_version', version=version)}"
        
        # 显示确认对话框
        reply = StyledMessageBox.question(
            self,
            self.i18n.t("update.title"),
            message,
            yes_text=self.i18n.t("update.download_now"),
            no_text=self.i18n.t("update.remind_later")
        )
        
        if reply == StyledMessageBox.Yes:
            # 打开下载链接
            if download_url:
                webbrowser.open(download_url)
