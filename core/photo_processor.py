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

# 文件夹名称映射（简化版：只有 2 星和 3 星有文件夹）
RATING_FOLDER_NAMES = {
    3: "3星_优选",
    2: "2星_良好"
}


@dataclass
class ProcessingSettings:
    """处理参数配置"""
    ai_confidence: int = 50
    sharpness_threshold: int = 7500
    nima_threshold: float = 4.8
    save_crop: bool = False
    normalization_mode: str = 'log_compression'  # 默认使用log_compression，与GUI一致


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
        self._log(f"  ⚙️  高级配置 - min_sharpness: {self.config.min_sharpness}")
        self._log(f"  ⚙️  高级配置 - min_nima: {self.config.min_nima}\n")
        
        # 统计数据（简化版：无 star_1）
        self.stats = {
            'total': 0,
            'star_3': 0,
            'picked': 0,
            'star_2': 0,
            'star_0': 0,  # 普通照片
            'no_bird': 0,
            'start_time': 0,
            'end_time': 0,
            'total_time': 0,
            'avg_time': 0
        }
        
        # 内部状态
        self.file_ratings = {}
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
        
        raw_extensions = ['.nef', '.cr2', '.cr3', '.arw', '.raf', '.orf', 
                         '.rw2', '.pef', '.dng', '.3fr', '.iiq']
        jpg_extensions = ['.jpg', '.jpeg']
        
        raw_dict = {}
        jpg_dict = {}
        files_tbr = []
        
        for filename in os.listdir(self.dir_path):
            if filename.startswith('.'):
                continue
            
            file_prefix, file_ext = os.path.splitext(filename)
            if file_ext.lower() in raw_extensions:
                raw_dict[file_prefix] = file_ext
            if file_ext.lower() in jpg_extensions:
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
        self._log(f"⏱️  RAW转换耗时: {raw_time:.1f}秒 (平均 {avg_time:.1f}秒/张)\n")
    
    def _process_images(self, files_tbr, raw_dict):
        """处理所有图片 - AI检测与评分"""
        # 加载模型
        model_start = time.time()
        self._log("🤖 加载AI模型...")
        model = load_yolo_model()
        model_time = (time.time() - model_start) * 1000
        self._log(f"⏱️  模型加载耗时: {model_time:.0f}ms")
        
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
            filepath = os.path.join(self.dir_path, filename)
            file_prefix, _ = os.path.splitext(filename)
            
            self._log(f"[{i}/{total_files}] {filename}")
            
            # 更新进度
            should_update = (i % 5 == 0 or i == total_files or i == 1)
            if should_update:
                progress = int((i / total_files) * 100)
                self._progress(progress)
            
            # AI检测
            try:
                result = detect_and_draw_birds(
                    filepath, model, None, self.dir_path, ui_settings, None
                )
                if result is None:
                    self._log(f"  ⚠️  无法处理(AI推理失败)", "error")
                    continue
            except Exception as e:
                self._log(f"  ❌ 处理异常: {e}", "error")
                continue
            
            # 解构 AI 结果（忽略 selected，由 RatingEngine 重新计算）
            detected, _, confidence, sharpness, nima, brisque = result
            
            # 使用 RatingEngine 计算评分
            rating_result = self.rating_engine.calculate(
                detected=detected,
                confidence=confidence,
                sharpness=sharpness,
                nima=nima,
                brisque=brisque
            )
            rating_value = rating_result.rating
            pick = rating_result.pick
            reason = rating_result.reason
            
            # 显示结果
            self._log_photo_result(rating_value, reason, confidence, sharpness, nima, brisque)
            
            # 记录统计
            self._update_stats(rating_value)
            
            # 写入EXIF
            raw_file_path = None
            if file_prefix in raw_dict:
                raw_extension = raw_dict[file_prefix]
                raw_file_path = os.path.join(self.dir_path, file_prefix + raw_extension)
                
                if os.path.exists(raw_file_path):
                    single_batch = [{
                        'file': raw_file_path,
                        'rating': rating_value if rating_value >= 0 else 0,
                        'pick': pick,
                        'sharpness': sharpness,
                        'nima_score': nima,
                        'brisque_score': brisque
                    }]
                    exiftool_mgr.batch_set_metadata(single_batch)
                    
                    # 收集3星照片
                    if rating_value == 3 and nima is not None:
                        self.star_3_photos.append({
                            'file': raw_file_path,
                            'nima': nima,
                            'sharpness': sharpness
                        })
                    
                    # 记录评分
                    self.file_ratings[file_prefix] = rating_value
        
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
        nima: Optional[float], 
        brisque: Optional[float]
    ):
        """记录照片处理结果"""
        iqa_text = ""
        if nima is not None:
            iqa_text += f", 美学:{nima:.2f}"
        if brisque is not None:
            iqa_text += f", 失真:{brisque:.2f}"
        
        if rating == 3:
            self._log(f"  ⭐⭐⭐ 优选照片 (AI:{conf:.2f}, 锐度:{sharp:.1f}{iqa_text})", "success")
        elif rating == 2:
            self._log(f"  ⭐⭐ 良好照片 (AI:{conf:.2f}, 锐度:{sharp:.1f}{iqa_text})", "info")
        elif rating == 0:
            self._log(f"  普通照片 (AI:{conf:.2f}, 锐度:{sharp:.1f}{iqa_text})", "warning")
        else:  # -1
            self._log(f"  ❌ 无鸟 - {reason}", "error")
    
    def _update_stats(self, rating: int):
        """更新统计数据"""
        self.stats['total'] += 1
        if rating == 3:
            self.stats['star_3'] += 1
        elif rating == 2:
            self.stats['star_2'] += 1
        elif rating == 0:
            self.stats['star_0'] += 1  # 普通照片
        else:  # -1
            self.stats['no_bird'] += 1
    
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
        """移动文件到分类文件夹"""
        # 筛选需要移动的文件
        files_to_move = []
        for prefix, rating in self.file_ratings.items():
            if rating in [1, 2, 3] and prefix in raw_dict:
                raw_ext = raw_dict[prefix]
                raw_path = os.path.join(self.dir_path, prefix + raw_ext)
                if os.path.exists(raw_path):
                    files_to_move.append({
                        'filename': prefix + raw_ext,
                        'rating': rating,
                        'folder': RATING_FOLDER_NAMES[rating]
                    })
        
        if not files_to_move:
            self._log("\n📂 无需移动文件(没有1-3星照片)")
            return
        
        self._log(f"\n📂 移动 {len(files_to_move)} 张照片到分类文件夹...")
        
        # 创建文件夹
        ratings_in_use = set(f['rating'] for f in files_to_move)
        for rating in ratings_in_use:
            folder_name = RATING_FOLDER_NAMES[rating]
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
        
        manifest_path = os.path.join(self.dir_path, "_superpicky_manifest.json")
        try:
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            self._log(f"  ✅ 已移动 {moved_count} 张照片")
            self._log(f"  📋 Manifest: _superpicky_manifest.json")
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
