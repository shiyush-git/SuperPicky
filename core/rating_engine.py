#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rating Engine - 评分引擎
负责根据 AI 检测结果和关键点检测计算照片评分

职责：
- 接收原始数据（置信度、锐度、TOPIQ美学分数、关键点检测结果）
- 根据配置计算评分和旗标
- 返回评分结果和原因

评分等级（关键点增强版）：
- -1 = 无鸟（排除）
-  0 = 普通（所有关键点不可见 或 最低标准不通过）
-  1 = 普通（通过最低标准但锐度和美学都不达标）
-  2 = 良好（锐度或美学达标）
-  3 = 优选（锐度+美学双达标）

注意：精选旗标(picked) 在所有照片处理完后由 PhotoProcessor 单独计算
"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class RatingResult:
    """评分结果"""
    rating: int          # -1=无鸟, 0=普通(问题照片), 1=普通(合格), 2=良好, 3=优选
    pick: int            # 0=无旗标, 1=精选, -1=排除
    reason: str          # 评分原因说明
    
    @property
    def star_display(self) -> str:
        """获取星级显示字符串"""
        if self.rating == 3:
            return "⭐⭐⭐"
        elif self.rating == 2:
            return "⭐⭐"
        elif self.rating == 1:
            return "⭐"
        elif self.rating == 0:
            return "普通"
        else:  # -1
            return "❌"


