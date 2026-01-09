"""
连拍检测器模块 - V4.0

功能：
1. 读取毫秒级时间戳 (SubSecTimeOriginal)
2. 检测连拍组 (时间差 < 150ms)
3. 组内最佳选择
4. 分组处理 (子目录 + 标签)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple, Dict
import os
import subprocess
import json
import sys  # V3.9: 用于检测 PyInstaller 打包环境


@dataclass
class PhotoTimestamp:
    """照片时间戳信息"""
    filepath: str
    datetime_original: Optional[datetime] = None
    subsec: Optional[str] = None  # 毫秒部分，如 "045"
    precise_time: Optional[float] = None  # 精确时间戳（秒）
    rating: int = 0
    sharpness: float = 0.0
    topiq: float = 0.0
    
    @property
    def has_subsec(self) -> bool:
        """是否有毫秒信息"""
        return self.subsec is not None and self.subsec != ""


@dataclass
class BurstGroup:
    """连拍组"""
    group_id: int
    photos: List[PhotoTimestamp] = field(default_factory=list)
    best_index: int = 0  # 最佳照片在 photos 列表中的索引
    
    @property
    def count(self) -> int:
        return len(self.photos)
    
    @property
    def best_photo(self) -> Optional[PhotoTimestamp]:
        if self.photos and 0 <= self.best_index < len(self.photos):
            return self.photos[self.best_index]
        return None


class BurstDetector:
    """连拍检测器"""
    
    # 默认检测参数（可被 advanced_config 覆盖）
    TIME_THRESHOLD_MS = 250  # V3.9: 默认 250ms
    MIN_BURST_COUNT = 4      # V3.9: 默认 4 张
    MIN_RATING = 2           # 只处理 >= 2 星的照片
    
    # pHash 参数
    PHASH_THRESHOLD = 12     # 汉明距离阈值（<=12 视为相似）
    USE_PHASH = True         # 是否启用 pHash 验证
    
    def __init__(self, exiftool_path: str = None, use_phash: bool = True):
        """
        初始化连拍检测器
        
        Args:
            exiftool_path: ExifTool 路径
            use_phash: 是否启用 pHash 验证
        """
        self.exiftool_path = exiftool_path or self._find_exiftool()
        self.USE_PHASH = use_phash
        
        # V3.9: 从配置加载参数
        try:
            from advanced_config import get_advanced_config
            config = get_advanced_config()
            self.TIME_THRESHOLD_MS = config.burst_time_threshold
            self.MIN_BURST_COUNT = config.burst_min_count
        except Exception:
            pass  # 使用默认值
    
    def _find_exiftool(self) -> str:
        """查找 ExifTool 路径"""
        # V3.9: 优先检查 PyInstaller 打包环境
        if hasattr(sys, '_MEIPASS'):
            bundled = os.path.join(sys._MEIPASS, 'exiftool_bundle', 'exiftool')
            if os.path.exists(bundled):
                return bundled
        
        # 开发环境: 优先使用项目内置的 exiftool
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        builtin = os.path.join(project_root, 'exiftool')
        if os.path.exists(builtin):
            return builtin
        
        # 否则使用系统 exiftool
        return 'exiftool'
    
    def read_timestamps(self, filepaths: List[str]) -> List[PhotoTimestamp]:
        """
        批量读取照片的精确时间戳
        
        Args:
            filepaths: 文件路径列表
            
        Returns:
            PhotoTimestamp 列表
        """
        if not filepaths:
            return []
        
        # 使用 exiftool 批量读取
        cmd = [
            self.exiftool_path,
            '-json',
            '-DateTimeOriginal',
            '-SubSecTimeOriginal',
            '-Rating',
        ] + filepaths
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode != 0:
                print(f"⚠️ ExifTool 读取时间戳失败: {result.stderr}")
                return []
            
            exif_data = json.loads(result.stdout)
            return self._parse_exif_timestamps(exif_data)
            
        except subprocess.TimeoutExpired:
            print("⚠️ ExifTool 读取超时")
            return []
        except json.JSONDecodeError as e:
            print(f"⚠️ 解析 EXIF JSON 失败: {e}")
            return []
    
    def _parse_exif_timestamps(self, exif_data: List[dict]) -> List[PhotoTimestamp]:
        """
        解析 EXIF 数据为 PhotoTimestamp 列表
        
        Args:
            exif_data: ExifTool JSON 输出
            
        Returns:
            PhotoTimestamp 列表
        """
        results = []
        
        for item in exif_data:
            filepath = item.get('SourceFile', '')
            dt_str = item.get('DateTimeOriginal', '')
            subsec = item.get('SubSecTimeOriginal', '')
            rating = item.get('Rating', 0) or 0
            
            # 解析日期时间
            dt = None
            if dt_str:
                try:
                    # 格式: "2024:01:09 10:05:30"
                    dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
                except ValueError:
                    pass
            
            # 计算精确时间戳
            precise_time = None
            if dt:
                precise_time = dt.timestamp()
                if subsec:
                    # SubSecTimeOriginal 通常是毫秒部分，如 "045"
                    try:
                        subsec_float = float(f"0.{subsec}")
                        precise_time += subsec_float
                    except ValueError:
                        pass
            
            photo = PhotoTimestamp(
                filepath=filepath,
                datetime_original=dt,
                subsec=str(subsec) if subsec else None,
                precise_time=precise_time,
                rating=rating
            )
            results.append(photo)
        
        return results
    
    def detect_groups(self, photos: List[PhotoTimestamp]) -> List[BurstGroup]:
        """
        检测连拍组
        
        Args:
            photos: PhotoTimestamp 列表
            
        Returns:
            BurstGroup 列表
        """
        # 1. 只处理 >= 2 星的照片
        candidates = [p for p in photos if p.rating >= self.MIN_RATING and p.precise_time is not None]
        
        if len(candidates) < self.MIN_BURST_COUNT:
            return []
        
        # 2. 按精确时间排序
        candidates.sort(key=lambda p: p.precise_time)
        
        # 3. 分组检测（基于时间戳）
        groups = []
        current_group = [candidates[0]]
        
        for i in range(1, len(candidates)):
            prev = candidates[i - 1]
            curr = candidates[i]
            
            # 计算时间差（毫秒）
            time_diff_ms = (curr.precise_time - prev.precise_time) * 1000
            
            if time_diff_ms <= self.TIME_THRESHOLD_MS:
                # 属于同一组
                current_group.append(curr)
            else:
                # 保存当前组（如果满足最小张数）
                if len(current_group) >= self.MIN_BURST_COUNT:
                    group = BurstGroup(
                        group_id=len(groups) + 1,
                        photos=current_group.copy()
                    )
                    groups.append(group)
                
                # 开始新组
                current_group = [curr]
        
        # 处理最后一组
        if len(current_group) >= self.MIN_BURST_COUNT:
            group = BurstGroup(
                group_id=len(groups) + 1,
                photos=current_group.copy()
            )
            groups.append(group)
        
        # 4. V4.0: pHash 验证（过滤误判）
        if self.USE_PHASH and groups:
            groups = self.verify_groups_with_phash(groups)
        
        return groups
    
    def verify_groups_with_phash(self, groups: List[BurstGroup]) -> List[BurstGroup]:
        """
        使用 pHash 验证连拍组，过滤掉内容差异大的照片
        
        Args:
            groups: 初步检测的连拍组
            
        Returns:
            验证后的连拍组
        """
        try:
            from imagehash import phash
            from PIL import Image
        except ImportError:
            print("⚠️ imagehash 未安装，跳过 pHash 验证")
            return groups
        
        verified_groups = []
        
        for group in groups:
            if group.count < 2:
                verified_groups.append(group)
                continue
            
            # 计算组内所有照片的 pHash
            hashes = []
            for photo in group.photos:
                try:
                    # 使用预览图（如果存在）或原图
                    img_path = photo.filepath
                    # 尝试找 JPEG 预览（更快）
                    jpg_path = os.path.splitext(photo.filepath)[0] + '.jpg'
                    if os.path.exists(jpg_path):
                        img_path = jpg_path
                    
                    img = Image.open(img_path)
                    h = phash(img)
                    hashes.append((photo, h))
                except Exception as e:
                    # 无法计算 pHash，保留该照片
                    hashes.append((photo, None))
            
            # 验证相邻照片的相似度
            verified_photos = [hashes[0][0]]  # 保留第一张
            
            for i in range(1, len(hashes)):
                curr_photo, curr_hash = hashes[i]
                prev_photo, prev_hash = hashes[i - 1]
                
                if curr_hash is None or prev_hash is None:
                    # 无法比较，保留
                    verified_photos.append(curr_photo)
                else:
                    distance = curr_hash - prev_hash
                    if distance <= self.PHASH_THRESHOLD:
                        # 相似，保留在组内
                        verified_photos.append(curr_photo)
                    else:
                        # 不相似，可能是飞鸟或重构图
                        # 开始新组（如果剩余足够）
                        if len(verified_photos) >= self.MIN_BURST_COUNT:
                            verified_groups.append(BurstGroup(
                                group_id=len(verified_groups) + 1,
                                photos=verified_photos.copy()
                            ))
                        verified_photos = [curr_photo]
            
            # 保存最后的验证组
            if len(verified_photos) >= self.MIN_BURST_COUNT:
                verified_groups.append(BurstGroup(
                    group_id=len(verified_groups) + 1,
                    photos=verified_photos
                ))
        
        return verified_groups
    
    def select_best_in_groups(self, groups: List[BurstGroup]) -> List[BurstGroup]:
        """
        在每个连拍组中选择最佳照片
        
        Args:
            groups: BurstGroup 列表
            
        Returns:
            更新后的 BurstGroup 列表
        """
        for group in groups:
            if not group.photos:
                continue
            
            # 按综合分数排序：锐度 * 0.5 + 美学 * 0.5
            best_score = -1
            best_idx = 0
            
            for i, photo in enumerate(group.photos):
                score = photo.sharpness * 0.5 + photo.topiq * 0.5
                if score > best_score:
                    best_score = score
                    best_idx = i
            
            group.best_index = best_idx
        
        return groups
    
    def enrich_from_csv(self, photos: List[PhotoTimestamp], csv_path: str) -> List[PhotoTimestamp]:
        """
        从 CSV 报告中读取锐度和美学分数
        
        Args:
            photos: PhotoTimestamp 列表
            csv_path: CSV 报告路径
            
        Returns:
            更新后的 PhotoTimestamp 列表
        """
        import csv
        
        if not os.path.exists(csv_path):
            print(f"⚠️ CSV 报告不存在: {csv_path}")
            return photos
        
        # 读取 CSV 数据
        csv_data = {}
        try:
            with open(csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    filename = row.get('filename', '')
                    try:
                        sharpness = float(row.get('head_sharp', 0) or 0)
                    except (ValueError, TypeError):
                        sharpness = 0.0
                    try:
                        topiq = float(row.get('nima_score', 0) or 0)
                    except (ValueError, TypeError):
                        topiq = 0.0
                    csv_data[filename] = {'sharpness': sharpness, 'topiq': topiq}
        except Exception as e:
            print(f"⚠️ 读取 CSV 失败: {e}")
            return photos
        
        # 更新照片数据
        for photo in photos:
            basename = os.path.splitext(os.path.basename(photo.filepath))[0]
            if basename in csv_data:
                photo.sharpness = csv_data[basename]['sharpness']
                photo.topiq = csv_data[basename]['topiq']
        
        return photos
    
    def process_burst_groups(
        self,
        groups: List[BurstGroup],
        output_dir: str,
        exiftool_mgr=None
    ) -> Dict[str, int]:
        """
        处理连拍组：创建子目录、移动文件、设置标签
        
        Args:
            groups: BurstGroup 列表
            output_dir: 输出目录（如 "3星_优选"）
            exiftool_mgr: ExifToolManager 实例（可选）
            
        Returns:
            统计结果 {'groups_processed': n, 'photos_moved': n, 'best_marked': n}
        """
        import shutil
        
        stats = {'groups_processed': 0, 'photos_moved': 0, 'best_marked': 0}
        
        for group in groups:
            if not group.photos or group.count < self.MIN_BURST_COUNT:
                continue
            
            # 创建子目录
            burst_dir = os.path.join(output_dir, f"burst_{group.group_id:03d}")
            os.makedirs(burst_dir, exist_ok=True)
            
            best_photo = group.best_photo
            
            for i, photo in enumerate(group.photos):
                if i == group.best_index:
                    # 最佳照片：保留原位（V4.1: 不再设置紫色标签，避免覆盖飞鸟/对焦标签）
                    stats['best_marked'] += 1
                else:
                    # 非最佳：移入子目录
                    try:
                        dest = os.path.join(burst_dir, os.path.basename(photo.filepath))
                        if os.path.exists(photo.filepath):
                            shutil.move(photo.filepath, dest)
                            stats['photos_moved'] += 1
                    except Exception as e:
                        print(f"⚠️ 移动文件失败: {e}")
            
            stats['groups_processed'] += 1
        
        return stats
    
    def run_full_detection(
        self,
        directory: str,
        rating_dirs: List[str] = None
    ) -> Dict[str, any]:
        """
        运行完整的连拍检测流程
        
        Args:
            directory: 主目录路径
            rating_dirs: 评分子目录列表（默认 ['3星_优选', '2星_良好']）
            
        Returns:
            完整结果
        """
        if rating_dirs is None:
            rating_dirs = ['3星_优选', '2星_良好']
        
        results = {
            'total_photos': 0,
            'photos_with_subsec': 0,
            'groups_detected': 0,
            'groups_by_dir': {}
        }
        
        # 遍历评分目录
        for rating_dir in rating_dirs:
            subdir = os.path.join(directory, rating_dir)
            if not os.path.exists(subdir):
                continue
            
            # 获取文件列表
            extensions = {'.nef', '.rw2', '.arw', '.cr2', '.cr3', '.orf', '.dng'}
            filepaths = []
            for entry in os.scandir(subdir):
                if entry.is_file():
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in extensions:
                        filepaths.append(entry.path)
            
            if not filepaths:
                continue
            
            results['total_photos'] += len(filepaths)
            
            # 读取时间戳
            photos = self.read_timestamps(filepaths)
            results['photos_with_subsec'] += sum(1 for p in photos if p.has_subsec)
            
            # 从 CSV 读取锐度和美学
            csv_path = os.path.join(directory, '.superpicky', 'report.csv')
            photos = self.enrich_from_csv(photos, csv_path)
            
            # 检测连拍组
            groups = self.detect_groups(photos)
            
            # 选择最佳
            groups = self.select_best_in_groups(groups)
            
            results['groups_detected'] += len(groups)
            results['groups_by_dir'][rating_dir] = {
                'photos': len(filepaths),
                'groups': len(groups),
                'group_details': [
                    {
                        'id': g.group_id,
                        'count': g.count,
                        'best': os.path.basename(g.best_photo.filepath) if g.best_photo else None
                    }
                    for g in groups
                ]
            }
        
        return results


# 测试函数
def test_burst_detector():
    """测试连拍检测器"""
    detector = BurstDetector()
    
    # 测试目录
    test_dir = '/Users/jameszhenyu/Desktop/Ti'
    
    if not os.path.exists(test_dir):
        print(f"测试目录不存在: {test_dir}")
        return
    
    # 获取所有图片文件
    extensions = {'.nef', '.rw2', '.arw', '.cr2', '.cr3', '.orf', '.jpg', '.jpeg'}
    filepaths = []
    for entry in os.scandir(test_dir):
        if entry.is_file():
            ext = os.path.splitext(entry.name)[1].lower()
            if ext in extensions:
                filepaths.append(entry.path)
    
    print(f"找到 {len(filepaths)} 个图片文件")
    
    # 读取时间戳
    print("\n读取时间戳...")
    photos = detector.read_timestamps(filepaths)
    
    # 显示结果
    print(f"\n读取到 {len(photos)} 个时间戳：")
    for p in photos[:10]:  # 只显示前 10 个
        subsec_str = f".{p.subsec}" if p.subsec else ""
        dt_str = p.datetime_original.strftime("%Y-%m-%d %H:%M:%S") if p.datetime_original else "无"
        print(f"  {os.path.basename(p.filepath)}: {dt_str}{subsec_str} (评分: {p.rating})")
    
    # 检测连拍组
    print("\n检测连拍组...")
    groups = detector.detect_groups(photos)
    
    print(f"\n发现 {len(groups)} 个连拍组：")
    for group in groups:
        print(f"  组 #{group.group_id}: {group.count} 张照片")
        for p in group.photos:
            print(f"    - {os.path.basename(p.filepath)}")


if __name__ == '__main__':
    test_burst_detector()
