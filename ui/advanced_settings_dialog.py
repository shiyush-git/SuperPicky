# -*- coding: utf-8 -*-
"""
SuperPicky - 高级设置对话框
PySide6 版本
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QSlider, QPushButton, QGroupBox, QComboBox,
    QTabWidget, QWidget, QMessageBox
)
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont

from advanced_config import get_advanced_config
from i18n import get_i18n


class AdvancedSettingsDialog(QDialog):
    """高级设置对话框 - PySide6 版本"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = get_advanced_config()
        self.i18n = get_i18n(self.config.language)
        
        # 变量存储
        self.vars = {}
        
        self._setup_ui()
        self._load_current_config()
    
    def _setup_ui(self):
        """设置 UI"""
        self.setWindowTitle(self.i18n.t("advanced_settings.title"))
        self.setMinimumSize(550, 600)
        self.resize(550, 650)
        self.setModal(True)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        # 选项卡
        tab_widget = QTabWidget()
        layout.addWidget(tab_widget)
        
        # Tab 1: 评分阈值
        rating_tab = QWidget()
        rating_layout = QVBoxLayout(rating_tab)
        rating_layout.setContentsMargins(15, 15, 15, 15)
        self._create_rating_tab(rating_layout)
        tab_widget.addTab(rating_tab, self.i18n.t("advanced_settings.zero_star_thresholds"))
        
        # Tab 2: 输出设置
        output_tab = QWidget()
        output_layout = QVBoxLayout(output_tab)
        output_layout.setContentsMargins(15, 15, 15, 15)
        self._create_output_tab(output_layout)
        tab_widget.addTab(output_tab, self.i18n.t("advanced_settings.output_settings"))
        
        # 底部按钮
        self._create_buttons(layout)
    
    def _create_rating_tab(self, layout):
        """创建评分阈值选项卡"""
        # 说明
        desc = QLabel(self.i18n.t("advanced_settings.rating_tab_description"))
        desc.setStyleSheet("color: #666;")
        layout.addWidget(desc)
        
        # AI 置信度阈值
        self.vars["min_confidence"] = self._create_slider_setting(
            layout,
            self.i18n.t("advanced_settings.min_confidence_label"),
            self.i18n.t("advanced_settings.min_confidence_description"),
            min_val=30, max_val=70, default=50,
            format_func=lambda v: f"{v/100:.2f} ({v}%)",
            scale=100
        )
        
        # 锐度最低阈值
        self.vars["min_sharpness"] = self._create_slider_setting(
            layout,
            self.i18n.t("advanced_settings.min_sharpness_label"),
            self.i18n.t("advanced_settings.min_sharpness_description"),
            min_val=200, max_val=500, default=250,
            step=10
        )
        
        # 美学最低阈值
        self.vars["min_nima"] = self._create_slider_setting(
            layout,
            self.i18n.t("advanced_settings.min_nima_label"),
            self.i18n.t("advanced_settings.min_nima_description"),
            min_val=30, max_val=50, default=40,
            format_func=lambda v: f"{v/10:.1f}",
            scale=10
        )
        
        layout.addStretch()
    
    def _create_output_tab(self, layout):
        """创建输出设置选项卡"""
        # 说明
        desc = QLabel(self.i18n.t("advanced_settings.output_tab_description"))
        desc.setStyleSheet("color: #666;")
        layout.addWidget(desc)
        
        # 精选旗标百分比
        self.vars["picked_top_percentage"] = self._create_slider_setting(
            layout,
            self.i18n.t("advanced_settings.picked_percentage_label"),
            self.i18n.t("advanced_settings.picked_percentage_description"),
            min_val=10, max_val=50, default=25,
            step=5,
            format_func=lambda v: f"{v}%"
        )
        
        # 语言设置
        lang_group = QGroupBox(self.i18n.t("advanced_settings.language_settings"))
        lang_layout = QHBoxLayout(lang_group)
        
        lang_label = QLabel(self.i18n.t("advanced_settings.language_label"))
        lang_layout.addWidget(lang_label)
        
        self.lang_combo = QComboBox()
        i18n_temp = get_i18n()
        available_languages = i18n_temp.get_available_languages()
        
        self.lang_name_to_code = {}
        self.lang_code_to_name = {}
        for code, name in available_languages.items():
            self.lang_name_to_code[name] = code
            self.lang_code_to_name[code] = name
            self.lang_combo.addItem(name)
        
        # 设置当前语言
        if self.config.language in self.lang_code_to_name:
            current_name = self.lang_code_to_name[self.config.language]
            idx = self.lang_combo.findText(current_name)
            if idx >= 0:
                self.lang_combo.setCurrentIndex(idx)
        
        lang_layout.addWidget(self.lang_combo)
        lang_layout.addStretch()
        
        layout.addWidget(lang_group)
        
        # 提示
        note = QLabel(self.i18n.t("advanced_settings.language_note"))
        note.setStyleSheet("color: #FF6B6B;")
        layout.addWidget(note)
        
        layout.addStretch()
    
    def _create_slider_setting(self, layout, label_text, description, 
                               min_val, max_val, default, step=1, 
                               format_func=None, scale=1):
        """创建滑块设置项，返回滑块以便后续获取值"""
        group = QGroupBox(label_text)
        group_layout = QVBoxLayout(group)
        
        # 描述
        desc_label = QLabel(description)
        desc_label.setStyleSheet("color: #888;")
        desc_label.setWordWrap(True)
        group_layout.addWidget(desc_label)
        
        # 滑块行
        slider_layout = QHBoxLayout()
        
        slider = QSlider(Qt.Horizontal)
        slider.setRange(min_val, max_val)
        slider.setValue(default)
        slider.setSingleStep(step)
        slider_layout.addWidget(slider)
        
        if format_func is None:
            format_func = lambda v: str(v)
        
        value_label = QLabel(format_func(default))
        value_label.setFont(QFont("Arial", 10, QFont.Bold))
        value_label.setMinimumWidth(80)
        slider_layout.addWidget(value_label)
        
        # 连接信号
        slider.valueChanged.connect(lambda v: value_label.setText(format_func(v)))
        
        group_layout.addLayout(slider_layout)
        layout.addWidget(group)
        
        # 存储 scale 用于后续转换
        slider.scale = scale
        return slider
    
    def _create_buttons(self, layout):
        """创建底部按钮"""
        btn_layout = QHBoxLayout()
        
        # 恢复默认
        reset_btn = QPushButton(self.i18n.t("advanced_settings.reset_to_default"))
        reset_btn.clicked.connect(self._reset_to_default)
        btn_layout.addWidget(reset_btn)
        
        btn_layout.addStretch()
        
        # 取消
        cancel_btn = QPushButton(self.i18n.t("buttons.cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        # 保存
        save_btn = QPushButton(self.i18n.t("buttons.save"))
        save_btn.clicked.connect(self._save_settings)
        btn_layout.addWidget(save_btn)
        
        layout.addLayout(btn_layout)
    
    def _load_current_config(self):
        """加载当前配置"""
        # min_confidence 使用 scale=100，所以要乘以 100
        self.vars["min_confidence"].setValue(int(self.config.min_confidence * 100))
        self.vars["min_sharpness"].setValue(int(self.config.min_sharpness))
        # min_nima 使用 scale=10
        self.vars["min_nima"].setValue(int(self.config.min_nima * 10))
        self.vars["picked_top_percentage"].setValue(int(self.config.picked_top_percentage))
    
    @Slot()
    def _reset_to_default(self):
        """恢复默认设置"""
        reply = QMessageBox.question(
            self,
            self.i18n.t("messages.reset_confirm_title"),
            self.i18n.t("advanced_settings.settings_reset"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.config.reset_to_default()
            self._load_current_config()
            QMessageBox.information(
                self,
                self.i18n.t("advanced_settings.settings_reset_title"),
                self.i18n.t("advanced_settings.settings_reset")
            )
    
    @Slot()
    def _save_settings(self):
        """保存设置"""
        # 从滑块获取值并转换
        min_confidence = self.vars["min_confidence"].value() / 100.0
        min_sharpness = self.vars["min_sharpness"].value()
        min_nima = self.vars["min_nima"].value() / 10.0
        picked_percentage = self.vars["picked_top_percentage"].value()
        
        # 更新配置
        self.config.set_min_confidence(min_confidence)
        self.config.set_min_sharpness(min_sharpness)
        self.config.set_min_nima(min_nima)
        self.config.set_picked_top_percentage(picked_percentage)
        self.config.set_save_csv(True)
        
        # 语言
        selected_name = self.lang_combo.currentText()
        if selected_name in self.lang_name_to_code:
            self.config.set_language(self.lang_name_to_code[selected_name])
        
        # 保存
        if self.config.save():
            message = (
                self.i18n.t("advanced_settings.settings_saved") + "\n" +
                self.i18n.t("advanced_settings.language_note")
            )
            QMessageBox.information(
                self,
                self.i18n.t("advanced_settings.settings_saved_title"),
                message
            )
            self.accept()
        else:
            QMessageBox.critical(
                self,
                self.i18n.t("errors.error_title"),
                self.i18n.t("advanced_settings.settings_save_failed", error="")
            )
