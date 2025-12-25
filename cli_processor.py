#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLI Processor - 命令行处理器
复现 GUI WorkerThread 的所有功能
"""

import os
import time
import json
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

# 现有模块
from find_bird_util import raw_to_jpeg
from ai_model import load_yolo_model, detect_and_draw_birds
from exiftool_manager import get_exiftool_manager
from advanced_config import get_advanced_config

# 文件夹名称映射
RATING_FOLDER_NAMES = {
    3: "3星_优选",
    2: "2星_良好",
    1: "1星_普通"
}


class CLIProcessor:
    """CLI 处理器 - 对标 GUI 的 WorkerThread"""
    
    def __init__(self, dir_path: str, ui_settings: List = None, verbose: bool = True):
        """
        初始化处理器
        
        Args:
            dir_path: 处理目录
            ui_settings: [ai_confidence, sharpness_threshold, nima_threshold, save_crop, norm_mode]
            verbose: 详细输出
        """
        self.dir_path = dir_path
        self.verbose = verbose
        
        # GUI默认设置: [50, 7500, 4.8, False, '对数压缩(V3.1) - 大小鸟公平']
        if ui_settings is None:
            ui_settings = [50, 7500, 4.8, False, 'log']
        self.ui_settings = ui_settings
        
        # 统计数据
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
        
        # 加载配置
        self.config = get_advanced_config()
    
    def log(self, msg: str, level: str = "info"):
        """输出日志"""
        if not self.verbose:
            return
        
        # 简单的日志着色(仅终端支持ANSI)
        colors = {
            "success": "\033[92m",  # 绿色
            "error": "\033[91m",    # 红色
            "warning": "\033[93m",  # 黄色
            "info": "\033[94m",     # 蓝色
            "reset": "\033[0m"
        }
        
        color = colors.get(level, "")
        reset = colors["reset"] if color else ""
        print(f"{color}{msg}{reset}")
    
    def process(self, organize_files: bool = True, cleanup_temp: bool = True) -> Dict:
        """
        主处理流程
        
        Args:
            organize_files: 是否移动文件到分类文件夹
            cleanup_temp: 是否清理临时JPG
            
        Returns:
            处理结果字典
        """
        start_time = time.time()
        self.stats['start_time'] = start_time
        
        self.log("\n" + "="*60)
        self.log("🐦 SuperPicky CLI - 慧眼选鸟 (命令行版)")
        self.log("="*60 + "\n")
        
        # 阶段1: 文件扫描
        self.log("📁 阶段1: 文件扫描", "info")
        raw_dict, jpg_dict, files_tbr = self._scan_files()
        
        # 阶段2: RAW转换
        self.log("\n🔄 阶段2: RAW转换", "info")
        raw_files_to_convert = self._identify_raws_to_convert(raw_dict, jpg_dict, files_tbr)
        if raw_files_to_convert:
            self._convert_raws(raw_files_to_convert, files_tbr)
        else:
            self.log("  ✅ 无需转换RAW文件")
        
        # 阶段3: AI检测与评分
        self.log("\n🤖 阶段3: AI检测与评分", "info")
        file_ratings, star_3_photos = self._process_images(files_tbr, raw_dict)
        
        # 阶段4: 精选旗标计算
        self.log("\n🎯 阶段4: 计算精选旗标", "info")
        self._calculate_picked_flags(star_3_photos)
        
        # 阶段5: 文件组织
        if organize_files:
            self.log("\n📂 阶段5: 文件组织", "info")
            self._move_files_to_rating_folders(file_ratings, raw_dict)
        
        # 阶段6: 清理临时文件
        if cleanup_temp:
            self.log("\n🧹 阶段6: 清理临时文件", "info")
            self._cleanup_temp_files(files_tbr, raw_dict)
        
        # 统计
        end_time = time.time()
        self.stats['end_time'] = end_time
        self.stats['total_time'] = end_time - start_time
        self.stats['avg_time'] = self.stats['total_time'] / self.stats['total'] if self.stats['total'] > 0 else 0
        
        # 显示完成报告
        self._print_summary()
        
        return self.stats
    
    def _scan_files(self) -> tuple:
        """扫描目录文件"""
        scan_start = time.time()
        
        raw_extensions = ['.nef', '.cr2', '.cr3', '.arw', '.raf', '.orf', '.rw2', '.pef', '.dng', '.3fr', '.iiq']
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
        self.log(f"  ✅ 找到 {len(raw_dict)} 个 RAW, {len(jpg_dict)} 个 JPG")
        self.log(f"  ⏱️  扫描耗时: {scan_time:.1f}ms")
        
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
        
        self.log(f"  🔄 开始并行转换 {len(raw_files_to_convert)} 个RAW文件({max_workers}线程)...")
        
        def convert_single(args):
            key, raw_path = args
            try:
                raw_to_jpeg(raw_path)
                return (key, True, None)
            except Exception as e:
                return (key, False, str(e))
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_raw = {executor.submit(convert_single, args): args for args in raw_files_to_convert}
            converted_count = 0
            
            for future in as_completed(future_to_raw):
                key, success, error = future.result()
                if success:
                    files_tbr.append(key + ".jpg")
                    converted_count += 1
                    if converted_count % 5 == 0 or converted_count == len(raw_files_to_convert):
                        self.log(f"    ✅ 已转换 {converted_count}/{len(raw_files_to_convert)} 张")
                else:
                    self.log(f"    ❌ 转换失败: {key} ({error})", "error")
        
        raw_time = time.time() - raw_start
        self.log(f"  ⏱️  转换总耗时: {raw_time:.1f}秒 (平均 {raw_time/len(raw_files_to_convert):.1f}秒/张)")
    
    def _process_images(self, files_tbr, raw_dict):
        """处理所有图片"""
        # 加载模型
        model_start = time.time()
        self.log("  🤖 加载AI模型...")
        model = load_yolo_model()
        model_time = (time.time() - model_start) * 1000
        self.log(f"  ⏱️  模型加载耗时: {model_time:.0f}ms")
        
        total_files = len(files_tbr)
        self.log(f"  📁 共 {total_files} 个文件待处理\n")
        
        exiftool_mgr = get_exiftool_manager()
        
        file_ratings = {}
        star_3_photos = []
        ai_total_start = time.time()
        
        for i, filename in enumerate(files_tbr, 1):
            filepath = os.path.join(self.dir_path, filename)
            file_prefix, _ = os.path.splitext(filename)
            
            self.log(f"  [{i}/{total_files}] {filename}")
            
            # AI检测
            try:
                result = detect_and_draw_birds(filepath, model, None, self.dir_path, self.ui_settings, None)
                if result is None:
                    self.log(f"      ⚠️  无法处理(AI推理失败)", "error")
                    continue
            except Exception as e:
                self.log(f"      ❌ 处理异常: {e}", "error")
                continue
            
            detected, selected, confidence, sharpness, nima, brisque = result
            
            # 评分逻辑
            rating_value, pick, reason = self._calculate_rating(
                detected, selected, confidence, sharpness, nima, brisque
            )
            
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
                        star_3_photos.append({
                            'file': raw_file_path,
                            'nima': nima,
                            'sharpness': sharpness
                        })
                    
                    # 记录评分
                    file_ratings[file_prefix] = rating_value
        
        ai_total_time = time.time() - ai_total_start
        avg_ai_time = ai_total_time / total_files if total_files > 0 else 0
        self.log(f"\n  ⏱️  AI检测总耗时: {ai_total_time:.1f}秒 (平均 {avg_ai_time:.1f}秒/张)")
        
        return file_ratings, star_3_photos
    
    def _calculate_rating(self, detected, selected, confidence, sharpness, nima, brisque):
        """计算评分 - 完全对标GUI逻辑"""
        if not detected:
            return -1, -1, "完全没鸟"
        
        if selected:
            return 3, 0, "优选照片"
        
        # 检查0星原因
        if confidence < self.config.min_confidence:
            return 0, 0, f"置信度太低({confidence:.0%}<{self.config.min_confidence:.0%})"
        
        if brisque is not None and brisque > self.config.max_brisque:
            return 0, 0, f"失真过高({brisque:.1f}>{self.config.max_brisque})"
        
        if nima is not None and nima < self.config.min_nima:
            return 0, 0, f"美学太差({nima:.1f}<{self.config.min_nima:.1f})"
        
        if sharpness < self.config.min_sharpness:
            return 0, 0, f"锐度太低({sharpness:.0f}<{self.config.min_sharpness})"
        
        # 2星或1星判定
        if sharpness >= self.ui_settings[1] or (nima is not None and nima >= self.ui_settings[2]):
            return 2, 0, "良好照片"
        else:
            return 1, 0, "普通照片"
    
    def _log_photo_result(self, rating, reason, conf, sharp, nima, brisque):
        """记录照片结果"""
        iqa_text = ""
        if nima is not None:
            iqa_text += f", 美学:{nima:.2f}"
        if brisque is not None:
            iqa_text += f", 失真:{brisque:.2f}"
        
        if rating == 3:
            self.log(f"      ⭐⭐⭐ 优选照片 (AI:{conf:.2f}, 锐度:{sharp:.1f}{iqa_text})", "success")
        elif rating == 2:
            self.log(f"      ⭐⭐ 良好照片 (AI:{conf:.2f}, 锐度:{sharp:.1f}{iqa_text})", "info")
        elif rating == 1:
            self.log(f"      ⭐ 普通照片 (AI:{conf:.2f}, 锐度:{sharp:.1f}{iqa_text})", "warning")
        elif rating == 0:
            self.log(f"      0星 - {reason} (AI:{conf:.2f}, 锐度:{sharp:.1f}{iqa_text})", "warning")
        else:  # -1
            self.log(f"      ❌ 已拒绝 - {reason}", "error")
    
    def _update_stats(self, rating):
        """更新统计数据"""
        self.stats['total'] += 1
        if rating == 3:
            self.stats['star_3'] += 1
        elif rating == 2:
            self.stats['star_2'] += 1
        elif rating == 1:
            self.stats['star_1'] += 1
        elif rating == 0:
            self.stats['star_0'] += 1
        else:  # -1
            self.stats['no_bird'] += 1
    
    def _calculate_picked_flags(self, star_3_photos):
        """计算精选旗标"""
        if len(star_3_photos) == 0:
            self.log("  ℹ️  无3星照片，跳过精选旗标计算")
            return
        
        self.log(f"  📊 共{len(star_3_photos)}张3星照片")
        top_percent = self.config.picked_top_percentage / 100.0
        top_count = max(1, int(len(star_3_photos) * top_percent))
        
        # 美学排序
        sorted_by_nima = sorted(star_3_photos, key=lambda x: x['nima'], reverse=True)
        nima_top_files = set([photo['file'] for photo in sorted_by_nima[:top_count]])
        
        # 锐度排序
        sorted_by_sharpness = sorted(star_3_photos, key=lambda x: x['sharpness'], reverse=True)
        sharpness_top_files = set([photo['file'] for photo in sorted_by_sharpness[:top_count]])
        
        # 交集
        picked_files = nima_top_files & sharpness_top_files
        
        if len(picked_files) > 0:
            self.log(f"  📌 美学Top{self.config.picked_top_percentage}%: {len(nima_top_files)}张")
            self.log(f"  📌 锐度Top{self.config.picked_top_percentage}%: {len(sharpness_top_files)}张")
            self.log(f"  ⭐ 双排名交集: {len(picked_files)}张 → 设为精选")
            
            # 批量写入
            picked_batch = [{
                'file': file_path,
                'rating': 3,
                'pick': 1
            } for file_path in picked_files]
            
            exiftool_mgr = get_exiftool_manager()
            picked_stats = exiftool_mgr.batch_set_metadata(picked_batch)
            
            if picked_stats['failed'] == 0:
                self.log(f"  ✅ 精选旗标写入成功")
            else:
                self.log(f"  ⚠️  {picked_stats['failed']} 张精选旗标写入失败", "warning")
            
            self.stats['picked'] = len(picked_files) - picked_stats.get('failed', 0)
        else:
            self.log(f"  ℹ️  双排名交集为空，未设置精选旗标")
            self.stats['picked'] = 0
    
    def _move_files_to_rating_folders(self, file_ratings, raw_dict):
        """移动文件到分类文件夹"""
        from datetime import datetime
        
        # 筛选需要移动的文件
        files_to_move = []
        for prefix, rating in file_ratings.items():
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
            self.log("  ℹ️  无需移动文件(没有1-3星照片)")
            return
        
        self.log(f"  📂 移动 {len(files_to_move)} 张照片到分类文件夹...")
        
        # 创建文件夹
        ratings_in_use = set(f['rating'] for f in files_to_move)
        for rating in ratings_in_use:
            folder_name = RATING_FOLDER_NAMES[rating]
            folder_path = os.path.join(self.dir_path, folder_name)
            if not os.path.exists(folder_path):
                os.makedirs(folder_path)
                self.log(f"    📁 创建文件夹: {folder_name}/")
        
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
                self.log(f"    ⚠️  移动失败: {file_info['filename']} - {e}", "warning")
        
        # 生成manifest
        manifest = {
            "version": "1.0",
            "created": datetime.now().isoformat(),
            "app_version": "CLI-0.1.0",
            "original_dir": self.dir_path,
            "folder_structure": RATING_FOLDER_NAMES,
            "files": files_to_move,
            "stats": {"total_moved": moved_count}
        }
        
        manifest_path = os.path.join(self.dir_path, "_superpicky_manifest.json")
        try:
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            self.log(f"  ✅ 已移动 {moved_count} 张照片")
            self.log(f"  📋 Manifest: _superpicky_manifest.json")
        except Exception as e:
            self.log(f"  ⚠️  保存manifest失败: {e}", "warning")
    
    def _cleanup_temp_files(self, files_tbr, raw_dict):
        """清理临时JPG文件"""
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
                    self.log(f"  ⚠️  删除失败 {filename}: {e}", "warning")
        
        if deleted_count > 0:
            self.log(f"  ✅ 已删除 {deleted_count} 个临时JPG文件")
        else:
            self.log(f"  ℹ️  无临时文件需清理")
    
    def _print_summary(self):
        """打印完成摘要"""
        self.log("\n" + "="*60)
        self.log("📊 处理完成统计:", "success")
        self.log("")
        self.log(f"  总文件数: {self.stats['total']}")
        self.log(f"  ├─ ⭐⭐⭐ 优选 (3星): {self.stats['star_3']}  (精选: {self.stats['picked']})")
        self.log(f"  ├─ ⭐⭐   良好 (2星): {self.stats['star_2']}")
        self.log(f"  ├─ ⭐     普通 (1星): {self.stats['star_1']}")
        self.log(f"  ├─ 0星   质量差     : {self.stats['star_0']}")
        self.log(f"  └─ ❌    无鸟       : {self.stats['no_bird']}")
        self.log("")
        self.log(f"  总耗时: {self.stats['total_time']:.1f}秒")
        self.log(f"  平均速度: {self.stats['avg_time']:.1f}秒/张")
        self.log("="*60)
        self.log("\n✅ 所有照片已写入EXIF元数据，可在Lightroom中查看\n", "success")
