#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Focus Point Detector - 对焦点检测器
用于从 Nikon Z8/Z9 RAW 文件中提取对焦点坐标，并验证是否落在鸟的 Bounding Box 内

支持功能：
- 读取 Nikon MakerNotes 中的对焦数据
- 坐标归一化 (0.0-1.0)
- 竖拍旋转处理
- DX 裁切修正
- 与 YOLO Bounding Box 碰撞检测
"""

from dataclasses import dataclass
from typing import Optional, Tuple
import subprocess
import json
import numpy as np


@dataclass
class FocusPointResult:
    """对焦点检测结果"""
    x: float                    # 归一化 X 坐标 (0.0-1.0)
    y: float                    # 归一化 Y 坐标 (0.0-1.0)
    raw_x: int                  # 原始像素 X 坐标
    raw_y: int                  # 原始像素 Y 坐标
    area_width: int             # 对焦区域宽度
    area_height: int            # 对焦区域高度
    af_mode: str                # AF 模式 (AF-C, AF-S 等)
    area_mode: str              # 区域模式 (Auto, 3D-Tracking 等)
    focus_result: any           # 合焦结果 (1=Focus 或 "Focus")
    is_valid: bool              # 是否有效对焦数据
    
    @property
    def is_focused(self) -> bool:
        """是否成功合焦 (FocusResult: 1=Focus, 其他=未合焦)"""
        # ExifTool -n 返回数值: 1 = Focus
        if isinstance(self.focus_result, (int, float)):
            return int(self.focus_result) == 1
        return str(self.focus_result).lower() == 'focus'


class FocusPointDetector:
    """
    对焦点检测器 - 多相机品牌支持
    
    支持相机:
    - Nikon Z8/Z9/Z6/Z7 + D 系列 (NEF/NRW) ✅
    - Sony A1/A7R5/A7M4/A9/A6xxx (ARW) ✅
    - Canon R1/R3/R5/R6/R7/R8 + EOS 系列 (CR3/CR2) ✅
    - Olympus/OM System OM-1/OM-5/E-M1 (ORF) ✅
    - Fujifilm X-T5/X-H2/GFX (RAF) ✅
    - Panasonic S5/GH6/G9 (RW2) ✅
    """
    
    # 通用标签 + 各品牌特有标签
    COMMON_TAGS = ['Make', 'Model', 'FocusMode', 'Orientation']
    
    NIKON_TAGS = [
        'AFAreaMode',
        'AFAreaXPosition',
        'AFAreaYPosition',
        'AFAreaWidth',
        'AFAreaHeight',
        'AFImageWidth',
        'AFImageHeight',
        'FocusResult',
        'CropArea',
    ]
    
    SONY_TAGS = [
        'FocusLocation',
        'AFAreaMode',
        'FocusFrameSize',
    ]
    
    # Canon: 基于 Focus-Points 仓库分析
    # 坐标是从图像中心偏移的！需要 +imageWidth/2 和 +imageHeight/2
    CANON_TAGS = [
        'AFAreaMode',
        'AFPointsInFocus',      # 合焦点索引 (0-based, 逗号分隔)
        'AFAreaXPositions',     # X 坐标列表 (空格分隔, 从中心偏移)
        'AFAreaYPositions',     # Y 坐标列表 (空格分隔, 从中心偏移)
        'AFAreaWidths',         # 对焦框宽度列表
        'AFAreaHeights',        # 对焦框高度列表
        'AFImageWidth',         # AF 图像宽度
        'AFImageHeight',        # AF 图像高度
        'ExifImageWidth',       # 备用图像宽度
        'ExifImageHeight',      # 备用图像高度
    ]
    
    # Olympus/OM System: 基于 Focus-Points 仓库分析
    # 新 OM 系统: AF Frame Size + AF Focus Area
    # 旧 Olympus: AF Point Selected (归一化 0-1)
    OLYMPUS_TAGS = [
        'AFPointSelected',      # 归一化坐标 "x y" (0.0-1.0)
        'AFFrameSize',          # AF 框尺寸 "w h"
        'AFFocusArea',          # 对焦区域 "x y w h"
        'AFSelectedArea',       # 选择区域
        'AFAreaMode',
        'ExifImageWidth',
        'ExifImageHeight',
    ]
    
    # Fujifilm: 基于 Focus-Points 仓库分析
    # Focus Pixel: 像素坐标 "x y"
    FUJIFILM_TAGS = [
        'FocusPixel',           # 像素坐标 "x y"
        'FocusMode',
        'AFMode',
        'AFAreaMode',
        'ExifImageWidth',
        'ExifImageHeight',
    ]
    
    # Panasonic: 基于 Focus-Points 仓库分析  
    # AF Point Position: 归一化坐标 "0.x 0.y" (0.0-1.0)
    PANASONIC_TAGS = [
        'AFPointPosition',      # 归一化坐标 "0.5 0.5"
        'AFAreaSize',           # 对焦框尺寸 (归一化)
        'FocusMode',
        'AFAreaMode',
        'ExifImageWidth',
        'ExifImageHeight',
    ]
    
    def __init__(self, exiftool_path: str = 'exiftool'):
        """
        初始化检测器
        
        Args:
            exiftool_path: ExifTool 可执行文件路径
        """
        self.exiftool_path = exiftool_path
    
    def detect(self, raw_path: str) -> Optional[FocusPointResult]:
        """
        检测对焦点
        
        Args:
            raw_path: RAW 文件路径 (NEF/ARW/CR3/ORF)
            
        Returns:
            FocusPointResult 或 None (无对焦数据)
        """
        # 先读取通用标签确定相机品牌
        exif_data = self._read_exif(raw_path, self.COMMON_TAGS)
        if exif_data is None:
            return None
        
        make = str(exif_data.get('Make', '')).upper()
        
        # 根据品牌选择解析方法
        if 'NIKON' in make:
            return self._detect_nikon(raw_path, exif_data)
        elif 'SONY' in make:
            return self._detect_sony(raw_path, exif_data)
        elif 'CANON' in make:
            return self._detect_canon(raw_path, exif_data)
        elif 'OLYMPUS' in make or 'OM DIGITAL' in make:
            return self._detect_olympus(raw_path, exif_data)
        elif 'FUJIFILM' in make or 'FUJI' in make:
            return self._detect_fujifilm(raw_path, exif_data)
        elif 'PANASONIC' in make:
            return self._detect_panasonic(raw_path, exif_data)
        else:
            return None  # 不支持的相机品牌
    
    def _detect_nikon(self, raw_path: str, common_data: dict) -> Optional[FocusPointResult]:
        """Nikon Z8/Z9 对焦点检测"""
        exif_data = self._read_exif(raw_path, self.NIKON_TAGS)
        if exif_data is None:
            return None
        exif_data.update(common_data)
        
        # 检查是否为 AF 模式
        focus_mode = str(exif_data.get('FocusMode', '')).strip()
        if not focus_mode or 'MF' in focus_mode.upper() or 'MANUAL' in focus_mode.upper():
            return None  # 手动对焦无数据
        
        # 获取对焦坐标
        af_x = exif_data.get('AFAreaXPosition')
        af_y = exif_data.get('AFAreaYPosition')
        af_img_w = exif_data.get('AFImageWidth')
        af_img_h = exif_data.get('AFImageHeight')
        
        if af_x is None or af_y is None or af_img_w is None or af_img_h is None:
            return None  # 缺少关键数据
        
        try:
            raw_x = int(af_x)
            raw_y = int(af_y)
            img_w = int(af_img_w)
            img_h = int(af_img_h)
        except (ValueError, TypeError):
            return None
        
        # 处理 DX 裁切
        raw_x, raw_y, img_w, img_h = self._apply_crop_correction(
            raw_x, raw_y, img_w, img_h, exif_data
        )
        
        # 归一化坐标
        norm_x = raw_x / img_w if img_w > 0 else 0.5
        norm_y = raw_y / img_h if img_h > 0 else 0.5
        
        # 处理竖拍旋转
        orientation = exif_data.get('Orientation', 1)
        norm_x, norm_y = self._apply_orientation_correction(norm_x, norm_y, orientation)
        
        # 获取其他信息
        area_w = int(exif_data.get('AFAreaWidth', 0))
        area_h = int(exif_data.get('AFAreaHeight', 0))
        area_mode = str(exif_data.get('AFAreaMode', 'Unknown'))
        focus_result = exif_data.get('FocusResult', 1)  # Nikon: 1=Focus
        
        return FocusPointResult(
            x=norm_x,
            y=norm_y,
            raw_x=raw_x,
            raw_y=raw_y,
            area_width=area_w,
            area_height=area_h,
            af_mode=focus_mode,
            area_mode=area_mode,
            focus_result=focus_result,
            is_valid=True
        )
    
    def _detect_sony(self, raw_path: str, common_data: dict) -> Optional[FocusPointResult]:
        """Sony A1/A7R5/A7M4 对焦点检测"""
        exif_data = self._read_exif(raw_path, self.SONY_TAGS)
        if exif_data is None:
            return None
        exif_data.update(common_data)
        
        # 检查是否为 AF 模式
        focus_mode = str(exif_data.get('FocusMode', '')).strip()
        # Sony FocusMode: 1=MF, 2=AF-S, 3=AF-C, 4=AF-A 等
        if focus_mode == '1' or 'MF' in focus_mode.upper() or 'MANUAL' in focus_mode.upper():
            return None  # 手动对焦无数据
        
        # Sony FocusLocation 格式: "imgW imgH focusX focusY"
        focus_location = exif_data.get('FocusLocation', '')
        if not focus_location:
            return None
        
        try:
            parts = str(focus_location).split()
            if len(parts) >= 4:
                img_w = int(parts[0])
                img_h = int(parts[1])
                raw_x = int(parts[2])
                raw_y = int(parts[3])
            else:
                return None
        except (ValueError, IndexError):
            return None
        
        # 归一化坐标
        norm_x = raw_x / img_w if img_w > 0 else 0.5
        norm_y = raw_y / img_h if img_h > 0 else 0.5
        
        # 处理竖拍旋转
        orientation = exif_data.get('Orientation', 1)
        norm_x, norm_y = self._apply_orientation_correction(norm_x, norm_y, orientation)
        
        # 获取对焦框大小
        frame_size = exif_data.get('FocusFrameSize', '')
        area_w, area_h = 0, 0
        if frame_size:
            try:
                fs_parts = str(frame_size).split()
                if len(fs_parts) >= 2:
                    area_w = int(fs_parts[0])
                    area_h = int(fs_parts[1])
            except (ValueError, IndexError):
                pass
        
        # Sony 没有 FocusResult 标签，假设 AF 模式下都是合焦的
        area_mode = str(exif_data.get('AFAreaMode', 'Unknown'))
        
        return FocusPointResult(
            x=norm_x,
            y=norm_y,
            raw_x=raw_x,
            raw_y=raw_y,
            area_width=area_w,
            area_height=area_h,
            af_mode=focus_mode,
            area_mode=area_mode,
            focus_result=1,  # 假设 AF 模式下合焦
            is_valid=True
        )
    
    def _detect_canon(self, raw_path: str, common_data: dict) -> Optional[FocusPointResult]:
        """
        Canon EOS R 系列 / DSLR 对焦点检测
        
        Canon 坐标系统: 从图像中心偏移！
        实际坐标 = 中心 + 偏移量
        """
        exif_data = self._read_exif(raw_path, self.CANON_TAGS)
        if exif_data is None:
            return None
        exif_data.update(common_data)
        
        # 检查 AF 模式
        focus_mode = str(exif_data.get('FocusMode', '')).strip()
        if 'MF' in focus_mode.upper() or 'MANUAL' in focus_mode.upper():
            return None
        
        # 获取图像尺寸
        img_w = exif_data.get('AFImageWidth') or exif_data.get('ExifImageWidth')
        img_h = exif_data.get('AFImageHeight') or exif_data.get('ExifImageHeight')
        if img_w is None or img_h is None:
            return None
        img_w, img_h = int(img_w), int(img_h)
        
        # 获取对焦点坐标列表
        x_positions = exif_data.get('AFAreaXPositions', '')
        y_positions = exif_data.get('AFAreaYPositions', '')
        if not x_positions or not y_positions:
            return None
        
        try:
            x_list = [int(x) for x in str(x_positions).split()]
            y_list = [int(y) for y in str(y_positions).split()]
        except (ValueError, AttributeError):
            return None
        
        if not x_list or not y_list:
            return None
        
        # 获取合焦点索引
        af_points_in_focus = exif_data.get('AFPointsInFocus', '')
        focus_indices = []
        if af_points_in_focus:
            try:
                focus_indices = [int(i.strip()) for i in str(af_points_in_focus).split(',') if i.strip().isdigit()]
            except ValueError:
                focus_indices = []
        
        # 如果有合焦点，使用第一个合焦点；否则使用第一个点
        if focus_indices and focus_indices[0] < len(x_list):
            idx = focus_indices[0]
        else:
            idx = 0
        
        # Canon 坐标从中心偏移，需要 +imageWidth/2
        raw_x = img_w // 2 + x_list[idx]
        raw_y = img_h // 2 + y_list[idx]
        
        # 归一化坐标
        norm_x = raw_x / img_w if img_w > 0 else 0.5
        norm_y = raw_y / img_h if img_h > 0 else 0.5
        
        # 处理竖拍旋转
        orientation = exif_data.get('Orientation', 1)
        norm_x, norm_y = self._apply_orientation_correction(norm_x, norm_y, orientation)
        
        # 获取对焦框尺寸
        widths = exif_data.get('AFAreaWidths', '')
        heights = exif_data.get('AFAreaHeights', '')
        area_w, area_h = 0, 0
        try:
            if widths:
                w_list = [int(w) for w in str(widths).split()]
                area_w = w_list[idx] if idx < len(w_list) else w_list[0] if w_list else 0
            if heights:
                h_list = [int(h) for h in str(heights).split()]
                area_h = h_list[idx] if idx < len(h_list) else h_list[0] if h_list else 0
        except (ValueError, IndexError):
            pass
        
        area_mode = str(exif_data.get('AFAreaMode', 'Unknown'))
        
        return FocusPointResult(
            x=norm_x,
            y=norm_y,
            raw_x=raw_x,
            raw_y=raw_y,
            area_width=area_w,
            area_height=area_h,
            af_mode=focus_mode,
            area_mode=area_mode,
            focus_result=1 if focus_indices else 0,  # 有合焦点则合焦
            is_valid=True
        )
    
    def _detect_olympus(self, raw_path: str, common_data: dict) -> Optional[FocusPointResult]:
        """
        Olympus/OM System 对焦点检测
        
        Olympus 坐标系统:
        - AF Point Selected: 归一化坐标 "x y" (0.0-1.0)
        - AF Focus Area: 像素坐标 "x y w h"
        """
        exif_data = self._read_exif(raw_path, self.OLYMPUS_TAGS)
        if exif_data is None:
            return None
        exif_data.update(common_data)
        
        # 检查 AF 模式
        focus_mode = str(exif_data.get('FocusMode', '')).strip()
        if 'MF' in focus_mode.upper() or focus_mode == 'MF; MF':
            return None
        
        # 获取图像尺寸
        img_w = exif_data.get('ExifImageWidth')
        img_h = exif_data.get('ExifImageHeight')
        
        # 尝试方法 1: AF Point Selected (归一化坐标)
        af_point_selected = exif_data.get('AFPointSelected', '')
        if af_point_selected and af_point_selected != '0 0':
            try:
                parts = str(af_point_selected).split()
                if len(parts) >= 2:
                    norm_x = float(parts[0])
                    norm_y = float(parts[1])
                    
                    # 检查是否为百分比格式
                    if '%' in str(af_point_selected):
                        # 格式如 "(50%,50%)"
                        import re
                        match = re.search(r'\((\d+)%,(\d+)', str(af_point_selected))
                        if match:
                            norm_x = int(match.group(1)) / 100
                            norm_y = int(match.group(2)) / 100
                    
                    # 计算像素坐标
                    if img_w and img_h:
                        raw_x = int(norm_x * int(img_w))
                        raw_y = int(norm_y * int(img_h))
                    else:
                        raw_x, raw_y = 0, 0
                    
                    # 处理竖拍旋转
                    orientation = exif_data.get('Orientation', 1)
                    norm_x, norm_y = self._apply_orientation_correction(norm_x, norm_y, orientation)
                    
                    area_mode = str(exif_data.get('AFAreaMode', 'Unknown'))
                    
                    return FocusPointResult(
                        x=norm_x,
                        y=norm_y,
                        raw_x=raw_x,
                        raw_y=raw_y,
                        area_width=0,
                        area_height=0,
                        af_mode=focus_mode,
                        area_mode=area_mode,
                        focus_result=1,
                        is_valid=True
                    )
            except (ValueError, IndexError):
                pass
        
        # 尝试方法 2: AF Focus Area (像素坐标)
        af_focus_area = exif_data.get('AFFocusArea', '')
        af_frame_size = exif_data.get('AFFrameSize', '')
        if af_focus_area and af_frame_size:
            try:
                area_parts = str(af_focus_area).split()
                frame_parts = str(af_frame_size).split()
                
                if len(area_parts) >= 4 and len(frame_parts) >= 2:
                    frame_w = int(frame_parts[0])
                    frame_h = int(frame_parts[1])
                    area_x = int(area_parts[0])
                    area_y = int(area_parts[1])
                    area_w = int(area_parts[2])
                    area_h = int(area_parts[3])
                    
                    # 计算中心点并归一化
                    center_x = area_x + area_w // 2
                    center_y = area_y + area_h // 2
                    norm_x = center_x / frame_w if frame_w > 0 else 0.5
                    norm_y = center_y / frame_h if frame_h > 0 else 0.5
                    
                    # 处理竖拍旋转
                    orientation = exif_data.get('Orientation', 1)
                    norm_x, norm_y = self._apply_orientation_correction(norm_x, norm_y, orientation)
                    
                    area_mode = str(exif_data.get('AFAreaMode', 'Unknown'))
                    
                    return FocusPointResult(
                        x=norm_x,
                        y=norm_y,
                        raw_x=center_x,
                        raw_y=center_y,
                        area_width=area_w,
                        area_height=area_h,
                        af_mode=focus_mode,
                        area_mode=area_mode,
                        focus_result=1,
                        is_valid=True
                    )
            except (ValueError, IndexError):
                pass
        
        return None
    
    def _detect_fujifilm(self, raw_path: str, common_data: dict) -> Optional[FocusPointResult]:
        """
        Fujifilm X/GFX 系列对焦点检测
        
        Fujifilm 坐标系统:
        - Focus Pixel: 像素坐标 "x y"
        """
        exif_data = self._read_exif(raw_path, self.FUJIFILM_TAGS)
        if exif_data is None:
            return None
        exif_data.update(common_data)
        
        # 检查 AF 模式
        focus_mode = str(exif_data.get('FocusMode', '')).strip()
        if 'MF' in focus_mode.upper() or 'MANUAL' in focus_mode.upper():
            return None
        
        # 获取 Focus Pixel
        focus_pixel = exif_data.get('FocusPixel', '')
        if not focus_pixel:
            return None
        
        try:
            parts = str(focus_pixel).split()
            if len(parts) >= 2:
                raw_x = int(parts[0])
                raw_y = int(parts[1])
            else:
                return None
        except (ValueError, IndexError):
            return None
        
        # 获取图像尺寸
        img_w = exif_data.get('ExifImageWidth')
        img_h = exif_data.get('ExifImageHeight')
        if img_w is None or img_h is None:
            return None
        img_w, img_h = int(img_w), int(img_h)
        
        # 归一化坐标
        norm_x = raw_x / img_w if img_w > 0 else 0.5
        norm_y = raw_y / img_h if img_h > 0 else 0.5
        
        # 处理竖拍旋转
        orientation = exif_data.get('Orientation', 1)
        norm_x, norm_y = self._apply_orientation_correction(norm_x, norm_y, orientation)
        
        area_mode = str(exif_data.get('AFAreaMode') or exif_data.get('AFMode', 'Unknown'))
        
        return FocusPointResult(
            x=norm_x,
            y=norm_y,
            raw_x=raw_x,
            raw_y=raw_y,
            area_width=0,
            area_height=0,
            af_mode=focus_mode,
            area_mode=area_mode,
            focus_result=1,
            is_valid=True
        )
    
    def _detect_panasonic(self, raw_path: str, common_data: dict) -> Optional[FocusPointResult]:
        """
        Panasonic LUMIX S/G 系列对焦点检测
        
        Panasonic 坐标系统:
        - AF Point Position: 归一化坐标 "0.5 0.5" (0.0-1.0)
        """
        exif_data = self._read_exif(raw_path, self.PANASONIC_TAGS)
        if exif_data is None:
            return None
        exif_data.update(common_data)
        
        # 检查 AF 模式
        focus_mode = str(exif_data.get('FocusMode', '')).strip()
        if 'MF' in focus_mode.upper() or 'MANUAL' in focus_mode.upper():
            return None
        
        # 获取 AF Point Position (归一化坐标)
        af_point_position = exif_data.get('AFPointPosition', '')
        if not af_point_position:
            return None
        
        # 检查是否为无效值
        if '4.194e' in str(af_point_position):  # Panasonic 的 "未找到" 标记
            return None
        
        try:
            parts = str(af_point_position).split()
            if len(parts) >= 2:
                norm_x = float(parts[0])
                norm_y = float(parts[1])
            else:
                return None
        except (ValueError, IndexError):
            return None
        
        # 获取图像尺寸计算像素坐标
        img_w = exif_data.get('ExifImageWidth')
        img_h = exif_data.get('ExifImageHeight')
        if img_w and img_h:
            raw_x = int(norm_x * int(img_w))
            raw_y = int(norm_y * int(img_h))
        else:
            raw_x, raw_y = 0, 0
        
        # 处理竖拍旋转
        orientation = exif_data.get('Orientation', 1)
        norm_x, norm_y = self._apply_orientation_correction(norm_x, norm_y, orientation)
        
        # 获取对焦框尺寸
        area_w, area_h = 0, 0
        af_area_size = exif_data.get('AFAreaSize', '')
        if af_area_size and img_w and img_h:
            try:
                size_parts = str(af_area_size).split()
                if len(size_parts) >= 2:
                    area_w = int(float(size_parts[0]) * int(img_w))
                    area_h = int(float(size_parts[1]) * int(img_h))
            except (ValueError, IndexError):
                pass
        
        area_mode = str(exif_data.get('AFAreaMode', 'Unknown'))
        
        return FocusPointResult(
            x=norm_x,
            y=norm_y,
            raw_x=raw_x,
            raw_y=raw_y,
            area_width=area_w,
            area_height=area_h,
            af_mode=focus_mode,
            area_mode=area_mode,
            focus_result=1,
            is_valid=True
        )
    
    def _read_exif(self, file_path: str, tags: list) -> Optional[dict]:
        """读取指定的 EXIF 标签"""
        cmd = [self.exiftool_path, '-j', '-n']
        for tag in tags:
            cmd.append(f'-{tag}')
        cmd.append(file_path)
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return None
            data = json.loads(result.stdout)
            return data[0] if data else None
        except Exception:
            return None
    
    def _apply_crop_correction(
        self, 
        x: int, 
        y: int, 
        img_w: int, 
        img_h: int, 
        exif_data: dict
    ) -> Tuple[int, int, int, int]:
        """
        处理 DX 裁切修正
        
        CropArea 格式: "left top width height"
        """
        crop_area = exif_data.get('CropArea', '')
        if not crop_area:
            return x, y, img_w, img_h
        
        try:
            parts = str(crop_area).split()
            if len(parts) >= 4:
                crop_left = int(parts[0])
                crop_top = int(parts[1])
                crop_w = int(parts[2])
                crop_h = int(parts[3])
                
                # 如果裁切区域与图像尺寸差异大于 5%，才进行修正
                if abs(crop_w - img_w) > img_w * 0.05 or abs(crop_h - img_h) > img_h * 0.05:
                    # DX 模式：坐标需要减去偏移量
                    x = x - crop_left
                    y = y - crop_top
                    img_w = crop_w
                    img_h = crop_h
        except (ValueError, IndexError):
            pass
        
        return x, y, img_w, img_h
    
    def _apply_orientation_correction(
        self, 
        x: float, 
        y: float, 
        orientation: int
    ) -> Tuple[float, float]:
        """
        处理竖拍旋转
        
        Orientation 值:
        - 1: Horizontal (正常)
        - 6: Rotate 90 CW (顺时针旋转 90°)
        - 8: Rotate 270 CW (顺时针旋转 270°)
        """
        if orientation == 6:
            # 顺时针 90°: (x, y) -> (y, 1-x)
            return y, 1.0 - x
        elif orientation == 8:
            # 顺时针 270° (逆时针 90°): (x, y) -> (1-y, x)
            return 1.0 - y, x
        else:
            return x, y


def verify_focus_in_bbox(
    focus: Optional[FocusPointResult], 
    bbox: Tuple[int, int, int, int],
    img_dims: Tuple[int, int],
    seg_mask: np.ndarray = None,
    head_center: Tuple[int, int] = None,
    head_radius: int = None,
) -> float:
    """
    验证对焦点位置，返回锐度权重因子
    
    采用 4 层检测策略：
    1. 头部区域内 → 1.2 (最佳)
    2. SEG 掩码内 → 1.0 (正常)
    3. BBox 内 → 0.8 (轻微惩罚)
    4. BBox 外 → 0.6 (较大惩罚)
    
    Args:
        focus: 对焦点检测结果
        bbox: YOLO 检测的 Bounding Box (x, y, w, h) - 左上角坐标
        img_dims: 图像尺寸 (width, height)
        seg_mask: 分割掩码（原图尺寸），可选
        head_center: 头部圆心 (x, y) 像素坐标，可选
        head_radius: 头部区域半径（像素），可选
        
    Returns:
        权重因子 (乘以锐度值):
        - 1.2: 对焦点在头部区域内
        - 1.0: 对焦点在 SEG 掩码内（但不在头部）
        - 0.8: 对焦点在 BBox 内（但不在 SEG）
        - 0.6: 对焦点在 BBox 外
        - 1.0: 无对焦数据
    """
    if focus is None or not focus.is_valid:
        return 1.0  # 无数据，不影响评分
    
    if not focus.is_focused:
        return 0.8  # 未合焦，轻微惩罚
    
    # 转换对焦点为像素坐标
    img_w, img_h = img_dims
    focus_px = (int(focus.x * img_w), int(focus.y * img_h))
    
    # 检查是否在头部区域内
    if head_center is not None and head_radius is not None:
        dist = np.sqrt((focus_px[0] - head_center[0])**2 + (focus_px[1] - head_center[1])**2)
        if dist <= head_radius:
            return 1.2  # 对焦在头部区域，最佳
    
    # 检查是否在 SEG 掩码内
    if seg_mask is not None:
        # 确保坐标在图像范围内
        fx, fy = focus_px
        if 0 <= fy < seg_mask.shape[0] and 0 <= fx < seg_mask.shape[1]:
            if seg_mask[fy, fx] > 0:
                return 1.0  # 对焦在鸟身上（但不在头部），正常
    
    # 检查是否在 BBox 内
    bx, by, bw, bh = bbox
    in_bbox = (bx <= focus_px[0] <= bx + bw) and (by <= focus_px[1] <= by + bh)
    
    if in_bbox:
        return 0.8  # 对焦在 BBox 内但不在鸟身上（背景），轻微惩罚
    else:
        return 0.6  # 对焦完全在 BBox 外，较大惩罚


# 全局单例
_focus_detector: Optional[FocusPointDetector] = None


def get_focus_detector() -> FocusPointDetector:
    """获取对焦点检测器单例"""
    global _focus_detector
    if _focus_detector is None:
        _focus_detector = FocusPointDetector()
    return _focus_detector
