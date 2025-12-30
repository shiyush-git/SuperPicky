# -*- coding: utf-8 -*-
"""
SuperPicky - 主窗口
PySide6 版本
"""

import os
import threading
import subprocess
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QSlider, QProgressBar,
    QTextEdit, QGroupBox, QCheckBox, QMenuBar, QMenu,
    QFileDialog, QMessageBox, QSizePolicy, QFrame
)
from PySide6.QtCore import Qt, Signal, QObject, Slot, QTimer
from PySide6.QtGui import QFont, QPixmap, QIcon, QAction, QTextCursor, QColor

from i18n import get_i18n
from advanced_config import get_advanced_config


class WorkerSignals(QObject):
    """工作线程信号"""
    progress = Signal(int)
    log = Signal(str, str)  # message, tag
    finished = Signal(dict)
    error = Signal(str)


class WorkerThread(threading.Thread):
    """处理线程 (与原 Tkinter 版本相同的逻辑)"""
    
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
        try:
            self.caffeinate_process = subprocess.Popen(
                ['caffeinate', '-d', '-i'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            self.signals.log.emit("☕ 已启动防休眠保护", "info")
        except Exception as e:
            self.signals.log.emit(f"⚠️ 防休眠启动失败: {e}", "warning")
    
    def _stop_caffeinate(self):
        """停止防休眠"""
        if self.caffeinate_process:
            try:
                self.caffeinate_process.terminate()
                self.caffeinate_process.wait(timeout=2)
                self.signals.log.emit("☕ 已停止防休眠保护", "info")
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
            detect_flight=self.ui_settings[5] if len(self.ui_settings) > 5 else True
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
        
        self.stats = result.stats


class SuperPickyMainWindow(QMainWindow):
    """SuperPicky 主窗口"""
    
    def __init__(self):
        super().__init__()
        
        # 初始化配置和国际化
        self.config = get_advanced_config()
        self.i18n = get_i18n(self.config.language)
        
        # 状态变量
        self.directory_path = ""
        self.worker = None
        self.worker_signals = None
        
        # 设置窗口
        self._setup_window()
        self._setup_menu()
        self._setup_ui()
        self._show_initial_help()
    
    def _setup_window(self):
        """设置窗口属性"""
        self.setWindowTitle(self.i18n.t("app.window_title"))
        self.setMinimumSize(700, 650)
        self.resize(750, 700)
        
        # 设置图标
        icon_path = os.path.join(os.path.dirname(__file__), "..", "img", "icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
    
    def _setup_menu(self):
        """设置菜单栏"""
        menubar = self.menuBar()
        
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
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # 标题
        self._create_title_section(main_layout)
        
        # 目录选择
        self._create_directory_section(main_layout)
        
        # 参数设置
        self._create_settings_section(main_layout)
        
        # 进度和日志
        self._create_progress_section(main_layout)
        
        # 控制按钮
        self._create_button_section(main_layout)
    
    def _create_title_section(self, parent_layout):
        """创建标题区域"""
        title_layout = QHBoxLayout()
        title_layout.setAlignment(Qt.AlignCenter)
        
        title_font = QFont("Arial", 16)
        title_font.setBold(True)
        
        label1 = QLabel("拍片一时爽，")
        label1.setFont(title_font)
        title_layout.addWidget(label1)
        
        # 图标
        icon_path = os.path.join(os.path.dirname(__file__), "..", "img", "icon.png")
        if os.path.exists(icon_path):
            icon_label = QLabel()
            pixmap = QPixmap(icon_path).scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            icon_label.setPixmap(pixmap)
            title_layout.addWidget(icon_label)
        
        label2 = QLabel("选片照样爽")
        label2.setFont(title_font)
        title_layout.addWidget(label2)
        
        parent_layout.addLayout(title_layout)
    
    def _create_directory_section(self, parent_layout):
        """创建目录选择区域"""
        dir_group = QGroupBox(self.i18n.t("labels.select_photo_dir"))
        dir_group.setFont(QFont("Arial", 14))
        dir_layout = QHBoxLayout(dir_group)
        
        self.dir_input = QLineEdit()
        self.dir_input.setFont(QFont("Arial", 11))
        self.dir_input.setPlaceholderText("点击浏览或粘贴路径后按回车")
        self.dir_input.returnPressed.connect(self._on_path_entered)
        dir_layout.addWidget(self.dir_input)
        
        browse_btn = QPushButton(self.i18n.t("labels.browse"))
        browse_btn.setMinimumWidth(80)
        browse_btn.clicked.connect(self._browse_directory)
        dir_layout.addWidget(browse_btn)
        
        parent_layout.addWidget(dir_group)
    
    def _create_settings_section(self, parent_layout):
        """创建参数设置区域"""
        settings_group = QGroupBox()
        settings_layout = QVBoxLayout(settings_group)
        
        # 标题行 (左标题，右飞版检测)
        title_row = QHBoxLayout()
        title_label = QLabel(self.i18n.t("labels.rating_params"))
        title_label.setFont(QFont("Arial", 15, QFont.Bold))
        title_row.addWidget(title_label)
        
        title_row.addStretch()
        
        self.flight_check = QCheckBox("识别飞鸟")
        self.flight_check.setFont(QFont("Arial", 14))
        self.flight_check.setChecked(True)
        title_row.addWidget(self.flight_check)
        
        settings_layout.addLayout(title_row)
        
        # 隐藏的变量 (兼容原逻辑)
        self.ai_confidence = 50
        self.norm_mode = "log_compression"
        
        # 锐度阈值
        sharp_layout = QHBoxLayout()
        sharp_label = QLabel(self.i18n.t("labels.sharpness"))
        sharp_label.setFont(QFont("Arial", 15))
        sharp_label.setMinimumWidth(120)
        sharp_layout.addWidget(sharp_label)
        
        self.sharp_slider = QSlider(Qt.Horizontal)
        self.sharp_slider.setRange(100, 1000)
        self.sharp_slider.setValue(500)
        self.sharp_slider.setSingleStep(50)
        self.sharp_slider.valueChanged.connect(self._on_sharp_changed)
        sharp_layout.addWidget(self.sharp_slider)
        
        self.sharp_value = QLabel("500")
        self.sharp_value.setFont(QFont("Arial", 15))
        self.sharp_value.setMinimumWidth(60)
        sharp_layout.addWidget(self.sharp_value)
        
        settings_layout.addLayout(sharp_layout)
        
        # NIMA 阈值
        nima_layout = QHBoxLayout()
        nima_label = QLabel(self.i18n.t("labels.nima"))
        nima_label.setFont(QFont("Arial", 15))
        nima_label.setMinimumWidth(120)
        nima_layout.addWidget(nima_label)
        
        self.nima_slider = QSlider(Qt.Horizontal)
        self.nima_slider.setRange(45, 55)  # 4.5 - 5.5, 用整数*10表示
        self.nima_slider.setValue(50)  # 5.0
        self.nima_slider.valueChanged.connect(self._on_nima_changed)
        nima_layout.addWidget(self.nima_slider)
        
        self.nima_value = QLabel("5.0")
        self.nima_value.setFont(QFont("Arial", 15))
        self.nima_value.setMinimumWidth(60)
        nima_layout.addWidget(self.nima_value)
        
        settings_layout.addLayout(nima_layout)
        
        parent_layout.addWidget(settings_group)
    
    def _create_progress_section(self, parent_layout):
        """创建进度和日志区域"""
        progress_group = QGroupBox(self.i18n.t("labels.processing"))
        progress_group.setFont(QFont("Arial", 14))
        progress_layout = QVBoxLayout(progress_group)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)
        
        # 日志文本框
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Menlo", 13))
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                padding: 8px;
            }
        """)
        self.log_text.setMinimumHeight(200)
        progress_layout.addWidget(self.log_text)
        
        parent_layout.addWidget(progress_group, 1)  # 1 = stretch factor
    
    def _create_button_section(self, parent_layout):
        """创建按钮区域"""
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        # 开始按钮
        self.start_btn = QPushButton(self.i18n.t("buttons.start"))
        self.start_btn.setMinimumWidth(120)
        self.start_btn.clicked.connect(self._start_processing)
        btn_layout.addWidget(self.start_btn)
        
        # 重新评星按钮
        self.post_da_btn = QPushButton(self.i18n.t("buttons.post_adjust"))
        self.post_da_btn.setMinimumWidth(120)
        self.post_da_btn.setEnabled(False)
        self.post_da_btn.clicked.connect(self._open_post_adjustment)
        btn_layout.addWidget(self.post_da_btn)
        
        # 重置按钮
        self.reset_btn = QPushButton(self.i18n.t("buttons.reset"))
        self.reset_btn.setMinimumWidth(120)
        self.reset_btn.setEnabled(False)
        self.reset_btn.clicked.connect(self._reset_directory)
        btn_layout.addWidget(self.reset_btn)
        
        # 版本号
        version_label = QLabel("V3.6.0")
        version_label.setFont(QFont("Arial", 9))
        btn_layout.addWidget(version_label)
        
        parent_layout.addLayout(btn_layout)
    
    # ========== 槽函数 ==========
    
    @Slot()
    def _on_sharp_changed(self):
        """锐度滑块变化"""
        value = self.sharp_slider.value()
        rounded = round(value / 50) * 50
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
        """路径输入回车"""
        directory = self.dir_input.text().strip()
        if directory and os.path.isdir(directory):
            self._handle_directory_selection(directory)
        elif directory:
            QMessageBox.critical(
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
    
    def _handle_directory_selection(self, directory):
        """处理目录选择"""
        self.directory_path = directory
        self.dir_input.setText(directory)
        
        self._log(self.i18n.t("messages.dir_selected", directory=directory))
        
        self.start_btn.setEnabled(True)
        self.reset_btn.setEnabled(True)
        
        self._check_report_csv()
        
        # 检测历史记录
        history_csv = os.path.join(directory, ".superpicky", "report.csv")
        history_manifest = os.path.join(directory, ".superpicky_manifest.json")
        
        if os.path.exists(history_csv) or os.path.exists(history_manifest):
            reply = QMessageBox.question(
                self,
                self.i18n.t("messages.history_detected_title"),
                self.i18n.t("messages.history_detected_msg"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                QTimer.singleShot(100, self._open_post_adjustment)
    
    def _check_report_csv(self):
        """检查是否有 report.csv"""
        if not self.directory_path:
            self.post_da_btn.setEnabled(False)
            return
        
        report_path = os.path.join(self.directory_path, ".superpicky", "report.csv")
        if os.path.exists(report_path):
            self.post_da_btn.setEnabled(True)
            self._log(f"📊 {self.i18n.t('messages.report_detected')}")
        else:
            self.post_da_btn.setEnabled(False)
    
    @Slot()
    def _start_processing(self):
        """开始处理"""
        if not self.directory_path:
            QMessageBox.warning(
                self, 
                self.i18n.t("messages.hint"),
                self.i18n.t("messages.select_dir_first")
            )
            return
        
        if self.worker and self.worker.is_alive():
            QMessageBox.warning(
                self,
                self.i18n.t("messages.hint"),
                self.i18n.t("messages.processing")
            )
            return
        
        # 确认弹窗
        confirm_msg = """识别完成后，将按评星移动到对应文件夹：

• 3星 → 3星_优选
• 2星 → 2星_良好
• 1星 → 1星_普通
• 0星/无鸟 → 0星_放弃

如需恢复原始目录结构，可使用"重置目录"功能。"""
        
        reply = QMessageBox.question(
            self,
            "文件整理提示",
            confirm_msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # 清空日志和进度
        self.log_text.clear()
        self.progress_bar.setValue(0)
        
        self._log(self.i18n.t("logs.processing_start"))
        
        # 准备 UI 设置
        ui_settings = [
            self.ai_confidence,
            self.sharp_slider.value(),
            self.nima_slider.value() / 10.0,
            False,  # save_crop
            self.norm_mode,
            self.flight_check.isChecked()
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
    
    @Slot(str, str)
    def _on_log(self, message, tag):
        """日志更新"""
        self._log(message, tag)
    
    @Slot(dict)
    def _on_finished(self, stats):
        """处理完成"""
        self.start_btn.setEnabled(True)
        self.reset_btn.setEnabled(True)
        self.post_da_btn.setEnabled(True)
        self.progress_bar.setValue(100)
        
        # 显示报告
        self.log_text.clear()
        report = self._format_statistics_report(stats)
        self._log(report)
        
        # 显示 Lightroom 指南
        self._show_lightroom_guide()
        
        # 播放完成音效
        self._play_completion_sound()
        
        # 打开目录
        if self.directory_path and os.path.exists(self.directory_path):
            subprocess.Popen(['open', self.directory_path])
    
    @Slot(str)
    def _on_error(self, error_msg):
        """处理错误"""
        self._log(f"❌ 错误: {error_msg}", "error")
        self.start_btn.setEnabled(True)
        self.reset_btn.setEnabled(True)
    
    @Slot()
    def _reset_directory(self):
        """重置目录"""
        if not self.directory_path:
            QMessageBox.warning(
                self,
                self.i18n.t("messages.hint"),
                self.i18n.t("messages.select_dir_first")
            )
            return
        
        reply = QMessageBox.question(
            self,
            self.i18n.t("messages.reset_confirm_title"),
            self.i18n.t("messages.reset_confirm"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        self.log_text.clear()
        self.reset_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        
        self._log(self.i18n.t("logs.separator"))
        self._log(self.i18n.t("logs.reset_start"))
        
        def run_reset():
            try:
                from exiftool_manager import get_exiftool_manager
                from find_bird_util import reset
                
                exiftool_mgr = get_exiftool_manager()
                restore_stats = exiftool_mgr.restore_files_from_manifest(
                    self.directory_path
                )
                
                success = reset(self.directory_path, i18n=self.i18n)
                
                # 使用 QTimer 在主线程中更新 UI
                QTimer.singleShot(0, lambda: self._on_reset_complete(success, restore_stats))
            except Exception as e:
                QTimer.singleShot(0, lambda: self._on_reset_error(str(e)))
        
        threading.Thread(target=run_reset, daemon=True).start()
    
    def _on_reset_complete(self, success, restore_stats=None):
        """重置完成"""
        if success:
            self._log(self.i18n.t("logs.reset_complete"))
            
            if restore_stats:
                restored = restore_stats.get('restored', 0)
                if restored > 0:
                    QMessageBox.information(
                        self,
                        "重置完成",
                        f"✅ 已成功恢复 {restored} 张照片到主目录"
                    )
            
            QMessageBox.information(
                self,
                self.i18n.t("messages.reset_complete_title"),
                self.i18n.t("messages.reset_complete")
            )
        else:
            self._log(self.i18n.t("logs.reset_failed"))
        
        self.reset_btn.setEnabled(True)
        self.start_btn.setEnabled(True)
        self._check_report_csv()
    
    def _on_reset_error(self, error_msg):
        """重置错误"""
        self._log(f"❌ 错误: {error_msg}", "error")
        QMessageBox.critical(
            self,
            self.i18n.t("errors.error_title"),
            error_msg
        )
        self.reset_btn.setEnabled(True)
        self.start_btn.setEnabled(True)
    
    @Slot()
    def _open_post_adjustment(self):
        """打开重新评星对话框"""
        if not self.directory_path:
            QMessageBox.warning(
                self,
                self.i18n.t("messages.hint"),
                self.i18n.t("messages.select_dir_first")
            )
            return
        
        report_path = os.path.join(self.directory_path, ".superpicky", "report.csv")
        if not os.path.exists(report_path):
            QMessageBox.warning(
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
            on_complete_callback=self._on_post_adjustment_complete
        )
        dialog.exec()
    
    def _on_post_adjustment_complete(self):
        """重新评星完成回调"""
        self._log("✅ 重新评星完成！评分已更新到EXIF元数据")
    
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
    
    # ========== 辅助方法 ==========
    
    def _log(self, message, tag=None):
        """输出日志"""
        print(message)  # 同时输出到终端
        
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        
        # 设置颜色
        if tag == "error":
            color = "#ff0066"
        elif tag == "warning":
            color = "#ffaa00"
        elif tag == "success":
            color = "#00ff88"
        elif tag == "info":
            color = "#00aaff"
        else:
            color = "#d4d4d4"
        
        cursor.insertHtml(f'<span style="color: {color};">{message}</span><br>')
        
        self.log_text.setTextCursor(cursor)
        self.log_text.ensureCursorVisible()
    
    def _show_initial_help(self):
        """显示初始帮助信息"""
        separator = "━" * 60
        help_text = f"""{separator}
  {self.i18n.t("help.welcome_title")}
{separator}
{self.i18n.t("help.usage_steps_title")}
  1️⃣ {self.i18n.t("help.step1")}
  2️⃣ {self.i18n.t("help.step2")}
  3️⃣ {self.i18n.t("help.step3")}
  4️⃣ {self.i18n.t("help.step4")}

📊 {self.i18n.t("help.rating_rules_title")}
  • {self.i18n.t("help.rule_3_star")}
  • {self.i18n.t("help.rule_2_star")}
  • {self.i18n.t("help.rule_1_star")}
  • {self.i18n.t("help.rule_0_star")}
  • {self.i18n.t("help.rule_rejected")}

{self.i18n.t("help.ready")}
{separator}"""
        self._log(help_text)
    
    def _format_statistics_report(self, stats):
        """格式化统计报告"""
        total = stats.get('total', 0)
        star_3 = stats.get('star_3', 0)
        star_2 = stats.get('star_2', 0)
        star_1 = stats.get('star_1', 0)
        star_0 = stats.get('star_0', 0)
        no_bird = stats.get('no_bird', 0)
        total_time = stats.get('total_time', 0)
        avg_time = stats.get('avg_time', 0)
        picked = stats.get('picked', 0)
        
        bird_total = star_3 + star_2 + star_1 + star_0
        
        report = "\n" + "=" * 50 + "\n"
        report += f"📊 {self.i18n.t('report.title')}\n"
        report += "=" * 50 + "\n"
        report += self.i18n.t('report.total_photos', total=total) + "\n"
        report += self.i18n.t('report.total_time', time_sec=total_time, time_min=total_time/60) + "\n"
        report += self.i18n.t('report.avg_time', avg=avg_time) + "\n\n"
        
        if total > 0:
            report += f"⭐⭐⭐ {self.i18n.t('report.star_3', count=star_3, percent=star_3/total*100)}\n"
            if picked > 0 and star_3 > 0:
                report += f"  └─ {self.i18n.t('report.picked_detail', count=picked, percent=picked/star_3*100)}\n"
            report += f"⭐⭐ {self.i18n.t('report.star_2', count=star_2, percent=star_2/total*100)}\n"
            report += f"⭐ {self.i18n.t('report.star_1', count=star_1, percent=star_1/total*100)}\n"
            if star_0 > 0:
                report += self.i18n.t('report.star_0', count=star_0, percent=star_0/total*100) + "\n"
            report += f"❌ {self.i18n.t('report.no_bird', count=no_bird, percent=no_bird/total*100)}\n\n"
            report += self.i18n.t('report.bird_total', count=bird_total, percent=bird_total/total*100) + "\n"
        
        report += "=" * 50 + "\n"
        return report
    
    def _show_lightroom_guide(self):
        """显示 Lightroom 指南"""
        separator = "━" * 60
        guide = f"""
{separator}
  📸 {self.i18n.t("lightroom_guide.title")}
{separator}

【{self.i18n.t("lightroom_guide.method1_title")}】
  1️⃣ {self.i18n.t("lightroom_guide.method1_step1")}
  2️⃣ {self.i18n.t("lightroom_guide.method1_step2")}
  3️⃣ {self.i18n.t("lightroom_guide.method1_step3")}

{separator}
"""
        self._log(guide)
    
    def _play_completion_sound(self):
        """播放完成音效"""
        sound_path = os.path.join(
            os.path.dirname(__file__), "..",
            "img", "toy-story-short-happy-audio-logo-short-cartoony-intro-outro-music-125627.mp3"
        )
        
        if os.path.exists(sound_path):
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
            reply = QMessageBox.question(
                self,
                self.i18n.t("messages.exit_title"),
                self.i18n.t("messages.exit_confirm"),
                QMessageBox.Ok | QMessageBox.Cancel,
                QMessageBox.Cancel
            )
            
            if reply == QMessageBox.Ok:
                self.worker._stop_event.set()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()
