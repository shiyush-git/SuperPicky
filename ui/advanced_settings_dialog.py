# -*- coding: utf-8 -*-
"""
SuperPicky - 高级设置对话框
PySide6 版本 - 极简艺术风格（单页面布局）
V3.8: 合并两个 Tab 为单页面布局，添加曝光阈值设置
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QSlider, QPushButton, QGroupBox, QComboBox,
    QWidget, QMessageBox, QFrame, QScrollArea
)
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont

from advanced_config import get_advanced_config
from i18n import get_i18n
from ui.styles import COLORS, FONTS
from ui.custom_dialogs import StyledMessageBox


class AdvancedSettingsDialog(QDialog):
    """高级设置对话框 - 极简艺术风格（单页面布局）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = get_advanced_config()
        self.i18n = get_i18n(self.config.language)

        self.vars = {}

        self._setup_ui()
        self._load_current_config()

    def _setup_ui(self):
        """设置 UI"""
        self.setWindowTitle(self.i18n.t("settings.window_title"))
        self.setMinimumSize(520, 620)
        self.resize(540, 680)
        self.setModal(True)

        # 应用样式
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLORS['bg_primary']};
            }}
            QLabel {{
                color: {COLORS['text_primary']};
                font-size: 13px;
            }}
            QPushButton {{
                background-color: {COLORS['accent']};
                color: {COLORS['bg_void']};
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: #00e6b8;
            }}
            QPushButton#secondary {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border']};
                color: {COLORS['text_secondary']};
            }}
            QPushButton#secondary:hover {{
                border-color: {COLORS['text_muted']};
                color: {COLORS['text_primary']};
            }}
            QPushButton#tertiary {{
                background-color: transparent;
                color: {COLORS['text_tertiary']};
            }}
            QPushButton#tertiary:hover {{
                color: {COLORS['text_secondary']};
            }}
            QSlider::groove:horizontal {{
                height: 4px;
                background: {COLORS['bg_input']};
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {COLORS['accent']};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                width: 16px;
                height: 16px;
                margin: -6px 0;
                background: {COLORS['text_primary']};
                border-radius: 8px;
            }}
            QComboBox {{
                background-color: {COLORS['bg_input']};
                border: 1px solid {COLORS['border']};
                border-radius: 8px;
                padding: 10px 16px;
                color: {COLORS['text_primary']};
                font-size: 13px;
                min-width: 150px;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 24px;
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid {COLORS['text_tertiary']};
                margin-right: 8px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {COLORS['bg_elevated']};
                border: 1px solid {COLORS['border']};
                border-radius: 8px;
                padding: 4px;
                selection-background-color: {COLORS['accent']};
                selection-color: {COLORS['bg_void']};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(0)

        # 标题
        title = QLabel(self.i18n.t("settings.header_title"))
        title.setStyleSheet(f"""
            color: {COLORS['text_primary']};
            font-size: 16px;
            font-weight: 600;
        """)
        layout.addWidget(title)
        layout.addSpacing(24)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(0)

        # 上半区域：0星阈值
        self._create_threshold_section(scroll_layout)
        
        scroll_layout.addSpacing(24)
        
        # 分隔线
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {COLORS['border_subtle']};")
        scroll_layout.addWidget(divider)
        
        scroll_layout.addSpacing(24)

        # 下半区域：输出设置
        self._create_output_section(scroll_layout)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        layout.addSpacing(24)

        # 底部按钮
        self._create_buttons(layout)

    def _create_threshold_section(self, layout):
        """创建 0 星阈值区域"""
        # 区域标题
        section_title = QLabel(self.i18n.t("settings.tab_thresholds").upper())
        section_title.setStyleSheet(f"""
            color: {COLORS['text_tertiary']};
            font-size: 11px;
            font-weight: 500;
            letter-spacing: 1px;
        """)
        layout.addWidget(section_title)
        layout.addSpacing(8)

        # 说明
        desc = QLabel(self.i18n.t("settings.thresholds_desc"))
        desc.setStyleSheet(f"color: {COLORS['text_tertiary']}; font-size: 12px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        layout.addSpacing(16)

        # AI 置信度阈值
        self.vars["min_confidence"] = self._create_slider_setting(
            layout,
            self.i18n.t("settings.ai_confidence"),
            min_val=30, max_val=70, default=50,
            format_func=lambda v: f"{v/100:.2f}",
            scale=100
        )

        layout.addSpacing(12)

        # 锐度最低阈值
        self.vars["min_sharpness"] = self._create_slider_setting(
            layout,
            self.i18n.t("settings.min_sharpness"),
            min_val=100, max_val=500, default=100,  # V4.1: 最小调整为100
            step=10
        )

        layout.addSpacing(12)

        # 美学最低阈值
        self.vars["min_nima"] = self._create_slider_setting(
            layout,
            self.i18n.t("settings.min_aesthetics"),
            min_val=30, max_val=50, default=40,
            format_func=lambda v: f"{v/10:.1f}",
            scale=10
        )

    def _create_output_section(self, layout):
        """创建输出设置区域"""
        # 区域标题
        section_title = QLabel(self.i18n.t("settings.tab_output").upper())
        section_title.setStyleSheet(f"""
            color: {COLORS['text_tertiary']};
            font-size: 11px;
            font-weight: 500;
            letter-spacing: 1px;
        """)
        layout.addWidget(section_title)
        layout.addSpacing(8)

        # 说明
        desc = QLabel(self.i18n.t("settings.output_desc"))
        desc.setStyleSheet(f"color: {COLORS['text_tertiary']}; font-size: 12px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        layout.addSpacing(16)

        # 精选旗标百分比
        self.vars["picked_top_percentage"] = self._create_slider_setting(
            layout,
            self.i18n.t("settings.pick_top_percent"),
            min_val=10, max_val=50, default=25,
            step=5,
            format_func=lambda v: f"{v}%"
        )

        layout.addSpacing(12)

        # V3.8: 曝光阈值
        self.vars["exposure_threshold"] = self._create_slider_setting(
            layout,
            self.i18n.t("settings.exposure_threshold"),
            min_val=5, max_val=20, default=10,
            step=5,
            format_func=lambda v: f"{v}%"
        )

        layout.addSpacing(20)

        # 语言设置
        lang_section = QLabel(self.i18n.t("settings.language_section").upper())
        lang_section.setStyleSheet(f"""
            color: {COLORS['text_tertiary']};
            font-size: 11px;
            font-weight: 500;
            letter-spacing: 1px;
        """)
        layout.addWidget(lang_section)
        layout.addSpacing(12)

        lang_frame = QFrame()
        lang_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_card']};
                border-radius: 8px;
            }}
        """)
        lang_layout = QHBoxLayout(lang_frame)
        lang_layout.setContentsMargins(16, 12, 16, 12)

        lang_label = QLabel(self.i18n.t("settings.interface_language"))
        lang_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 13px;")
        lang_layout.addWidget(lang_label)

        lang_layout.addStretch()

        self.lang_combo = QComboBox()
        i18n_temp = get_i18n()
        available_languages = i18n_temp.get_available_languages()

        self.lang_name_to_code = {}
        self.lang_code_to_name = {}
        for code, name in available_languages.items():
            self.lang_name_to_code[name] = code
            self.lang_code_to_name[code] = name
            self.lang_combo.addItem(name)

        if self.config.language in self.lang_code_to_name:
            current_name = self.lang_code_to_name[self.config.language]
            idx = self.lang_combo.findText(current_name)
            if idx >= 0:
                self.lang_combo.setCurrentIndex(idx)

        lang_layout.addWidget(self.lang_combo)
        layout.addWidget(lang_frame)

        layout.addSpacing(8)

        # 提示
        note = QLabel(self.i18n.t("settings.restart_note"))
        note.setStyleSheet(f"color: {COLORS['warning']}; font-size: 11px;")
        layout.addWidget(note)

    def _create_slider_setting(self, layout, label_text,
                               min_val, max_val, default, step=1,
                               format_func=None, scale=1):
        """创建滑块设置项"""
        container = QHBoxLayout()
        container.setSpacing(16)

        label = QLabel(label_text)
        label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 13px; min-width: 100px;")
        container.addWidget(label)

        slider = QSlider(Qt.Horizontal)
        slider.setRange(min_val, max_val)
        slider.setValue(default)
        slider.setSingleStep(step)
        container.addWidget(slider, 1)

        if format_func is None:
            format_func = lambda v: str(v)

        value_label = QLabel(format_func(default))
        value_label.setStyleSheet(f"""
            color: {COLORS['accent']};
            font-size: 14px;
            font-family: {FONTS['mono']};
            font-weight: 500;
            min-width: 50px;
        """)
        value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        container.addWidget(value_label)

        slider.valueChanged.connect(lambda v: value_label.setText(format_func(v)))

        layout.addLayout(container)

        slider.scale = scale
        return slider

    def _create_buttons(self, layout):
        """创建底部按钮"""
        btn_layout = QHBoxLayout()

        # 恢复默认
        reset_btn = QPushButton(self.i18n.t("settings.reset_defaults"))
        reset_btn.setObjectName("tertiary")
        reset_btn.clicked.connect(self._reset_to_default)
        btn_layout.addWidget(reset_btn)

        btn_layout.addStretch()

        # 取消
        cancel_btn = QPushButton(self.i18n.t("settings.cancel"))
        cancel_btn.setObjectName("secondary")
        cancel_btn.setMinimumWidth(100)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        # 保存
        save_btn = QPushButton(self.i18n.t("settings.save"))
        save_btn.setMinimumWidth(100)
        save_btn.clicked.connect(self._save_settings)
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)

    def _load_current_config(self):
        """加载当前配置"""
        self.vars["min_confidence"].setValue(int(self.config.min_confidence * 100))
        self.vars["min_sharpness"].setValue(int(self.config.min_sharpness))
        self.vars["min_nima"].setValue(int(self.config.min_nima * 10))
        self.vars["picked_top_percentage"].setValue(int(self.config.picked_top_percentage))
        # V3.8: 加载曝光阈值
        self.vars["exposure_threshold"].setValue(int(self.config.exposure_threshold * 100))

    @Slot()
    def _reset_to_default(self):
        """恢复默认设置"""
        reply = StyledMessageBox.question(
            self,
            self.i18n.t("settings.reset_confirm_title"),
            self.i18n.t("settings.reset_confirm_msg"),
            yes_text=self.i18n.t("labels.yes"),
            no_text=self.i18n.t("labels.no")
        )

        if reply == StyledMessageBox.Yes:
            self.config.reset_to_default()
            self._load_current_config()
            StyledMessageBox.information(
                self,
                self.i18n.t("settings.reset_done_title"),
                self.i18n.t("settings.reset_done_msg")
            )

    @Slot()
    def _save_settings(self):
        """保存设置"""
        min_confidence = self.vars["min_confidence"].value() / 100.0
        min_sharpness = self.vars["min_sharpness"].value()
        min_nima = self.vars["min_nima"].value() / 10.0
        picked_percentage = self.vars["picked_top_percentage"].value()
        # V3.8: 获取曝光阈值
        exposure_threshold = self.vars["exposure_threshold"].value() / 100.0

        self.config.set_min_confidence(min_confidence)
        self.config.set_min_sharpness(min_sharpness)
        self.config.set_min_nima(min_nima)
        self.config.set_picked_top_percentage(picked_percentage)
        self.config.set_exposure_threshold(exposure_threshold)
        self.config.set_save_csv(True)

        selected_name = self.lang_combo.currentText()
        if selected_name in self.lang_name_to_code:
            self.config.set_language(self.lang_name_to_code[selected_name])

        if self.config.save():
            StyledMessageBox.information(
                self,
                self.i18n.t("settings.save_success_title"),
                self.i18n.t("settings.save_success_msg")
            )
            self.accept()
        else:
            StyledMessageBox.critical(
                self,
                self.i18n.t("settings.save_error_title"),
                self.i18n.t("settings.save_error_msg")
            )
