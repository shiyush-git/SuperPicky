# -*- coding: utf-8 -*-
"""
SuperPicky - 重新评星对话框
PySide6 版本
"""

import os
import json
import shutil
import threading
from datetime import datetime
from typing import Dict, List, Set, Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QSlider, QPushButton, QGroupBox, QTextEdit,
    QMessageBox, QFrame, QCheckBox
)
from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtGui import QFont, QTextCursor

from post_adjustment_engine import PostAdjustmentEngine, safe_int, safe_float
from exiftool_manager import get_exiftool_manager
from advanced_config import get_advanced_config
from i18n import get_i18n
from constants import RATING_FOLDER_NAMES


class PostAdjustmentDialog(QDialog):
    """重新评星对话框 - PySide6 版本"""
    
    # 信号
    progress_updated = Signal(str)
    
    def __init__(self, parent, directory: str, current_sharpness: int = 500,
                 current_nima: float = 5.0, on_complete_callback=None):
        super().__init__(parent)
        
        self.config = get_advanced_config()
        self.i18n = get_i18n(self.config.language)
        
        self.directory = directory
        self.on_complete_callback = on_complete_callback
        
        # 初始化引擎
        self.engine = PostAdjustmentEngine(directory)
        
        # 阈值变量（使用整数存储，转换时除以对应倍数）
        self.min_confidence = int(self.config.min_confidence * 100)  # 0.5 -> 50
        self.min_sharpness = int(self.config.min_sharpness)
        self.min_nima = int(self.config.min_nima * 10)  # 4.0 -> 40
        self.sharpness_threshold = current_sharpness
        self.nima_threshold = int(current_nima * 10)  # 5.0 -> 50
        self.picked_percentage = int(self.config.picked_top_percentage)
        
        # 数据
        self.original_photos: List[Dict] = []
        self.updated_photos: List[Dict] = []
        self.picked_files: Set[str] = set()
        
        # 统计
        self.current_stats: Optional[Dict] = None
        self.preview_stats: Optional[Dict] = None
        
        # 防抖定时器
        self._preview_timer = None
        
        # 信号连接
        self.progress_updated.connect(self._update_progress_label)
        
        self._setup_ui()
        self._load_data()
    
    def _setup_ui(self):
        """设置 UI"""
        self.setWindowTitle(self.i18n.t("post_adjustment.title"))
        self.setMinimumSize(750, 520)
        self.resize(750, 550)
        self.setModal(True)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        # 顶部两列统计
        self._create_stats_section(layout)
        
        # 阈值调整
        self._create_threshold_section(layout)
        
        # 底部按钮
        self._create_button_section(layout)
        
        # 高级设置（折叠）
        self._create_advanced_section(layout)
    
    def _create_stats_section(self, layout):
        """创建统计区域（两列）"""
        stats_layout = QHBoxLayout()
        
        # 左列：当前统计
        current_group = QGroupBox("  当前评星统计  ")
        current_group.setFont(QFont("PingFang SC", 14, QFont.Bold))
        current_layout = QVBoxLayout(current_group)
        
        self.current_stats_text = QTextEdit()
        self.current_stats_text.setReadOnly(True)
        self.current_stats_text.setFont(QFont("PingFang SC", 13))
        self.current_stats_text.setStyleSheet("""
            QTextEdit {
                background-color: #fafafa;
                color: #333;
                border: none;
                padding: 8px;
            }
        """)
        self.current_stats_text.setMaximumHeight(180)
        current_layout.addWidget(self.current_stats_text)
        
        stats_layout.addWidget(current_group)
        
        # 右列：预览
        preview_group = QGroupBox("  调整后预览  ")
        preview_group.setFont(QFont("PingFang SC", 14, QFont.Bold))
        preview_layout = QVBoxLayout(preview_group)
        
        self.preview_stats_text = QTextEdit()
        self.preview_stats_text.setReadOnly(True)
        self.preview_stats_text.setFont(QFont("PingFang SC", 13))
        self.preview_stats_text.setStyleSheet("""
            QTextEdit {
                background-color: #fafafa;
                color: #333;
                border: none;
                padding: 8px;
            }
        """)
        self.preview_stats_text.setMaximumHeight(180)
        preview_layout.addWidget(self.preview_stats_text)
        
        stats_layout.addWidget(preview_group)
        
        layout.addLayout(stats_layout)
    
    def _create_threshold_section(self, layout):
        """创建阈值调整区域"""
        threshold_group = QGroupBox("  阈值调整（2/3星）  ")
        threshold_group.setFont(QFont("PingFang SC", 14, QFont.Bold))
        threshold_layout = QVBoxLayout(threshold_group)
        
        # 锐度阈值
        self.sharp_slider, self.sharp_label = self._create_slider(
            threshold_layout,
            "🔍 鸟锐度阈值 (2/3星):",
            min_val=300, max_val=1000, default=self.sharpness_threshold,
            step=50
        )
        
        # 美学阈值
        self.nima_slider, self.nima_label = self._create_slider(
            threshold_layout,
            "🎨 摄影美学阈值 (2/3星):",
            min_val=45, max_val=55, default=self.nima_threshold,
            step=1,
            format_func=lambda v: f"{v/10:.1f}"
        )
        
        # 精选百分比
        self.picked_slider, self.picked_label = self._create_slider(
            threshold_layout,
            "🏆 精选旗标百分比:",
            min_val=10, max_val=50, default=self.picked_percentage,
            step=5,
            format_func=lambda v: f"{v}%"
        )
        
        layout.addWidget(threshold_group)
    
    def _create_slider(self, layout, label_text, min_val, max_val, default, 
                       step=1, format_func=None):
        """创建滑块，返回滑块和值标签"""
        container = QHBoxLayout()
        
        label = QLabel(label_text)
        label.setFont(QFont("Arial", 13))
        label.setMinimumWidth(180)
        container.addWidget(label)
        
        slider = QSlider(Qt.Horizontal)
        slider.setRange(min_val, max_val)
        slider.setValue(default)
        slider.setSingleStep(step)
        container.addWidget(slider, 1)
        
        if format_func is None:
            format_func = lambda v: str(v)
        
        value_label = QLabel(format_func(default))
        value_label.setFont(QFont("Arial", 13))
        value_label.setMinimumWidth(60)
        container.addWidget(value_label)
        
        # 存储 format_func 用于后续更新
        slider.format_func = format_func
        
        # 连接信号
        def on_value_changed(v):
            # 步进对齐
            aligned = round(v / step) * step
            if aligned != v:
                slider.blockSignals(True)
                slider.setValue(aligned)
                slider.blockSignals(False)
                v = aligned
            value_label.setText(format_func(v))
            self._on_threshold_changed()
        
        slider.valueChanged.connect(on_value_changed)
        
        layout.addLayout(container)
        return slider, value_label
    
    def _create_button_section(self, layout):
        """创建按钮区域"""
        btn_layout = QHBoxLayout()
        
        # 取消
        cancel_btn = QPushButton("取消")
        cancel_btn.setMinimumWidth(120)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        # 进度标签（中间）
        self.progress_label = QLabel("")
        self.progress_label.setFont(QFont("PingFang SC", 11))
        btn_layout.addWidget(self.progress_label, 1, Qt.AlignCenter)
        
        # 应用
        self.apply_btn = QPushButton("✓ 应用新评星")
        self.apply_btn.setMinimumWidth(120)
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._apply_new_ratings)
        btn_layout.addWidget(self.apply_btn)
        
        layout.addLayout(btn_layout)
    
    def _create_advanced_section(self, layout):
        """创建高级设置区域（折叠）"""
        # 折叠按钮
        self.advanced_check = QCheckBox("▶ 高级: 0星筛选设置")
        self.advanced_check.setFont(QFont("Arial", 12))
        self.advanced_check.stateChanged.connect(self._toggle_advanced)
        layout.addWidget(self.advanced_check)
        
        # 高级设置内容
        self.advanced_group = QGroupBox("  0星筛选阈值（技术质量不达标）  ")
        self.advanced_group.setFont(QFont("PingFang SC", 12))
        advanced_layout = QVBoxLayout(self.advanced_group)
        
        # 最低置信度
        self.conf_slider, self.conf_label = self._create_slider(
            advanced_layout,
            "AI 最低置信度:",
            min_val=30, max_val=80, default=self.min_confidence,
            step=5,
            format_func=lambda v: f"{v/100:.2f}"
        )
        
        # 最低锐度
        self.min_sharp_slider, self.min_sharp_label = self._create_slider(
            advanced_layout,
            "头部最低锐度:",
            min_val=100, max_val=500, default=self.min_sharpness,
            step=50
        )
        
        # 最低美学
        self.min_nima_slider, self.min_nima_label = self._create_slider(
            advanced_layout,
            "最低美学评分:",
            min_val=30, max_val=50, default=self.min_nima,
            step=1,
            format_func=lambda v: f"{v/10:.1f}"
        )
        
        self.advanced_group.hide()  # 默认隐藏
        layout.addWidget(self.advanced_group)
    
    @Slot(int)
    def _toggle_advanced(self, state):
        """切换高级设置显示"""
        if state == Qt.Checked:
            self.advanced_group.show()
            self.advanced_check.setText("▼ 高级: 0星筛选设置")
            self.resize(self.width(), 850)
        else:
            self.advanced_group.hide()
            self.advanced_check.setText("▶ 高级: 0星筛选设置")
            self.resize(self.width(), 550)
    
    def _load_data(self):
        """加载 CSV 数据"""
        success, message = self.engine.load_report()
        
        if not success:
            QMessageBox.critical(self, self.i18n.t("errors.error_title"), message)
            self.reject()
            return
        
        self.original_photos = self.engine.photos_data.copy()
        print(f"DEBUG: 加载了 {len(self.original_photos)} 张照片的数据")
        
        self.current_stats = self._get_original_statistics()
        print(f"DEBUG: 当前统计 = {self.current_stats}")
        
        self._update_current_stats_display()
        self.apply_btn.setEnabled(True)
        self._on_threshold_changed()
    
    def _get_original_statistics(self) -> Dict[str, int]:
        """获取原始统计"""
        stats = {
            'star_0': 0, 'star_1': 0, 'star_2': 0, 'star_3': 0,
            'picked': 0, 'total': len(self.original_photos)
        }
        
        star_3_photos = []
        
        for photo in self.original_photos:
            rating = safe_int(photo.get('rating', '0'), 0)
            if rating == 0:
                stats['star_0'] += 1
            elif rating == 1:
                stats['star_1'] += 1
            elif rating == 2:
                stats['star_2'] += 1
            elif rating == 3:
                stats['star_3'] += 1
                star_3_photos.append(photo)
        
        picked_files = self.engine.recalculate_picked(
            star_3_photos, self.picked_percentage
        )
        stats['picked'] = len(picked_files)
        
        return stats
    
    def _update_current_stats_display(self):
        """更新当前统计显示"""
        if not self.current_stats:
            return
        
        stats = self.current_stats
        text = f"总共 {stats['total']} 张有鸟照片\n\n"
        text += f"⭐⭐⭐ 三星 ({stats['star_3']}) 张\n"
        text += f"  └─🏆 精选 ({stats['picked']}) 张\n"
        text += f"⭐⭐ 两星 ({stats['star_2']}) 张\n"
        text += f"⭐ 一星 ({stats['star_1']}) 张\n"
        text += f"0星 ({stats['star_0']}) 张"
        
        self.current_stats_text.setPlainText(text)
    
    def _on_threshold_changed(self):
        """阈值改变回调（防抖）"""
        if self._preview_timer:
            self._preview_timer.stop()
        
        self._preview_timer = QTimer()
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._update_preview)
        self._preview_timer.start(300)
    
    @Slot()
    def _update_preview(self):
        """更新预览统计"""
        # 从滑块获取值
        sharpness_threshold = self.sharp_slider.value()
        nima_threshold = self.nima_slider.value() / 10.0
        picked_percentage = self.picked_slider.value()
        
        min_confidence = self.conf_slider.value() / 100.0
        min_sharpness = self.min_sharp_slider.value()
        min_nima = self.min_nima_slider.value() / 10.0
        
        # 重新计算
        self.updated_photos = self.engine.recalculate_ratings(
            self.original_photos,
            min_confidence=min_confidence,
            min_sharpness=min_sharpness,
            min_nima=min_nima,
            sharpness_threshold=sharpness_threshold,
            nima_threshold=nima_threshold
        )
        
        star_3_photos = [p for p in self.updated_photos if p.get('新星级') == 3]
        self.picked_files = self.engine.recalculate_picked(star_3_photos, picked_percentage)
        
        self.preview_stats = self.engine.get_statistics(self.updated_photos)
        self.preview_stats['picked'] = len(self.picked_files)
        
        self._update_preview_display()
    
    def _update_preview_display(self):
        """更新预览显示（带变化量）"""
        if not self.preview_stats or not self.current_stats:
            return
        
        old = self.current_stats
        new = self.preview_stats
        
        def format_diff(new_val, old_val):
            diff = new_val - old_val
            if diff == 0:
                return ""
            return f" <span style='color: #d32f2f;'>[{diff:+d}]</span>"
        
        html = f"总共 {new['total']} 张有鸟照片<br><br>"
        html += f"⭐⭐⭐ 三星 ({new['star_3']}) 张{format_diff(new['star_3'], old['star_3'])}<br>"
        html += f"  └─🏆 精选 ({new['picked']}) 张{format_diff(new['picked'], old.get('picked', 0))}<br>"
        html += f"⭐⭐ 两星 ({new['star_2']}) 张{format_diff(new['star_2'], old['star_2'])}<br>"
        html += f"⭐ 一星 ({new['star_1']}) 张{format_diff(new['star_1'], old['star_1'])}<br>"
        html += f"0星 ({new['star_0']}) 张{format_diff(new['star_0'], old['star_0'])}"
        
        self.preview_stats_text.setHtml(html)
    
    @Slot(str)
    def _update_progress_label(self, text):
        """更新进度标签"""
        self.progress_label.setText(text)
    
    @Slot()
    def _apply_new_ratings(self):
        """应用新评星"""
        if not self.updated_photos:
            QMessageBox.warning(
                self,
                self.i18n.t("messages.hint"),
                self.i18n.t("post_adjustment.no_data_warning")
            )
            return
        
        # 过滤有变化的照片
        changed_photos = []
        for photo in self.updated_photos:
            new_rating = photo.get('新星级', 0)
            old_rating = int(photo.get('rating', 0))
            if new_rating != old_rating:
                changed_photos.append(photo)
        
        if not changed_photos:
            QMessageBox.information(
                self,
                self.i18n.t("messages.hint"),
                "当前阈值设置与原始评星一致，无需调整"
            )
            return
        
        # 确认
        msg = f"将更新 {len(changed_photos)} 张照片的评星\n（共 {len(self.updated_photos)} 张有鸟照片）\n\n确定应用新评星？"
        reply = QMessageBox.question(
            self,
            self.i18n.t("post_adjustment.apply_confirm_title"),
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        self.apply_btn.setEnabled(False)
        
        # 在后台线程处理
        def process():
            try:
                self._do_apply(changed_photos)
            except Exception as e:
                self.progress_updated.emit(f"❌ 错误: {e}")
        
        threading.Thread(target=process, daemon=True).start()
    
    def _do_apply(self, changed_photos):
        """执行应用（后台线程）"""
        total = len(changed_photos)
        batch_data = []
        not_found = 0
        
        # 准备数据
        for i, photo in enumerate(changed_photos):
            filename = photo['filename']
            file_path = self.engine.find_image_file(filename)
            
            if not file_path:
                not_found += 1
            else:
                rating = photo.get('新星级', 0)
                pick = 1 if filename in self.picked_files else 0
                batch_data.append({
                    'file': file_path,
                    'rating': rating,
                    'pick': pick
                })
            
            if (i + 1) % 10 == 0 or i == total - 1:
                self.progress_updated.emit(f"查找文件 {i+1}/{total}")
        
        if not batch_data:
            self.progress_updated.emit(f"❌ 未找到文件")
            QTimer.singleShot(0, lambda: self.apply_btn.setEnabled(True))
            return
        
        # EXIF 写入
        exiftool_mgr = get_exiftool_manager()
        total_files = len(batch_data)
        batch_size = 20
        success_count = 0
        failed_count = 0
        
        for i in range(0, total_files, batch_size):
            batch = batch_data[i:i+batch_size]
            current = min(i + batch_size, total_files)
            self.progress_updated.emit(f"写入EXIF {current}/{total_files}")
            
            stats = exiftool_mgr.batch_set_metadata(batch)
            success_count += stats['success']
            failed_count += stats['failed']
        
        # 更新 CSV
        self.progress_updated.emit("更新CSV报告...")
        csv_success, csv_msg = self.engine.update_report_csv(
            changed_photos, self.picked_files
        )
        
        # 文件重分配
        self.progress_updated.emit("重新分配文件目录...")
        moved_count = 0
        
        for photo in changed_photos:
            filename = photo['filename']
            new_rating = photo.get('新星级', 0)
            old_rating = safe_int(photo.get('rating', '0'), 0)
            
            if new_rating == old_rating:
                continue
            
            file_path = self.engine.find_image_file(filename)
            if not file_path:
                continue
            
            target_folder = RATING_FOLDER_NAMES.get(new_rating, "0星_放弃")
            target_dir = os.path.join(self.directory, target_folder)
            actual_filename = os.path.basename(file_path)
            target_path = os.path.join(target_dir, actual_filename)
            
            if os.path.dirname(file_path) == target_dir:
                continue
            
            try:
                if not os.path.exists(target_dir):
                    os.makedirs(target_dir)
                if not os.path.exists(target_path):
                    shutil.move(file_path, target_path)
                    moved_count += 1
            except Exception:
                pass
        
        self.progress_updated.emit("✅ 完成!")
        
        # 显示结果（在主线程）
        result_msg = f"✅ EXIF更新: {success_count} 张\n❌ 失败: {failed_count} 张"
        if moved_count > 0:
            result_msg += f"\n📁 目录重分配: {moved_count} 张"
        result_msg += "\n\n💡 提示：如已导入Lightroom，请「从文件读取元数据」以同步新星级"
        
        def show_result():
            QMessageBox.information(
                self,
                self.i18n.t("post_adjustment.apply_success_title"),
                result_msg
            )
            if self.on_complete_callback:
                self.on_complete_callback()
            self.accept()
        
        QTimer.singleShot(0, show_result)
