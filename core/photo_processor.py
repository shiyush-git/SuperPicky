#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Core Photo Processor - 核心照片处理器
提取自 GUI 和 CLI 的共享业务逻辑

职责：
- 文件扫描和 RAW 转换
- 调用 AI 检测
- 调用 RatingEngine 评分
- 写入 EXIF 元数据
- 文件移动和清理
"""

import os
import time
import json
import shutil
import numpy as np
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Callable, Tuple
from dataclasses import dataclass, field
from datetime import datetime

# 现有模块
from find_bird_util import raw_to_jpeg
from ai_model import load_yolo_model, detect_and_draw_birds
from exiftool_manager import get_exiftool_manager
from advanced_config import get_advanced_config
from core.rating_engine import RatingEngine, create_rating_engine_from_config
from core.keypoint_detector import KeypointDetector, get_keypoint_detector
from core.flight_detector import FlightDetector, get_flight_detector, FlightResult
from core.exposure_detector import ExposureDetector, get_exposure_detector, ExposureResult
from core.focus_point_detector import get_focus_detector, verify_focus_in_bbox

from constants import RATING_FOLDER_NAMES, RAW_EXTENSIONS, JPG_EXTENSIONS


@dataclass
class ProcessingSettings:
    """处理参数配置"""
    ai_confidence: int = 50
    sharpness_threshold: int = 400   # 头部区域锐度达标阈值 (200-600)
    nima_threshold: float = 5.0      # V3.9.4: TOPIQ 美学达标阈值，与 GUI 滑块默认值一致
    save_crop: bool = False
    normalization_mode: str = 'log_compression'  # 默认使用log_compression，与GUI一致
    detect_flight: bool = True       # V3.4: 飞版检测开关
    detect_exposure: bool = True     # V3.9.4: 曝光检测开关（默认开启，与 GUI 一致）
    exposure_threshold: float = 0.10 # V3.8: 曝光阈值 (0.05-0.20)
    detect_burst: bool = True        # V4.0: 连拍检测开关（默认开启）


@dataclass
class ProcessingCallbacks:
    """回调函数（用于进度更新和日志输出）"""
    log: Optional[Callable[[str, str], None]] = None
    progress: Optional[Callable[[int], None]] = None


@dataclass
class ProcessingResult:
    """处理结果数据"""
    stats: Dict[str, any] = field(default_factory=dict)
    file_ratings: Dict[str, int] = field(default_factory=dict)
    star_3_photos: List[Dict] = field(default_factory=list)
    total_time: float = 0.0
    avg_time: float = 0.0


class PhotoProcessor:
    """
    核心照片处理器
    
    封装所有业务逻辑，GUI 和 CLI 都调用这个类
    """
    
    def __init__(
        self,
        dir_path: str,
        settings: ProcessingSettings,
        callbacks: Optional[ProcessingCallbacks] = None
    ):
        """
        初始化处理器
        
        Args:
            dir_path: 处理目录路径
            settings: 处理参数
            callbacks: 回调函数（进度、日志）
        """
        self.dir_path = dir_path
        self.settings = settings
        self.callbacks = callbacks or ProcessingCallbacks()
        self.config = get_advanced_config()
        
        # 初始化评分引擎
        self.rating_engine = create_rating_engine_from_config(self.config)
        # 使用 UI 设置更新达标阈值
        self.rating_engine.update_thresholds(
            sharpness_threshold=settings.sharpness_threshold,
            nima_threshold=settings.nima_threshold
        )
        
        # DEBUG: 输出参数
        self._log(f"\n🔍 DEBUG - 处理参数:")
        self._log(f"  📊 AI置信度: {settings.ai_confidence}")
        self._log(f"  📏 锐度阈值: {settings.sharpness_threshold}")
        self._log(f"  🎨 NIMA阈值: {settings.nima_threshold}")
        self._log(f"  🔧 归一化模式: {settings.normalization_mode}")
        self._log(f"  🦅 飞鸟检测: {'开启' if settings.detect_flight else '关闭'}")
        self._log(f"  📸 曝光检测: {'开启' if settings.detect_exposure else '关闭'}")
        self._log(f"  ⚙️  高级配置 - 最低锐度: {self.config.min_sharpness}")
        self._log(f"  ⚙️  高级配置 - 最低美学: {self.config.min_nima}\n")
        
        # 统计数据（支持 0/1/2/3 星）
        self.stats = {
            'total': 0,
            'star_3': 0,
            'picked': 0,
            'star_2': 0,
            'star_1': 0,  # 普通照片（合格）
            'star_0': 0,  # 普通照片（问题）
            'no_bird': 0,
            'flying': 0,  # V3.6: 飞鸟照片计数
            'exposure_issue': 0,  # V3.8: 曝光问题计数
            'start_time': 0,
            'end_time': 0,
            'total_time': 0,
            'avg_time': 0
        }
        
        # 内部状态
        self.file_ratings = {}
        self.star2_reasons = {}  # 记录2星原因: 'sharpness' 或 'nima'
        self.star_3_photos = []
    
    def _log(self, msg: str, level: str = "info"):
        """内部日志方法"""
        if self.callbacks.log:
            self.callbacks.log(msg, level)
    
    def _progress(self, percent: int):
        """内部进度更新"""
        if self.callbacks.progress:
            self.callbacks.progress(percent)
    
    def process(
        self,
        organize_files: bool = True,
        cleanup_temp: bool = True
    ) -> ProcessingResult:
        """
        主处理流程
        
        Args:
            organize_files: 是否移动文件到分类文件夹
            cleanup_temp: 是否清理临时JPG文件
            
        Returns:
            ProcessingResult 包含统计数据和处理结果
        """
        start_time = time.time()
        self.stats['start_time'] = start_time
        
        # 阶段1: 文件扫描
        raw_dict, jpg_dict, files_tbr = self._scan_files()
        
        # 阶段2: RAW转换
        raw_files_to_convert = self._identify_raws_to_convert(raw_dict, jpg_dict, files_tbr)
        if raw_files_to_convert:
            self._convert_raws(raw_files_to_convert, files_tbr)
        
        # 阶段3: AI检测与评分
        self._process_images(files_tbr, raw_dict)
        
        # 阶段4: 精选旗标计算
        self._calculate_picked_flags()
        
        # 阶段5: 文件组织
        if organize_files:
            self._move_files_to_rating_folders(raw_dict)
        
        # 阶段6: 清理临时文件
        if cleanup_temp:
            self._cleanup_temp_files(files_tbr, raw_dict)
        
        # 记录结束时间
        end_time = time.time()
        self.stats['end_time'] = end_time
        self.stats['total_time'] = end_time - start_time
        self.stats['avg_time'] = (
            self.stats['total_time'] / self.stats['total']
            if self.stats['total'] > 0 else 0
        )
        
        return ProcessingResult(
            stats=self.stats.copy(),
            file_ratings=self.file_ratings.copy(),
            star_3_photos=self.star_3_photos.copy(),
            total_time=self.stats['total_time'],
            avg_time=self.stats['avg_time']
        )
    
    def _scan_files(self) -> Tuple[dict, dict, list]:
        """扫描目录文件"""
        scan_start = time.time()
        
        raw_dict = {}
        jpg_dict = {}
        files_tbr = []
        
        for filename in os.listdir(self.dir_path):
            if filename.startswith('.'):
                continue

            
            file_prefix, file_ext = os.path.splitext(filename)
            if file_ext.lower() in RAW_EXTENSIONS:
                raw_dict[file_prefix] = file_ext
            if file_ext.lower() in JPG_EXTENSIONS:
                jpg_dict[file_prefix] = file_ext
                files_tbr.append(filename)
        
        scan_time = (time.time() - scan_start) * 1000
        self._log(f"⏱️  文件扫描耗时: {scan_time:.1f}ms")
        
        return raw_dict, jpg_dict, files_tbr
    
    def _identify_raws_to_convert(self, raw_dict, jpg_dict, files_tbr):
        """识别需要转换的RAW文件"""
        raw_files_to_convert = []
        
        for key, value in raw_dict.items():
            if key in jpg_dict:
                jpg_dict.pop(key)
                continue
            else:
                raw_file_path = os.path.join(self.dir_path, key + value)
                raw_files_to_convert.append((key, raw_file_path))
        
        return raw_files_to_convert
    
    def _convert_raws(self, raw_files_to_convert, files_tbr):
        """并行转换RAW文件"""
        raw_start = time.time()
        import multiprocessing
        max_workers = min(4, multiprocessing.cpu_count())
        
        self._log(f"🔄 开始并行转换 {len(raw_files_to_convert)} 个RAW文件({max_workers}线程)...")
        
        def convert_single(args):
            key, raw_path = args
            try:
                raw_to_jpeg(raw_path)
                return (key, True, None)
            except Exception as e:
                return (key, False, str(e))
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_raw = {
                executor.submit(convert_single, args): args 
                for args in raw_files_to_convert
            }
            converted_count = 0
            
            for future in as_completed(future_to_raw):
                key, success, error = future.result()
                if success:
                    files_tbr.append(key + ".jpg")
                    converted_count += 1
                    if converted_count % 5 == 0 or converted_count == len(raw_files_to_convert):
                        self._log(f"  ✅ 已转换 {converted_count}/{len(raw_files_to_convert)} 张")
                else:
                    self._log(f"  ❌ 转换失败: {key} ({error})", "error")
        
        raw_time = time.time() - raw_start
        avg_time = raw_time / len(raw_files_to_convert) if len(raw_files_to_convert) > 0 else 0
        # 智能选择时间单位：小于1秒显示毫秒
        if avg_time < 1:
            self._log(f"⏱️  RAW转换耗时: {raw_time:.1f}秒 (平均 {avg_time*1000:.0f}ms/张)\n")
        else:
            self._log(f"⏱️  RAW转换耗时: {raw_time:.1f}秒 (平均 {avg_time:.1f}秒/张)\n")
    
    def _process_images(self, files_tbr, raw_dict):
        """处理所有图片 - AI检测、关键点检测与评分"""
        # 加载模型
        model_start = time.time()
        self._log("🤖 加载AI模型...")
        model = load_yolo_model()
        model_time = (time.time() - model_start) * 1000
        self._log(f"⏱️  模型加载耗时: {model_time:.0f}ms")
        
        # 加载关键点检测模型
        self._log("👁️  加载关键点模型...")
        keypoint_detector = get_keypoint_detector()
        try:
            keypoint_detector.load_model()
            self._log("✅ 关键点模型加载成功")
            use_keypoints = True
        except FileNotFoundError:
            self._log("⚠️  关键点模型未找到，使用传统锐度计算", "warning")
            use_keypoints = False
        
        # V3.4: 加载飞版检测模型
        use_flight = False
        flight_detector = None
        if self.settings.detect_flight:
            self._log("🦅 加载飞版检测模型...")
            flight_detector = get_flight_detector()
            try:
                flight_detector.load_model()
                self._log("✅ 飞版检测模型加载成功")
                use_flight = True
            except FileNotFoundError:
                self._log("⚠️  飞版检测模型未找到，跳过飞版检测", "warning")
                use_flight = False
        
        total_files = len(files_tbr)
        self._log(f"📁 共 {total_files} 个文件待处理\n")
        
        exiftool_mgr = get_exiftool_manager()
        
        # UI设置转为列表格式
        ui_settings = [
            self.settings.ai_confidence,
            self.settings.sharpness_threshold,
            self.settings.nima_threshold,
            self.settings.save_crop,
            self.settings.normalization_mode
        ]
        
        ai_total_start = time.time()
        
        for i, filename in enumerate(files_tbr, 1):
            # 记录每张照片的开始时间
            photo_start_time = time.time()
            
            filepath = os.path.join(self.dir_path, filename)
            file_prefix, _ = os.path.splitext(filename)
            
            # 更新进度
            should_update = (i % 5 == 0 or i == total_files or i == 1)
            if should_update:
                progress = int((i / total_files) * 100)
                self._progress(progress)
            
            # 优化流程：YOLO → 关键点检测(在crop上) → 条件NIMA
            # Phase 1: 先做YOLO检测（跳过NIMA），获取鸟的位置和bbox
            try:
                result = detect_and_draw_birds(
                    filepath, model, None, self.dir_path, ui_settings, None, skip_nima=True
                )
                if result is None:
                    self._log(f"  ⚠️  无法处理(AI推理失败)", "error")
                    continue
            except Exception as e:
                self._log(f"  ❌ 处理异常: {e}", "error")
                continue
            
            # 解构 AI 结果 (包含bbox, 图像尺寸, 分割掩码) - V3.2移除BRISQUE
            detected, _, confidence, sharpness, _, bird_bbox, img_dims, bird_mask = result
            
            # V4.1: 早期退出 - 无鸟或置信度低，跳过所有后续检测
            if not detected or (detected and confidence < 0.5):
                photo_time_ms = (time.time() - photo_start_time) * 1000
                
                if not detected:
                    rating_value = -1
                    reason = "未检测到鸟类"
                else:
                    rating_value = 0
                    reason = f"置信度低({confidence:.0%})"
                
                # 简化日志
                self._log_photo_result_simple(i, total_files, filename, rating_value, reason, photo_time_ms, False, False, None)
                
                # 记录统计
                self._update_stats(rating_value, False, False)
                
                # 记录评分（用于文件移动）
                self.file_ratings[file_prefix] = rating_value
                
                # 写入简化 EXIF
                if file_prefix in raw_dict:
                    raw_extension = raw_dict[file_prefix]
                    target_file_path = os.path.join(self.dir_path, file_prefix + raw_extension)
                    if os.path.exists(target_file_path):
                        single_batch = [{
                            'file': target_file_path,
                            'rating': 0 if rating_value >= 0 else 0,  # -1星也写0
                            'pick': -1 if rating_value == -1 else 0,
                            'sharpness': None,
                            'nima_score': None,
                            'label': None,
                            'focus_status': None,
                            'caption': f"{rating_value}星 | {reason}",
                        }]
                        exiftool_mgr.batch_set_metadata(single_batch)
                
                continue  # 跳过后续所有检测
            
            # Phase 2: 关键点检测（在裁剪区域上执行，更准确）
            all_keypoints_hidden = False
            both_eyes_hidden = False  # 保留用于日志/调试
            best_eye_visibility = 0.0  # V3.8: 眼睛最高置信度，用于封顶逻辑
            head_sharpness = 0.0
            has_visible_eye = False
            has_visible_beak = False
            left_eye_vis = 0.0
            right_eye_vis = 0.0
            beak_vis = 0.0
            
            # V3.9: 头部区域信息（用于对焦验证）
            head_center_orig = None
            head_radius_val = None
            
            # V3.9.4: 原图尺寸和裁剪偏移（用于对焦点坐标转换）
            # 这些变量必须在循环开始时初始化，确保后续代码可用
            w_orig, h_orig = None, None
            x_orig, y_orig = 0, 0  # 裁剪偏移默认为 0
            
            # V3.2优化: 只读取原图一次，在关键点检测和NIMA计算中复用
            orig_img = None  # 原图缓存
            bird_crop_bgr = None  # 裁剪区域缓存（BGR）
            bird_crop_mask = None # 裁剪区域掩码缓存
            bird_mask_orig = None  # V3.9: 原图尺寸的分割掩码（用于对焦验证）
            
            if use_keypoints and detected and bird_bbox is not None and img_dims is not None:
                try:
                    import cv2
                    orig_img = cv2.imread(filepath)  # 只读取一次!
                    if orig_img is not None:
                        h_orig, w_orig = orig_img.shape[:2]
                        # 获取YOLO处理时的图像尺寸
                        w_resized, h_resized = img_dims
                        
                        # 计算缩放比例：原图 / 缩放图
                        scale_x = w_orig / w_resized
                        scale_y = h_orig / h_resized
                        
                        # 将bbox从缩放尺寸转换到原图尺寸
                        x, y, w, h = bird_bbox
                        x_orig = int(x * scale_x)
                        y_orig = int(y * scale_y)
                        w_orig_box = int(w * scale_x)
                        h_orig_box = int(h * scale_y)
                        
                        # 确保边界有效
                        x_orig = max(0, min(x_orig, w_orig - 1))
                        y_orig = max(0, min(y_orig, h_orig - 1))
                        w_orig_box = min(w_orig_box, w_orig - x_orig)
                        h_orig_box = min(h_orig_box, h_orig - y_orig)
                        
                        # 裁剪鸟的区域（保存BGR版本供NIMA使用）
                        bird_crop_bgr = orig_img[y_orig:y_orig+h_orig_box, x_orig:x_orig+w_orig_box]
                        
                        # 同样裁剪 mask (如果存在)
                        if bird_mask is not None:
                            # 缩放 mask 到原图尺寸 (Mask是整图的)
                            # bird_mask 是 (h_resized, w_resized)，需要放大到 (h_orig, w_orig)
                            if bird_mask.shape[:2] != (h_orig, w_orig):
                                # 使用最近邻插值保持二值特性
                                bird_mask_orig = cv2.resize(bird_mask, (w_orig, h_orig), interpolation=cv2.INTER_NEAREST)
                            else:
                                bird_mask_orig = bird_mask
                                
                            bird_crop_mask = bird_mask_orig[y_orig:y_orig+h_orig_box, x_orig:x_orig+w_orig_box]
                        
                        if bird_crop_bgr.size > 0:
                            crop_rgb = cv2.cvtColor(bird_crop_bgr, cv2.COLOR_BGR2RGB)
                            # 在裁剪区域上进行关键点检测，传入分割掩码
                            kp_result = keypoint_detector.detect(
                                crop_rgb, 
                                box=(x_orig, y_orig, w_orig_box, h_orig_box),
                                seg_mask=bird_crop_mask  # 传入分割掩码
                            )
                            if kp_result is not None:
                                both_eyes_hidden = kp_result.both_eyes_hidden  # 保留兼容
                                all_keypoints_hidden = kp_result.all_keypoints_hidden  # 新属性
                                best_eye_visibility = kp_result.best_eye_visibility  # V3.8
                                has_visible_eye = kp_result.visible_eye is not None
                                has_visible_beak = kp_result.beak_vis >= 0.3  # V3.8: 降低到 0.3
                                left_eye_vis = kp_result.left_eye_vis
                                right_eye_vis = kp_result.right_eye_vis
                                beak_vis = kp_result.beak_vis
                                head_sharpness = kp_result.head_sharpness
                                
                                # V3.9: 计算头部区域中心和半径（用于对焦验证）
                                ch, cw = bird_crop_bgr.shape[:2]
                                # 选择更可见的眼睛作为头部中心
                                if left_eye_vis >= right_eye_vis and left_eye_vis >= 0.3:
                                    eye_px = (int(kp_result.left_eye[0] * cw), int(kp_result.left_eye[1] * ch))
                                elif right_eye_vis >= 0.3:
                                    eye_px = (int(kp_result.right_eye[0] * cw), int(kp_result.right_eye[1] * ch))
                                else:
                                    eye_px = None
                                
                                if eye_px is not None:
                                    # 转换到原图坐标
                                    head_center_orig = (eye_px[0] + x_orig, eye_px[1] + y_orig)
                                    # 计算半径
                                    beak_px = (int(kp_result.beak[0] * cw), int(kp_result.beak[1] * ch))
                                    if beak_vis >= 0.3:
                                        import math
                                        dist = math.sqrt((eye_px[0] - beak_px[0])**2 + (eye_px[1] - beak_px[1])**2)
                                        head_radius_val = int(dist * 1.2)
                                    else:
                                        head_radius_val = int(max(cw, ch) * 0.15)
                                    head_radius_val = max(20, min(head_radius_val, min(cw, ch) // 2))
                except Exception as e:
                    self._log(f"  ⚠️ 关键点检测异常: {e}", "warning")
                    # import traceback
                    # self._log(traceback.format_exc(), "error")
                    pass
            
            # Phase 3: 根据关键点可见性决定是否计算TOPIQ
            # V4.0: 眼睛可见度 < 30% 时也跳过 TOPIQ（节省时间）
            topiq = None
            if detected and not all_keypoints_hidden and best_eye_visibility >= 0.3:
                # 双眼可见，需要计算NIMA以进行星级判定
                try:
                    from iqa_scorer import get_iqa_scorer
                    import time as time_module
                    
                    step_start = time_module.time()
                    scorer = get_iqa_scorer(device='mps')
                    
                    # V3.7: 使用全图而非裁剪图进行TOPIQ美学评分
                    # 全图评分 + 头部锐度阈值 是更好的组合：
                    # - 全图评分评估整体画面构图和美感
                    # - 头部锐度阈值确保鸟本身足够清晰
                    topiq = scorer.calculate_nima(filepath)
                    
                    topiq_time = (time_module.time() - step_start) * 1000
                except Exception as e:
                    pass  # V3.3: 简化日志，静默 TOPIQ 计算失败
            # V3.8: 移除跳过日志，改用 all_keypoints_hidden 后跳过的情况会少很多
            
            # Phase 4: V3.4 飞版检测（在鸟的裁剪区域上执行）
            is_flying = False
            flight_confidence = 0.0
            if use_flight and detected and bird_crop_bgr is not None and bird_crop_bgr.size > 0:
                try:
                    flight_result = flight_detector.detect(bird_crop_bgr)
                    is_flying = flight_result.is_flying
                    flight_confidence = flight_result.confidence
                    # DEBUG: 输出飞版检测结果
                    # self._log(f"  🦅 飞版检测: is_flying={is_flying}, conf={flight_confidence:.2f}")
                except Exception as e:
                    self._log(f"  ⚠️ 飞版检测异常: {e}", "warning")
            
            # Phase 5: V3.8 曝光检测（在鸟的裁剪区域上执行）
            is_overexposed = False
            is_underexposed = False
            if self.settings.detect_exposure and detected and bird_crop_bgr is not None and bird_crop_bgr.size > 0:
                try:
                    exposure_detector = get_exposure_detector()
                    exposure_result = exposure_detector.detect(
                        bird_crop_bgr, 
                        threshold=self.settings.exposure_threshold
                    )
                    is_overexposed = exposure_result.is_overexposed
                    is_underexposed = exposure_result.is_underexposed
                except Exception as e:
                    pass  # 曝光检测失败不影响处理
            
            # V3.8: 飞版加成（仅当 confidence >= 0.5 且 is_flying 时）
            # 锐度+100，美学+0.5，加成后的值用于评分
            rating_sharpness = head_sharpness
            rating_topiq = topiq
            if is_flying and confidence >= 0.5:
                rating_sharpness = head_sharpness + 100
                if topiq is not None:
                    rating_topiq = topiq + 0.5
            
            # V4.0 优化: 先计算初步评分（不考虑对焦），只对 1 星以上做对焦检测
            # 这样 0 星和 -1 星照片不需要调用 exiftool，节省大量时间
            preliminary_result = self.rating_engine.calculate(
                detected=detected,
                confidence=confidence,
                sharpness=head_sharpness,   # V4.0: 原始锐度（飞鸟加成在引擎内）
                topiq=topiq,                # V4.0: 原始美学（飞鸟加成在引擎内）
                all_keypoints_hidden=all_keypoints_hidden,
                best_eye_visibility=best_eye_visibility,
                is_overexposed=is_overexposed,
                is_underexposed=is_underexposed,
                focus_sharpness_weight=1.0,  # 初步评分不考虑对焦
                focus_topiq_weight=1.0,
                is_flying=False,             # 初步评分不考虑飞鸟加成
            )
            
            # Phase 6: V4.0 对焦点验证
            # 4 层检测返回两个权重: 锐度权重 + 美学权重
            focus_sharpness_weight = 1.0  # 默认无影响
            focus_topiq_weight = 1.0      # 默认无影响
            focus_x, focus_y = None, None
            focus_data_available = False  # V3.9.3: 标记是否有对焦点数据
            focus_result = None           # V3.9.3: 保存对焦检测结果用于调试图
            
            # V3.9.3: 对焦点坐标获取（始终执行，用于调试图显示）
            # 即使是 0 星照片，也需要在调试图中显示对焦点位置
            if detected and bird_bbox is not None and img_dims is not None:
                if file_prefix in raw_dict:
                    raw_ext = raw_dict[file_prefix]
                    raw_path = os.path.join(self.dir_path, file_prefix + raw_ext)
                    # Nikon, Sony, Canon, Olympus, Fujifilm, Panasonic 全支持
                    if raw_ext.lower() in ['.nef', '.nrw', '.arw', '.cr3', '.cr2', '.orf', '.raf', '.rw2']:
                        try:
                            focus_detector = get_focus_detector()
                            focus_result = focus_detector.detect(raw_path)
                            if focus_result is not None:
                                focus_data_available = True
                                focus_x, focus_y = focus_result.x, focus_result.y
                        except Exception as e:
                            pass  # 对焦检测失败不影响处理
            
            # V4.0: 对焦权重计算（仅对 1 星以上照片，节省时间）
            if preliminary_result.rating >= 1:
                if focus_data_available and focus_result is not None:
                    # V3.9.4 修复：使用原图尺寸而非 resize 后的 img_dims
                    # 如果 w_orig/h_orig 为 None，使用 img_dims 作为后备
                    if w_orig is not None and h_orig is not None:
                        orig_dims = (w_orig, h_orig)
                    else:
                        orig_dims = img_dims
                    
                    # V3.9.3: 修复 BBox 坐标系不匹配 bug
                    if img_dims is not None and bird_bbox is not None:
                        scale_x = orig_dims[0] / img_dims[0]
                        scale_y = orig_dims[1] / img_dims[1]
                        bx, by, bw, bh = bird_bbox
                        bird_bbox_orig = (
                            int(bx * scale_x),
                            int(by * scale_y),
                            int(bw * scale_x),
                            int(bh * scale_y)
                        )
                    else:
                        bird_bbox_orig = bird_bbox
                    
                    # V4.0: 返回元组 (锐度权重, 美学权重)
                    focus_sharpness_weight, focus_topiq_weight = verify_focus_in_bbox(
                        focus_result, 
                        bird_bbox_orig,
                        orig_dims,
                        seg_mask=bird_mask_orig,
                        head_center=head_center_orig,
                        head_radius=head_radius_val,
                    )
                elif file_prefix in raw_dict:
                    # V3.9.3: 支持对焦检测的 RAW 文件但无法获取对焦点数据
                    raw_ext = raw_dict[file_prefix]
                    if raw_ext.lower() in ['.nef', '.nrw', '.arw', '.cr3', '.cr2', '.orf', '.raf', '.rw2']:
                        # 检查是否是手动对焦模式
                        is_manual_focus = False
                        try:
                            import subprocess
                            focus_detector = get_focus_detector()
                            exiftool_path = focus_detector._get_exiftool_path()
                            raw_path = os.path.join(self.dir_path, file_prefix + raw_ext)
                            # V3.9.4: 在 Windows 上隐藏控制台窗口
                            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
                            result = subprocess.run(
                                [exiftool_path, '-charset', 'utf8', '-FocusMode', '-s', '-s', '-s', raw_path],
                                capture_output=True, 
                                text=True, 
                                encoding='utf-8',
                                timeout=5,
                                creationflags=creationflags
                            )
                            focus_mode = result.stdout.strip().lower()
                            if 'manual' in focus_mode or focus_mode == 'mf' or focus_mode == 'm':
                                is_manual_focus = True
                        except:
                            pass
                        
                        if is_manual_focus:
                            focus_sharpness_weight = 1.0
                            focus_topiq_weight = 1.0
                        else:
                            focus_sharpness_weight = 0.7
                            focus_topiq_weight = 0.9
            
            # V4.0: 最终评分计算（传入对焦权重和飞鸟状态）
            # 注意: 现在总是重新计算，因为需要传入 is_flying 参数
            rating_result = self.rating_engine.calculate(
                detected=detected,
                confidence=confidence,
                sharpness=head_sharpness,  # V4.0: 使用原始锐度，权重在引擎内应用
                topiq=topiq,              # V4.0: 使用原始美学，权重在引擎内应用
                all_keypoints_hidden=all_keypoints_hidden,
                best_eye_visibility=best_eye_visibility,
                is_overexposed=is_overexposed,
                is_underexposed=is_underexposed,
                focus_sharpness_weight=focus_sharpness_weight,  # V4.0: 锐度权重
                focus_topiq_weight=focus_topiq_weight,          # V4.0: 美学权重
                is_flying=is_flying,                            # V4.0: 飞鸟乘法加成
            )
            
            rating_value = rating_result.rating
            pick = rating_result.pick
            reason = rating_result.reason
            
            # V4.0: 根据 focus_sharpness_weight 计算对焦状态文本
            # 只有检测到鸟才设置对焦状态，避免无鸟照片也写入
            focus_status = None
            focus_status_en = None  # 英文版本用于调试图（避免中文字体问题）
            if detected:  # 只有检测到鸟才计算对焦状态
                if focus_sharpness_weight > 1.0:
                    focus_status = "精焦"
                    focus_status_en = "BEST"
                elif focus_sharpness_weight >= 0.9:  # V3.9.3: 鸟身也算合焦
                    focus_status = "合焦"
                    focus_status_en = "GOOD"
                elif focus_sharpness_weight >= 0.7:
                    focus_status = "失焦"
                    focus_status_en = "BAD"
                elif focus_sharpness_weight < 0.7:
                    focus_status = "脱焦"
                    focus_status_en = "WORST"
            
            # V3.9: 生成调试可视化图（仅对有鸟的照片）
            if detected and bird_crop_bgr is not None:
                # 计算裁剪区域内的坐标
                head_center_crop = None
                if head_center_orig is not None:
                    # 转换到裁剪区域坐标
                    head_center_crop = (head_center_orig[0] - x_orig, head_center_orig[1] - y_orig)
                
                focus_point_crop = None
                if focus_x is not None and focus_y is not None:
                    # V3.9.4: 对焦点从归一化坐标转换为裁剪区域坐标
                    # 使用 w_orig, h_orig（优先）或 bird_crop_bgr 尺寸 + 偏移（后备）
                    img_w_for_focus = w_orig
                    img_h_for_focus = h_orig
                    
                    # 如果原图尺寸未知，尝试从裁剪图推算（不太准确但总比没有好）
                    if img_w_for_focus is None or img_h_for_focus is None:
                        if img_dims is not None:
                            # 使用 YOLO resize 的尺寸 + 缩放比例
                            w_resized, h_resized = img_dims
                            if bird_crop_bgr is not None:
                                ch, cw = bird_crop_bgr.shape[:2]
                                # 估算原图尺寸（使用 bbox 比例）
                                if bird_bbox is not None:
                                    bx, by, bw, bh = bird_bbox
                                    scale_x = cw / bw if bw > 0 else 1
                                    scale_y = ch / bh if bh > 0 else 1
                                    img_w_for_focus = int(w_resized * scale_x)
                                    img_h_for_focus = int(h_resized * scale_y)
                    
                    if img_w_for_focus is not None and img_h_for_focus is not None:
                        fx_px = int(focus_x * img_w_for_focus) - x_orig
                        fy_px = int(focus_y * img_h_for_focus) - y_orig
                        focus_point_crop = (fx_px, fy_px)
                
                try:
                    self._save_debug_crop(
                        filename,
                        bird_crop_bgr,
                        bird_crop_mask if 'bird_crop_mask' in dir() else None,
                        head_center_crop,
                        head_radius_val,
                        focus_point_crop,
                        focus_status_en  # 使用英文标签
                    )
                except Exception as e:
                    pass  # 调试图生成失败不影响主流程
            
            # 计算真正总耗时并输出简化日志
            photo_time_ms = (time.time() - photo_start_time) * 1000
            has_exposure_issue = is_overexposed or is_underexposed
            self._log_photo_result_simple(i, total_files, filename, rating_value, reason, photo_time_ms, is_flying, has_exposure_issue, focus_status)
            
            # 记录统计
            self._update_stats(rating_value, is_flying, has_exposure_issue)
            
            # V3.4: 确定要处理的目标文件（RAW 优先，没有则用 JPEG）
            target_file_path = None
            target_extension = None
            
            if file_prefix in raw_dict:
                # 有对应的 RAW 文件
                raw_extension = raw_dict[file_prefix]
                target_file_path = os.path.join(self.dir_path, file_prefix + raw_extension)
                target_extension = raw_extension
                
                # 写入 EXIF（仅限 RAW 文件）
                if os.path.exists(target_file_path):
                    # V4.0: 标签逻辑 - 飞鸟绿色优先，头部对焦红色
                    label = None
                    if is_flying:
                        label = 'Green'
                    elif focus_sharpness_weight > 1.0:  # 头部对焦 (1.1)
                        label = 'Red'
                    
                    # V4.0: 构建详细评分说明（使用换行符提高可读性）
                    caption_lines = []
                    
                    # 最终评分
                    caption_lines.append(f"最终评分: {rating_value}星 | {reason}")
                    
                    # 原始数据
                    sharpness_str = f"{head_sharpness:.2f}" if head_sharpness else "无法计算"
                    topiq_str = f"{topiq:.2f}" if topiq else "未计算"
                    caption_lines.append(f"[原始检测数据] AI置信度: {confidence:.0%} | 头部锐度: {sharpness_str} | TOPIQ美学: {topiq_str} | 眼睛可见度: {best_eye_visibility:.0%}")
                    
                    # 修正因子
                    flying_str = "是 (锐度×1.2, 美学×1.1)" if is_flying else "否"
                    caption_lines.append(f"[修正因子] 对焦锐度权重: {focus_sharpness_weight:.2f} | 对焦美学权重: {focus_topiq_weight:.2f} | 是否飞鸟: {flying_str}")
                    
                    # 调整后数值
                    adj_sharpness = head_sharpness * focus_sharpness_weight if head_sharpness else 0
                    if is_flying and head_sharpness:
                        adj_sharpness = adj_sharpness * 1.2
                    adj_line = f"[调整后数值] 调整后锐度: {adj_sharpness:.2f} (阈值400)"
                    if topiq:
                        adj_topiq = topiq * focus_topiq_weight
                        if is_flying:
                            adj_topiq = adj_topiq * 1.1
                        adj_line += f" | 调整后美学: {adj_topiq:.2f} (阈值5.0)"
                    caption_lines.append(adj_line)
                    
                    # 可见度降权（简化版，不显示公式）
                    visibility_weight = max(0.5, min(1.0, best_eye_visibility * 2))
                    if visibility_weight < 1.0:
                        caption_lines.append(f"[可见度降权] 可见度权重: {visibility_weight:.2f}")
                    
                    caption = "\n".join(caption_lines)
                    
                    single_batch = [{
                        'file': target_file_path,
                        'rating': rating_value if rating_value >= 0 else 0,
                        'pick': pick,
                        'sharpness': adj_sharpness if 'adj_sharpness' in dir() else head_sharpness,  # V4.0: 使用调整后的值
                        'nima_score': adj_topiq if 'adj_topiq' in dir() else topiq,  # V4.0: 使用调整后的值
                        'label': label,
                        'focus_status': focus_status,  # V3.9: 对焦状态写入 Country 字段
                        'caption': caption,  # V4.0: 详细评分说明
                    }]
                    exiftool_mgr.batch_set_metadata(single_batch)
            else:
                # V3.4: 纯 JPEG 文件（没有对应 RAW）
                target_file_path = filepath  # 使用当前处理的 JPEG 路径
                target_extension = os.path.splitext(filename)[1]
            
            # V3.4: 以下操作对 RAW 和纯 JPEG 都执行
            if target_file_path and os.path.exists(target_file_path):
                # V4.1: 计算调整后锐度（用于 CSV，保证重新评星一致性）
                adj_sharpness_csv = head_sharpness * focus_sharpness_weight if head_sharpness else 0
                if is_flying and head_sharpness:
                    adj_sharpness_csv = adj_sharpness_csv * 1.2
                adj_topiq_csv = topiq * focus_topiq_weight if topiq else None
                if is_flying and adj_topiq_csv:
                    adj_topiq_csv = adj_topiq_csv * 1.1
                
                # 更新 CSV 中的关键点数据（V4.1: 添加 adj_sharpness, adj_topiq）
                self._update_csv_keypoint_data(
                    file_prefix, 
                    head_sharpness,  # V4.1: 原始头部锐度
                    has_visible_eye, 
                    has_visible_beak,
                    left_eye_vis,
                    right_eye_vis,
                    beak_vis,
                    topiq,  # V4.1: 原始美学分数
                    rating_value,
                    is_flying,
                    flight_confidence,
                    focus_status,  # V3.9: 对焦状态
                    focus_x,  # V3.9: 对焦点X坐标
                    focus_y,  # V3.9: 对焦点Y坐标
                    adj_sharpness_csv,  # V4.1: 调整后锐度
                    adj_topiq_csv,  # V4.1: 调整后美学
                )
                
                # 收集3星照片（V4.1: 使用调整后的值）
                if rating_value == 3 and adj_topiq_csv is not None:
                    self.star_3_photos.append({
                        'file': target_file_path,
                        'nima': adj_topiq_csv,  # V4.1: 调整后美学
                        'sharpness': adj_sharpness_csv  # V4.1: 调整后锐度
                    })
                
                # 记录评分（用于文件移动）
                self.file_ratings[file_prefix] = rating_value
                
                # 记录2星原因（用于分目录）（V3.8: 使用加成后的值）
                if rating_value == 2:
                    sharpness_ok = rating_sharpness >= self.settings.sharpness_threshold
                    topiq_ok = rating_topiq is not None and rating_topiq >= self.settings.nima_threshold
                    if sharpness_ok and not topiq_ok:
                        self.star2_reasons[file_prefix] = 'sharpness'
                    elif topiq_ok and not sharpness_ok:
                        self.star2_reasons[file_prefix] = 'nima'  # 保留原字段名兼容
                    else:
                        self.star2_reasons[file_prefix] = 'both'
        
        ai_total_time = time.time() - ai_total_start
        avg_ai_time = ai_total_time / total_files if total_files > 0 else 0
        self._log(f"\n⏱️  AI检测总耗时: {ai_total_time:.1f}秒 (平均 {avg_ai_time:.1f}秒/张)")
    
    # 注意: _calculate_rating 方法已移至 core/rating_engine.py
    # 现在使用 self.rating_engine.calculate() 替代
    
    def _log_photo_result(
        self, 
        rating: int, 
        reason: str, 
        conf: float, 
        sharp: float, 
        nima: Optional[float]
    ):
        """记录照片处理结果（详细版，保留用于调试）"""
        iqa_text = ""
        if nima is not None:
            iqa_text += f", 美学:{nima:.2f}"
        
        if rating == 3:
            self._log(f"  ⭐⭐⭐ 优选照片 (AI:{conf:.2f}, 锐度:{sharp:.1f}{iqa_text})", "success")
        elif rating == 2:
            self._log(f"  ⭐⭐ 良好照片 (AI:{conf:.2f}, 锐度:{sharp:.1f}{iqa_text})", "info")
        elif rating == 1:
            self._log(f"  ⭐ 普通照片 (AI:{conf:.2f}, 锐度:{sharp:.1f}{iqa_text})", "warning")
        elif rating == 0:
            self._log(f"  普通照片 - {reason}", "warning")
        else:  # -1
            self._log(f"  ❌ 无鸟 - {reason}", "error")
    
    def _log_photo_result_simple(
        self,
        index: int,
        total: int,
        filename: str,
        rating: int,
        reason: str,
        time_ms: float,
        is_flying: bool = False,  # V3.4: 飞鸟标识
        has_exposure_issue: bool = False,  # V3.8: 曝光问题标识
        focus_status: str = None  # V3.9: 对焦状态
    ):
        """记录照片处理结果（简化版，单行输出）"""
        # 星级标识
        star_map = {3: "3星", 2: "2星", 1: "1星", 0: "0星", -1: "-1星"}
        star_text = star_map.get(rating, "?星")
        
        # V3.4: 飞鸟标识
        flight_tag = "【飞鸟】" if is_flying else ""
        
        # V3.8: 曝光问题标识（已在reason中显示"欠曝/过曝"，故不再单独显示标签）
        # exposure_tag = "【曝光】" if has_exposure_issue else ""
        
        # V3.9: 对焦状态标识（已在reason中显示"精焦/合焦/失焦/脱焦"，故不再单独显示标签）
        # focus_tag = ""
        # if focus_status:
        #     focus_tag = f"【{focus_status}】"
        
        # 简化原因显示（V3.9: 增加到35字符避免截断）
        reason_short = reason if len(reason) < 35 else reason[:32] + "..."
        
        # 时间格式化
        if time_ms >= 1000:
            time_text = f"{time_ms/1000:.1f}s"
        else:
            time_text = f"{time_ms:.0f}ms"
        
        # 输出简化格式（对焦状态已在reason中显示）
        self._log(f"[{index:03d}/{total}] {filename} | {star_text} ({reason_short}) {flight_tag}| {time_text}")
    
    def _save_debug_crop(
        self,
        filename: str,
        bird_crop_bgr: np.ndarray,
        bird_crop_mask: np.ndarray = None,
        head_center_crop: tuple = None,
        head_radius: int = None,
        focus_point_crop: tuple = None,
        focus_status: str = None
    ):
        """
        V3.9: 保存调试可视化图片到 .superpicky/debug_crops/ 目录
        
        标注内容：
        - 🟢 绿色半透明: SEG mask 鸟身区域
        - 🔵 蓝色圆圈: 头部检测区域
        - 🔴 红色十字: 对焦点位置
        """
        import cv2
        
        # 创建调试目录
        debug_dir = os.path.join(self.dir_path, ".superpicky", "debug_crops")
        os.makedirs(debug_dir, exist_ok=True)
        
        # 复制原图
        debug_img = bird_crop_bgr.copy()
        h, w = debug_img.shape[:2]
        
        # 1. 绘制 SEG mask（绿色半透明覆盖）
        if bird_crop_mask is not None and bird_crop_mask.shape[:2] == (h, w):
            green_overlay = np.zeros_like(debug_img)
            green_overlay[:] = (0, 255, 0)  # BGR 绿色
            mask_bool = bird_crop_mask > 0
            # 半透明叠加
            debug_img[mask_bool] = cv2.addWeighted(
                debug_img[mask_bool], 0.7,
                green_overlay[mask_bool], 0.3, 0
            )
        
        # 2. 绘制头部圆圈（蓝色）
        if head_center_crop is not None and head_radius is not None:
            cx, cy = head_center_crop
            cv2.circle(debug_img, (cx, cy), head_radius, (255, 0, 0), 2)  # 蓝色圆圈
            cv2.circle(debug_img, (cx, cy), 3, (255, 0, 0), -1)  # 圆心
        
        # 3. 绘制对焦点（红色十字）- V3.9.3 加大加粗更醒目
        if focus_point_crop is not None:
            fx, fy = focus_point_crop
            cross_size = 30  # 原来15，加大到30
            thickness = 4    # 原来2，加粗到4
            cv2.line(debug_img, (fx - cross_size, fy), (fx + cross_size, fy), (0, 0, 255), thickness)
            cv2.line(debug_img, (fx, fy - cross_size), (fx, fy + cross_size), (0, 0, 255), thickness)
            # 额外画一个红色圆点作为中心标记
            cv2.circle(debug_img, (fx, fy), 6, (0, 0, 255), -1)
        
        # 4. 添加状态文字
        if focus_status:
            cv2.putText(debug_img, focus_status, (10, 30), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        
        # 保存调试图
        file_prefix = os.path.splitext(filename)[0]
        debug_path = os.path.join(debug_dir, f"{file_prefix}_debug.jpg")
        cv2.imwrite(debug_path, debug_img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    
    def _update_stats(self, rating: int, is_flying: bool = False, has_exposure_issue: bool = False):
        """更新统计数据"""
        self.stats['total'] += 1
        if rating == 3:
            self.stats['star_3'] += 1
        elif rating == 2:
            self.stats['star_2'] += 1
        elif rating == 1:
            self.stats['star_1'] += 1  # 普通照片（合格）
        elif rating == 0:
            self.stats['star_0'] += 1  # 普通照片（问题）
        else:  # -1
            self.stats['no_bird'] += 1
        
        # V3.6: 统计飞鸟照片
        if is_flying:
            self.stats['flying'] += 1
        
        # V3.8: 统计曝光问题照片
        if has_exposure_issue:
            self.stats['exposure_issue'] += 1
    
    def _update_csv_keypoint_data(
        self, 
        filename: str, 
        head_sharpness: float,
        has_visible_eye: bool,
        has_visible_beak: bool,
        left_eye_vis: float,
        right_eye_vis: float,
        beak_vis: float,
        nima: float,
        rating: int,
        is_flying: bool = False,
        flight_confidence: float = 0.0,
        focus_status: str = None,  # V3.9: 对焦状态
        focus_x: float = None,  # V3.9: 对焦点X坐标
        focus_y: float = None,  # V3.9: 对焦点Y坐标
        adj_sharpness: float = None,  # V4.1: 调整后锐度
        adj_topiq: float = None  # V4.1: 调整后美学
    ):
        """更新CSV中的关键点数据和评分（V4.1: 添加 adj_sharpness, adj_topiq）"""
        import csv
        
        csv_path = os.path.join(self.dir_path, ".superpicky", "report.csv")
        if not os.path.exists(csv_path):
            return
        
        try:
            # 读取现有CSV
            rows = []
            with open(csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                fieldnames = list(reader.fieldnames) if reader.fieldnames else []
                
                # V3.9: 如果没有对焦相关字段则添加
                if 'focus_status' not in fieldnames:
                    rating_idx = fieldnames.index('rating') if 'rating' in fieldnames else len(fieldnames)
                    fieldnames.insert(rating_idx + 1, 'focus_status')
                if 'focus_x' not in fieldnames:
                    focus_status_idx = fieldnames.index('focus_status') if 'focus_status' in fieldnames else len(fieldnames)
                    fieldnames.insert(focus_status_idx + 1, 'focus_x')
                if 'focus_y' not in fieldnames:
                    focus_x_idx = fieldnames.index('focus_x') if 'focus_x' in fieldnames else len(fieldnames)
                    fieldnames.insert(focus_x_idx + 1, 'focus_y')
                # V4.1: 添加调整后锐度和美学字段
                if 'adj_sharpness' not in fieldnames:
                    focus_y_idx = fieldnames.index('focus_y') if 'focus_y' in fieldnames else len(fieldnames)
                    fieldnames.insert(focus_y_idx + 1, 'adj_sharpness')
                if 'adj_topiq' not in fieldnames:
                    adj_sharp_idx = fieldnames.index('adj_sharpness') if 'adj_sharpness' in fieldnames else len(fieldnames)
                    fieldnames.insert(adj_sharp_idx + 1, 'adj_topiq')
                
                for row in reader:
                    if row.get('filename') == filename:
                        # V3.4: 使用英文字段名更新数据
                        row['head_sharp'] = f"{head_sharpness:.0f}" if head_sharpness > 0 else "-"
                        row['left_eye'] = f"{left_eye_vis:.2f}"
                        row['right_eye'] = f"{right_eye_vis:.2f}"
                        row['beak'] = f"{beak_vis:.2f}"
                        row['nima_score'] = f"{nima:.2f}" if nima is not None else "-"
                        # V3.4: 飞版检测字段
                        row['is_flying'] = "yes" if is_flying else "no"
                        row['flight_conf'] = f"{flight_confidence:.2f}"
                        row['rating'] = str(rating)
                        # V3.9: 对焦状态和坐标字段
                        row['focus_status'] = focus_status if focus_status else "-"
                        row['focus_x'] = f"{focus_x:.3f}" if focus_x is not None else "-"
                        row['focus_y'] = f"{focus_y:.3f}" if focus_y is not None else "-"
                        # V4.1: 调整后锐度和美学（用于重新评星一致性）
                        row['adj_sharpness'] = f"{adj_sharpness:.2f}" if adj_sharpness else "-"
                        row['adj_topiq'] = f"{adj_topiq:.2f}" if adj_topiq else "-"
                    rows.append(row)
            
            # 写回CSV
            if fieldnames and rows:
                with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
        except Exception as e:
            self._log(f"  ⚠️  更新CSV失败: {e}", "warning")
    
    def _calculate_picked_flags(self):
        """计算精选旗标 - 3星照片中美学+锐度双排名交集"""
        if len(self.star_3_photos) == 0:
            self._log("\nℹ️  无3星照片，跳过精选旗标计算")
            return
        
        self._log(f"\n🎯 计算精选旗标 (共{len(self.star_3_photos)}张3星照片)...")
        top_percent = self.config.picked_top_percentage / 100.0
        top_count = max(1, int(len(self.star_3_photos) * top_percent))
        
        # 美学排序
        sorted_by_nima = sorted(self.star_3_photos, key=lambda x: x['nima'], reverse=True)
        nima_top_files = set([photo['file'] for photo in sorted_by_nima[:top_count]])
        
        # 锐度排序
        sorted_by_sharpness = sorted(self.star_3_photos, key=lambda x: x['sharpness'], reverse=True)
        sharpness_top_files = set([photo['file'] for photo in sorted_by_sharpness[:top_count]])
        
        # 交集
        picked_files = nima_top_files & sharpness_top_files
        
        if len(picked_files) > 0:
            self._log(f"  📌 美学Top{self.config.picked_top_percentage}%: {len(nima_top_files)}张")
            self._log(f"  📌 锐度Top{self.config.picked_top_percentage}%: {len(sharpness_top_files)}张")
            self._log(f"  ⭐ 双排名交集: {len(picked_files)}张 → 设为精选")
            
            # 调试：显示精选文件路径
            for file_path in picked_files:
                exists = os.path.exists(file_path)
                self._log(f"    🔍 精选: {os.path.basename(file_path)} (存在: {exists})")
            
            # 批量写入
            picked_batch = [{
                'file': file_path,
                'rating': 3,
                'pick': 1
            } for file_path in picked_files]
            
            exiftool_mgr = get_exiftool_manager()
            picked_stats = exiftool_mgr.batch_set_metadata(picked_batch)
            
            if picked_stats['failed'] == 0:
                self._log(f"  ✅ 精选旗标写入成功")
            else:
                self._log(f"  ⚠️  {picked_stats['failed']} 张精选旗标写入失败", "warning")
            
            self.stats['picked'] = len(picked_files) - picked_stats.get('failed', 0)
        else:
            self._log(f"  ℹ️  双排名交集为空，未设置精选旗标")
            self.stats['picked'] = 0
    
    def _move_files_to_rating_folders(self, raw_dict):
        """移动文件到分类文件夹（V3.4: 支持纯 JPEG）"""
        # 筛选需要移动的文件（包括所有星级，确保原目录为空）
        files_to_move = []
        for prefix, rating in self.file_ratings.items():
            if rating in [-1, 0, 1, 2, 3]:
                # V3.4: 优先使用 RAW，没有则使用 JPEG
                if prefix in raw_dict:
                    # 有对应的 RAW 文件
                    raw_ext = raw_dict[prefix]
                    file_path = os.path.join(self.dir_path, prefix + raw_ext)
                    if os.path.exists(file_path):
                        folder = RATING_FOLDER_NAMES.get(rating, "0星_放弃")
                        files_to_move.append({
                            'filename': prefix + raw_ext,
                            'rating': rating,
                            'folder': folder
                        })
                else:
                    # V3.4: 纯 JPEG 文件
                    for jpg_ext in ['.jpg', '.jpeg', '.JPG', '.JPEG']:
                        jpg_path = os.path.join(self.dir_path, prefix + jpg_ext)
                        if os.path.exists(jpg_path):
                            folder = RATING_FOLDER_NAMES.get(rating, "0星_放弃")
                            files_to_move.append({
                                'filename': prefix + jpg_ext,
                                'rating': rating,
                                'folder': folder
                            })
                            break  # 找到就跳出
        
        if not files_to_move:
            self._log("\n📂 无需移动文件")
            return
        
        self._log(f"\n📂 移动 {len(files_to_move)} 张照片到分类文件夹...")
        
        # 创建文件夹（使用实际的目录名）
        folders_in_use = set(f['folder'] for f in files_to_move)
        for folder_name in folders_in_use:
            folder_path = os.path.join(self.dir_path, folder_name)
            if not os.path.exists(folder_path):
                os.makedirs(folder_path)
                self._log(f"  📁 创建文件夹: {folder_name}/")
        
        # 移动文件
        moved_count = 0
        for file_info in files_to_move:
            src_path = os.path.join(self.dir_path, file_info['filename'])
            dst_folder = os.path.join(self.dir_path, file_info['folder'])
            dst_path = os.path.join(dst_folder, file_info['filename'])
            
            try:
                if os.path.exists(dst_path):
                    continue
                shutil.move(src_path, dst_path)
                moved_count += 1
            except Exception as e:
                self._log(f"  ⚠️  移动失败: {file_info['filename']} - {e}", "warning")
        
        # 生成manifest
        manifest = {
            "version": "1.0",
            "created": datetime.now().isoformat(),
            "app_version": "Refactored-Core",
            "original_dir": self.dir_path,
            "folder_structure": RATING_FOLDER_NAMES,
            "files": files_to_move,
            "stats": {"total_moved": moved_count}
        }
        
        manifest_path = os.path.join(self.dir_path, ".superpicky_manifest.json")
        try:
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            self._log(f"  ✅ 已移动 {moved_count} 张照片")
            self._log(f"  📋 Manifest: .superpicky_manifest.json")
        except Exception as e:
            self._log(f"  ⚠️  保存manifest失败: {e}", "warning")
    
    def _cleanup_temp_files(self, files_tbr, raw_dict):
        """清理临时JPG文件"""
        self._log("\n🧹 清理临时文件...")
        deleted_count = 0
        for filename in files_tbr:
            file_prefix, file_ext = os.path.splitext(filename)
            if file_prefix in raw_dict and file_ext.lower() in ['.jpg', '.jpeg']:
                jpg_path = os.path.join(self.dir_path, filename)
                try:
                    if os.path.exists(jpg_path):
                        os.remove(jpg_path)
                        deleted_count += 1
                except Exception as e:
                    self._log(f"  ⚠️  删除失败 {filename}: {e}", "warning")
        
        if deleted_count > 0:
            self._log(f"  ✅ 已删除 {deleted_count} 个临时JPG文件")
        else:
            self._log(f"  ℹ️  无临时文件需清理")
