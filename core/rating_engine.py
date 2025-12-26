#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rating Engine - 评分引擎
负责根据 AI 检测结果计算照片评分

职责：
- 接收原始数据（置信度、锐度、美学分数、失真分数）
- 根据配置计算评分和旗标
- 返回评分结果和原因

评分等级（简化版）：
- -1 = 无鸟（排除）
-  0 = 普通（不达标）
-  2 = 良好（锐度或美学达标）
-  3 = 优选（锐度+美学双达标）

注意：精选旗标(picked) 在所有照片处理完后由 PhotoProcessor 单独计算
"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class RatingResult:
    """评分结果"""
    rating: int          # -1=无鸟, 0=普通(不达标), 2=良好, 3=优选
    pick: int            # 0=无旗标, 1=精选, -1=排除
    reason: str          # 评分原因说明
    
    @property
    def star_display(self) -> str:
        """获取星级显示字符串"""
        if self.rating == 3:
            return "⭐⭐⭐"
        elif self.rating == 2:
            return "⭐⭐"
        elif self.rating == 0:
            return "普通"
        else:  # -1
            return "❌"


class RatingEngine:
    """
    评分引擎（简化版）
    
    评分规则（从底部往上判定）：
    1. 无鸟 → -1 (Rejected)
    2. 锐度 >= 阈值 AND 美学 >= 阈值 → 3星 (优选)
    3. 锐度 >= 阈值 OR 美学 >= 阈值 → 2星 (良好)
    4. 其他 → 0 (普通/不达标)
    """
    
    def __init__(
        self,
        # 最低标准阈值（低于此为 0 星）
        min_confidence: float = 0.50,
        min_sharpness: float = 6500,
        min_nima: float = 4.3,
        max_brisque: float = 55,
        # 达标阈值（达到此为 2 星或更高）
        sharpness_threshold: float = 7500,
        nima_threshold: float = 4.8,
    ):
        """
        初始化评分引擎
        
        Args:
            min_confidence: AI 置信度最低阈值 (0-1)
            min_sharpness: 锐度最低阈值
            min_nima: NIMA 美学最低阈值 (0-10)
            max_brisque: BRISQUE 失真最高阈值 (越低越好)
            sharpness_threshold: 锐度达标阈值 (达到此为 2 星)
            nima_threshold: NIMA 达标阈值 (达到此为 2 星)
        """
        # 最低标准
        self.min_confidence = min_confidence
        self.min_sharpness = min_sharpness
        self.min_nima = min_nima
        self.max_brisque = max_brisque
        
        # 达标标准
        self.sharpness_threshold = sharpness_threshold
        self.nima_threshold = nima_threshold
    
    def calculate(
        self,
        detected: bool,
        confidence: float,
        sharpness: float,
        nima: Optional[float] = None,
        brisque: Optional[float] = None,
    ) -> RatingResult:
        """
        计算评分
        
        Args:
            detected: 是否检测到鸟
            confidence: AI 置信度 (0-1)
            sharpness: 归一化锐度值
            nima: NIMA 美学评分 (0-10)，可选
            brisque: BRISQUE 失真评分 (越低越好)，可选
            
        Returns:
            RatingResult 包含评分、旗标和原因
        """
        # 第一步：无鸟检查
        if not detected:
            return RatingResult(
                rating=-1,
                pick=-1,
                reason="未检测到鸟类"
            )
        
        # 第二步：最低标准检查（不达标 → 0星普通）
        if confidence < self.min_confidence:
            return RatingResult(
                rating=0,
                pick=0,
                reason=f"置信度太低({confidence:.0%}<{self.min_confidence:.0%})"
            )
        
        if brisque is not None and brisque > self.max_brisque:
            return RatingResult(
                rating=0,
                pick=0,
                reason=f"失真过高({brisque:.1f}>{self.max_brisque})"
            )
        
        if nima is not None and nima < self.min_nima:
            return RatingResult(
                rating=0,
                pick=0,
                reason=f"美学太差({nima:.1f}<{self.min_nima:.1f})"
            )
        
        if sharpness < self.min_sharpness:
            return RatingResult(
                rating=0,
                pick=0,
                reason=f"锐度太低({sharpness:.0f}<{self.min_sharpness})"
            )
        
        # 第三步：3 星判定（锐度 AND 美学都达标）
        sharpness_ok = sharpness >= self.sharpness_threshold
        nima_ok = nima is not None and nima >= self.nima_threshold
        
        if sharpness_ok and nima_ok:
            return RatingResult(
                rating=3,
                pick=0,  # 精选旗标由 PhotoProcessor 后续计算
                reason="优选照片（锐度+美学双达标）"
            )
        
        # 第四步：2 星判定（锐度 OR 美学达标）
        if sharpness_ok or nima_ok:
            if sharpness_ok:
                return RatingResult(
                    rating=2,
                    pick=0,
                    reason="良好照片（锐度达标）"
                )
            else:
                return RatingResult(
                    rating=2,
                    pick=0,
                    reason="良好照片（美学达标）"
                )
        
        # 第五步：0 = 普通（通过最低标准但未达标）
        return RatingResult(
            rating=0,
            pick=0,
            reason="普通照片（锐度和美学均未达标）"
        )
    
    def update_thresholds(
        self,
        sharpness_threshold: Optional[float] = None,
        nima_threshold: Optional[float] = None,
    ):
        """更新达标阈值（用于 UI 滑块调整）"""
        if sharpness_threshold is not None:
            self.sharpness_threshold = sharpness_threshold
        if nima_threshold is not None:
            self.nima_threshold = nima_threshold


def create_rating_engine_from_config(config) -> RatingEngine:
    """
    从高级配置创建评分引擎
    
    Args:
        config: AdvancedConfig 实例
        
    Returns:
        RatingEngine 实例
    """
    return RatingEngine(
        min_confidence=config.min_confidence,
        min_sharpness=config.min_sharpness,
        min_nima=config.min_nima,
        max_brisque=config.max_brisque,
        # 达标阈值使用默认值，由 UI 滑块动态调整
        sharpness_threshold=7500,
        nima_threshold=4.8,
    )