class RatingEngine:
    """
    评分引擎（关键点增强版 V3.8）
    
    评分规则：
    1. 无鸟 → -1 (Rejected)
    2. 最低标准不通过 → 0 (普通-问题照片)
    3. 所有关键点不可见（双眼+鸟喙都<0.3） → 0 (普通-角度不佳)
    4. 眼睛可见度封顶：
       - best_eye 0.3-0.5: 3星降为2星, 2星降为1星
       - best_eye >= 0.5: 正常评分
    5. 锐度 >= 阈值 AND TOPIQ >= 阈值 → 3星 (优选)
    6. 锐度 >= 阈值 OR TOPIQ >= 阈值 → 2星 (良好)
    7. 通过最低标准但都不达标 → 1星 (普通-合格)
    """
    
    def __init__(
        self,
        # 最低标准阈值（低于此为 0 星）
        min_confidence: float = 0.50,
        min_sharpness: float = 100,    # 头部区域锐度最低阈值
        min_nima: float = 4.0,
        # 2/3星达标阈值
        sharpness_threshold: float = 400,  # 头部区域锐度达标阈值（2星和3星共用）
        nima_threshold: float = 5.0,  # TOPIQ 美学达标阈值
    ):
        """
        初始化评分引擎
        
        Args:
            min_confidence: AI 置信度最低阈值 (0-1)
            min_sharpness: 锐度最低阈值
            min_nima: TOPIQ 美学最低阈值 (0-10)
            sharpness_threshold: 锐度达标阈值（2星和3星共用）
            topiq_threshold: TOPIQ 美学达标阈值 (2/3星)，范围 4.0-7.0
        """
        # 最低标准
        self.min_confidence = min_confidence
        self.min_sharpness = min_sharpness
        self.min_nima = min_nima
        
        # 达标标准（2星和3星共用）
        self.sharpness_threshold = sharpness_threshold
        self.nima_threshold = nima_threshold
    
    def calculate(
        self,
        detected: bool,
        confidence: float,
        sharpness: float,
        topiq: Optional[float] = None,
        all_keypoints_hidden: bool = False,
        best_eye_visibility: float = 1.0,  # V3.8: 眼睛最高置信度
        is_overexposed: bool = False,      # V3.8: 是否过曝
        is_underexposed: bool = False,     # V3.8: 是否欠曝
        focus_weight: float = 1.0,         # V3.9: 对焦权重 (1.5=对焦在鸟上, 0.8=对焦不在鸟上)
    ) -> RatingResult:
        """
        计算评分
        
        Args:
            detected: 是否检测到鸟
            confidence: AI 置信度 (0-1)
            sharpness: 归一化锐度值（关键点启用时为头部锐度）
            topiq: TOPIQ 美学评分 (0-10)，可选
            all_keypoints_hidden: 所有关键点是否都不可见（双眼+鸟喙）
            best_eye_visibility: 双眼中较高的置信度，用于封顶逻辑
            is_overexposed: 是否过曝（V3.8）
            is_underexposed: 是否欠曝（V3.8）
            focus_weight: 对焦权重（V3.9）
                - 1.5: 对焦点在鸟身上，锐度阈值降低 10%
                - 1.0: 无对焦数据，不影响评分
                - 0.8: 对焦不在鸟身上，最多给 2 星
            
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
        
        if topiq is not None and topiq < self.min_nima:
            return RatingResult(
                rating=0,
                pick=0,
                reason=f"美学太差({topiq:.1f}<{self.min_nima:.1f})"
            )
        
        # 第三步：关键点可见性检查（双眼+鸟喙都不可见才判 0 星）
        if all_keypoints_hidden:
            return RatingResult(
                rating=0,
                pick=0,
                reason="所有关键点不可见（角度不佳）"
            )
        
        # 第四步：锐度检查（只有眼睛可见时才有意义）
        if sharpness < self.min_sharpness:
            return RatingResult(
                rating=0,
                pick=0,
                reason=f"锐度太低({sharpness:.0f}<{self.min_sharpness})"
            )
        
        # V3.8: 判断是否需要封顶（眼睛可见度在 0.3-0.5 之间）
        needs_cap = 0.3 <= best_eye_visibility < 0.5
        
        # V3.8: 曝光问题标记
        has_exposure_issue = is_overexposed or is_underexposed
        exposure_suffix = ""
        if is_overexposed and is_underexposed:
            exposure_suffix = "，曝光异常"
        elif is_overexposed:
            exposure_suffix = "，过曝"
        elif is_underexposed:
            exposure_suffix = "，欠曝"
        
        # V3.9: 对焦权重处理 - 直接乘以锐度值
        # 权重: 1.05(头部) / 1.0(SEG) / 0.7(BBox) / 0.5(外部)
        adjusted_sharpness = sharpness * focus_weight
        
        # 设置对焦状态后缀
        focus_suffix = ""
        if focus_weight > 1.0:
            focus_suffix = "，对焦头部"
        elif focus_weight >= 1.0:
            pass  # 对焦在鸟身上，正常，不显示后缀
        elif focus_weight >= 0.7:
            focus_suffix = "，对焦偏移"
        else:  # 0.5
            focus_suffix = "，对焦错误"
        
        # 第五步：3 星判定（锐度 >= 阈值 AND TOPIQ >= 阈值）
        sharpness_ok = adjusted_sharpness >= self.sharpness_threshold
        topiq_ok = topiq is not None and topiq >= self.nima_threshold
        
        if sharpness_ok and topiq_ok:
            if needs_cap:
                # 眼睛可见度中等，3星降为2星
                rating = 2
                if has_exposure_issue:
                    rating = max(0, rating - 1)  # 曝光问题再降一星
                return RatingResult(
                    rating=rating,
                    pick=0,
                    reason=f"良好照片（双达标但眼睛可见度中等{exposure_suffix}{focus_suffix}）"
                )
            rating = 3
            if has_exposure_issue:
                rating = max(0, rating - 1)  # 3→2
            return RatingResult(
                rating=rating,
                pick=0,  # 精选旗标由 PhotoProcessor 后续计算
                reason=f"{'优选' if rating == 3 else '良好'}照片（锐度+TOPIQ双达标{exposure_suffix}{focus_suffix}）"
            )
        
        # 第六步：2 星判定（锐度达标 OR TOPIQ达标）
        if sharpness_ok or topiq_ok:
            if needs_cap:
                # 眼睛可见度中等，2星降为1星
                rating = 1
                if has_exposure_issue:
                    rating = max(0, rating - 1)  # 曝光问题再降一星
                if sharpness_ok:
                    return RatingResult(
                        rating=rating,
                        pick=0,
                        reason=f"普通照片（锐度达标但眼睛可见度中等{exposure_suffix}{focus_suffix}）"
                    )
                else:
                    return RatingResult(
                        rating=rating,
                        pick=0,
                        reason=f"普通照片（TOPIQ达标但眼睛可见度中等{exposure_suffix}{focus_suffix}）"
                    )
            # 正常情况
            rating = 2
            if has_exposure_issue:
                rating = max(0, rating - 1)  # 2→1
            if sharpness_ok:
                return RatingResult(
                    rating=rating,
                    pick=0,
                    reason=f"{'良好' if rating == 2 else '普通'}照片（锐度达标{exposure_suffix}{focus_suffix}）"
                )
            else:
                return RatingResult(
                    rating=rating,
                    pick=0,
                    reason=f"{'良好' if rating == 2 else '普通'}照片（TOPIQ达标{exposure_suffix}{focus_suffix}）"
                )
        
        # 第七步：1 = 普通（通过最低标准但未达标）
        rating = 1
        if has_exposure_issue:
            rating = max(0, rating - 1)  # 1→0
        return RatingResult(
            rating=rating,
            pick=0,
            reason=f"普通照片（锐度和美学均未达标{exposure_suffix}{focus_suffix}）"
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
        # 达标阈值（由 UI 滑块覆盖）
        sharpness_threshold=400,  # 锐度达标阈值 (200-600)
        nima_threshold=5.0,       # TOPIQ 美学达标阈值 (4.0-7.0)
    )
