#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLI Processor - 命令行处理器
简化版 - 调用核心 PhotoProcessor
"""

from typing import Dict, List
from core.photo_processor import (
    PhotoProcessor,
    ProcessingSettings,
    ProcessingCallbacks,
    ProcessingResult
)


class CLIProcessor:
    """CLI 处理器 - 只负责命令行交互"""
    
    def __init__(self, dir_path: str, ui_settings: List = None, verbose: bool = True):
        """
        初始化处理器
        
        Args:
            dir_path: 处理目录
            ui_settings: [ai_confidence, sharpness_threshold, nima_threshold, save_crop, norm_mode]
            verbose: 详细输出
        """
        self.verbose = verbose
        
        # GUI默认设置: [50, 7500, 4.8, False, 'log_compression']
        if ui_settings is None:
            ui_settings = [50, 7500, 4.8, False, 'log_compression']
        
        # 转换为 ProcessingSettings
        settings = ProcessingSettings(
            ai_confidence=ui_settings[0],
            sharpness_threshold=ui_settings[1],
            nima_threshold=ui_settings[2],
            save_crop=ui_settings[3] if len(ui_settings) > 3 else False,
            normalization_mode=ui_settings[4] if len(ui_settings) > 4 else 'log_compression'
        )
        
        # 创建核心处理器
        self.processor = PhotoProcessor(
            dir_path=dir_path,
            settings=settings,
            callbacks=ProcessingCallbacks(
                log=self._log,
                progress=self._progress
            )
        )
    
    def _log(self, msg: str, level: str = "info"):
        """日志回调 - 带颜色输出"""
        if not self.verbose:
            return
        
        # ANSI颜色代码
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
    
    def _progress(self, percent: int):
        """进度回调 - CLI可选"""
        # CLI 模式下可以选择是否显示进度
        # 目前不显示，避免输出过多
        pass
    
    def process(self, organize_files: bool = True, cleanup_temp: bool = True) -> Dict:
        """
        主处理流程
        
        Args:
            organize_files: 是否移动文件到分类文件夹
            cleanup_temp: 是否清理临时JPG
            
        Returns:
            处理统计字典
        """
        # 打印横幅
        self._print_banner()
        
        # 调用核心处理器
        result = self.processor.process(
            organize_files=organize_files,
            cleanup_temp=cleanup_temp
        )
        
        # 打印摘要
        self._print_summary(result)
        
        return result.stats
    
    def _print_banner(self):
        """打印CLI横幅"""
        self._log("\n" + "="*60)
        self._log("🐦 SuperPicky CLI - 慧眼选鸟 (命令行版)")
        self._log("="*60 + "\n")
        
        self._log("📁 阶段1: 文件扫描", "info")
    
    def _print_summary(self, result: ProcessingResult):
        """打印完成摘要"""
        stats = result.stats
        
        self._log("\n" + "="*60)
        self._log("📊 处理完成统计:", "success")
        self._log("")
        self._log(f"  总文件数: {stats['total']}")
        self._log(f"  ├─ ⭐⭐⭐ 优选 (3星): {stats['star_3']}  (精选: {stats['picked']})")
        self._log(f"  ├─ ⭐⭐   良好 (2星): {stats['star_2']}")
        self._log(f"  ├─ 普通 (不达标)  : {stats['star_0']}")
        self._log(f"  └─ ❌    无鸟       : {stats['no_bird']}")
        self._log("")
        self._log(f"  总耗时: {stats['total_time']:.1f}秒")
        self._log(f"  平均速度: {stats['avg_time']:.1f}秒/张")
        self._log("="*60)
        self._log("\n✅ 所有照片已写入EXIF元数据，可在Lightroom中查看\n", "success")
