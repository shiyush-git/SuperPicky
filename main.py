#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky - PySide6 版本入口点
Version: 3.9.3 - Focus Status Fix
"""

import sys
import os

# V3.9.3: 修复 macOS PyInstaller 打包后的多进程问题
# 必须在所有其他导入之前设置
import multiprocessing
if sys.platform == 'darwin':
    multiprocessing.set_start_method('spawn', force=True)

# V3.9.4: 防止 PyInstaller 打包后 spawn 模式创建重复进程/窗口
# 这是 macOS PyInstaller 的标准做法
multiprocessing.freeze_support()

# 确保模块路径正确
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon

from ui.main_window import SuperPickyMainWindow

# V3.9.3: 全局窗口引用，防止重复创建
_main_window = None


def main():
    """主函数"""
    global _main_window
    
    # V3.9.3: 检查是否已有 QApplication 实例
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    else:
        print("⚠️  检测到已存在的 QApplication 实例")
    
    # 设置应用属性
    app.setApplicationName("SuperPicky")
    app.setApplicationDisplayName("慧眼选鸟")
    app.setOrganizationName("JamesPhotography")
    app.setOrganizationDomain("jamesphotography.com.au")
    
    # 设置应用图标
    icon_path = os.path.join(os.path.dirname(__file__), "img", "icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    
    # 注：Qt6/PySide6 默认启用 HiDPI 支持，无需手动设置
    
    # V3.9.3: 防止重复创建窗口
    if _main_window is None:
        _main_window = SuperPickyMainWindow()
        _main_window.show()
    else:
        print("⚠️  检测到已存在的主窗口实例")
        _main_window.raise_()
        _main_window.activateWindow()
    
    # 运行事件循环
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
