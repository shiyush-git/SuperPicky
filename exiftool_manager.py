#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ExifTool管理器
用于设置照片评分和锐度值到EXIF/IPTC元数据
"""

import os
import subprocess
import sys
from typing import Optional, List, Dict
from pathlib import Path


class ExifToolManager:
    """ExifTool管理器 - 使用本地打包的exiftool"""

    def __init__(self):
        """初始化ExifTool管理器"""
        # 获取exiftool路径（支持PyInstaller打包）
        self.exiftool_path = self._get_exiftool_path()

        # 验证exiftool可用性
        if not self._verify_exiftool():
            raise RuntimeError(f"ExifTool不可用: {self.exiftool_path}")

        print(f"✅ ExifTool已加载: {self.exiftool_path}")

    def _get_exiftool_path(self) -> str:
        """获取exiftool可执行文件路径"""
        if hasattr(sys, '_MEIPASS'):
            # PyInstaller打包后的路径
            base_path = sys._MEIPASS
            print(f"🔍 PyInstaller环境检测到")
            print(f"   base_path (sys._MEIPASS): {base_path}")

            # 直接使用 exiftool_bundle/exiftool 路径（唯一打包位置）
            exiftool_path = os.path.join(base_path, 'exiftool_bundle', 'exiftool')
            abs_path = os.path.abspath(exiftool_path)

            print(f"   正在检查 exiftool...")
            print(f"   路径: {abs_path}")
            print(f"   存在: {os.path.exists(abs_path)}")
            print(f"   可执行: {os.access(abs_path, os.X_OK) if os.path.exists(abs_path) else False}")

            if os.path.exists(abs_path) and os.access(abs_path, os.X_OK):
                print(f"   ✅ 找到 exiftool")
                return abs_path
            else:
                print(f"   ⚠️  未找到可执行的 exiftool")
                return abs_path
        else:
            # 开发环境路径
            project_root = os.path.dirname(os.path.abspath(__file__))
            return os.path.join(project_root, 'exiftool')

    def _verify_exiftool(self) -> bool:
        """验证exiftool是否可用"""
        print(f"\n🧪 验证 ExifTool 是否可执行...")
        print(f"   路径: {self.exiftool_path}")
        print(f"   测试命令: {self.exiftool_path} -ver")

        try:
            result = subprocess.run(
                [self.exiftool_path, '-ver'],
                capture_output=True,
                text=True,
                timeout=5
            )
            print(f"   返回码: {result.returncode}")
            print(f"   stdout: {result.stdout.strip()}")
            if result.stderr:
                print(f"   stderr: {result.stderr.strip()}")

            if result.returncode == 0:
                print(f"   ✅ ExifTool 验证成功")
                return True
            else:
                print(f"   ❌ ExifTool 返回非零退出码")
                return False

        except subprocess.TimeoutExpired:
            print(f"   ❌ ExifTool 执行超时（5秒）")
            return False
        except Exception as e:
            print(f"   ❌ ExifTool 验证异常: {type(e).__name__}: {e}")
            return False

    def set_rating_and_pick(
        self,
        file_path: str,
        rating: int,
        pick: int = 0,
        sharpness: float = None,
        nima_score: float = None,
        brisque_score: float = None
    ) -> bool:
        """
        设置照片评分和旗标 (Lightroom标准)

        Args:
            file_path: 文件路径
            rating: 评分 (-1=拒绝, 0=无评分, 1-5=星级)
            pick: 旗标 (-1=排除旗标, 0=无旗标, 1=精选旗标)
            sharpness: 锐度值（可选，写入IPTC:City字段，用于Lightroom排序）
            nima_score: NIMA美学评分（可选，写入IPTC:Province-State字段）
            brisque_score: BRISQUE技术质量评分（可选，写入IPTC:Country-PrimaryLocationName字段）

        Returns:
            是否成功
        """
        if not os.path.exists(file_path):
            print(f"❌ 文件不存在: {file_path}")
            return False

        # 构建exiftool命令
        cmd = [
            self.exiftool_path,
            f'-Rating={rating}',
            f'-XMP:Pick={pick}',
        ]

        # 如果提供了锐度值，写入IPTC:City字段（补零到6位，确保文本排序正确）
        # 格式：000.00 到 999.99，例如：004.68, 100.50
        if sharpness is not None:
            sharpness_str = f'{sharpness:06.2f}'  # 6位总宽度，2位小数，前面补零
            cmd.append(f'-IPTC:City={sharpness_str}')

        # V3.1: NIMA美学评分 → IPTC:Province-State（省/州）
        # 格式：00.00 到 10.00（NIMA范围0-10）
        if nima_score is not None:
            nima_str = f'{nima_score:05.2f}'  # 5位总宽度，2位小数，前面补零
            cmd.append(f'-IPTC:Province-State={nima_str}')

        # V3.1: BRISQUE技术质量评分 → IPTC:Country-PrimaryLocationName（国家）
        # 格式：000.00 到 100.00（BRISQUE范围0-100，越低越好）
        if brisque_score is not None:
            brisque_str = f'{brisque_score:06.2f}'  # 6位总宽度，2位小数，前面补零
            cmd.append(f'-IPTC:Country-PrimaryLocationName={brisque_str}')

        cmd.extend(['-overwrite_original', file_path])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                filename = os.path.basename(file_path)
                pick_desc = {-1: "排除旗标", 0: "无旗标", 1: "精选旗标"}.get(pick, str(pick))
                sharpness_info = f", 锐度={sharpness:06.2f}" if sharpness is not None else ""
                nima_info = f", NIMA={nima_score:05.2f}" if nima_score is not None else ""
                brisque_info = f", BRISQUE={brisque_score:06.2f}" if brisque_score is not None else ""
                print(f"✅ EXIF已更新: {filename} (Rating={rating}, Pick={pick_desc}{sharpness_info}{nima_info}{brisque_info})")
                return True
            else:
                print(f"❌ ExifTool错误: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            print(f"❌ ExifTool超时: {file_path}")
            return False
        except Exception as e:
            print(f"❌ ExifTool异常: {e}")
            return False

    def batch_set_metadata(
        self,
        files_metadata: List[Dict[str, any]]
    ) -> Dict[str, int]:
        """
        批量设置元数据（使用-execute分隔符，支持不同文件不同参数）

        Args:
            files_metadata: 文件元数据列表
                [
                    {'file': 'path1.NEF', 'rating': 3, 'pick': 1, 'sharpness': 95.3, 'nima_score': 7.5, 'brisque_score': 25.0},
                    {'file': 'path2.NEF', 'rating': 2, 'pick': 0, 'sharpness': 78.5, 'nima_score': 6.8, 'brisque_score': 35.2},
                    {'file': 'path3.NEF', 'rating': -1, 'pick': -1, 'sharpness': 45.2, 'nima_score': 5.2, 'brisque_score': 55.8},
                ]

        Returns:
            统计结果 {'success': 成功数, 'failed': 失败数}
        """
        stats = {'success': 0, 'failed': 0}

        # ExifTool批量模式：使用 -execute 分隔符为每个文件单独设置参数
        # 格式: exiftool -TAG1=value1 file1 -overwrite_original -execute -TAG2=value2 file2 -overwrite_original -execute ...
        cmd = [self.exiftool_path]

        for item in files_metadata:
            file_path = item['file']
            rating = item.get('rating', 0)
            pick = item.get('pick', 0)
            sharpness = item.get('sharpness', None)
            nima_score = item.get('nima_score', None)
            brisque_score = item.get('brisque_score', None)

            if not os.path.exists(file_path):
                print(f"⏭️  跳过不存在的文件: {file_path}")
                stats['failed'] += 1
                continue

            # 为这个文件添加命令参数
            cmd.extend([
                f'-Rating={rating}',
                f'-XMP:Pick={pick}',
            ])

            # 如果提供了锐度值，写入IPTC:City字段（补零到6位，确保文本排序正确）
            # 格式：000.00 到 999.99，例如：004.68, 100.50
            if sharpness is not None:
                sharpness_str = f'{sharpness:06.2f}'  # 6位总宽度，2位小数，前面补零
                cmd.append(f'-IPTC:City={sharpness_str}')

            # V3.1: NIMA美学评分 → IPTC:Province-State（省/州）
            if nima_score is not None:
                nima_str = f'{nima_score:05.2f}'
                cmd.append(f'-IPTC:Province-State={nima_str}')

            # V3.1: BRISQUE技术质量评分 → IPTC:Country-PrimaryLocationName（国家）
            if brisque_score is not None:
                brisque_str = f'{brisque_score:06.2f}'
                cmd.append(f'-IPTC:Country-PrimaryLocationName={brisque_str}')

            cmd.append(file_path)
            cmd.append('-overwrite_original')  # 放在每个文件之后

            # 添加 -execute 分隔符（除了最后一个文件）
            cmd.append('-execute')

        # 执行批量命令
        try:
            # V3.1.2: 只在处理多个文件时显示消息（单文件处理不显示，避免刷屏）
            if len(files_metadata) > 1:
                print(f"📦 批量处理 {len(files_metadata)} 个文件...")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5分钟超时
            )

            if result.returncode == 0:
                stats['success'] = len(files_metadata) - stats['failed']
                # V3.1.2: 只在处理多个文件时显示完成消息
                if len(files_metadata) > 1:
                    print(f"✅ 批量处理完成: {stats['success']} 成功, {stats['failed']} 失败")
            else:
                print(f"❌ 批量处理失败: {result.stderr}")
                stats['failed'] = len(files_metadata)

        except Exception as e:
            print(f"❌ 批量处理异常: {e}")
            stats['failed'] = len(files_metadata)

        return stats

    def read_metadata(self, file_path: str) -> Optional[Dict]:
        """
        读取文件的元数据

        Args:
            file_path: 文件路径

        Returns:
            元数据字典或None
        """
        if not os.path.exists(file_path):
            return None

        cmd = [
            self.exiftool_path,
            '-Rating',
            '-XMP:Pick',
            '-XMP:Label',
            '-IPTC:City',
            '-IPTC:Country-PrimaryLocationName',
            '-IPTC:Province-State',
            '-json',
            file_path
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                import json
                data = json.loads(result.stdout)
                return data[0] if data else None
            else:
                return None

        except Exception as e:
            print(f"❌ 读取元数据失败: {e}")
            return None

    def reset_metadata(self, file_path: str) -> bool:
        """
        重置照片的评分和旗标为初始状态

        Args:
            file_path: 文件路径

        Returns:
            是否成功
        """
        if not os.path.exists(file_path):
            print(f"❌ 文件不存在: {file_path}")
            return False

        # 删除Rating、Pick、City、Country和Province-State字段
        cmd = [
            self.exiftool_path,
            '-Rating=',
            '-XMP:Pick=',
            '-XMP:Label=',
            '-IPTC:City=',
            '-IPTC:Country-PrimaryLocationName=',
            '-IPTC:Province-State=',
            '-overwrite_original',
            file_path
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                filename = os.path.basename(file_path)
                print(f"✅ EXIF已重置: {filename}")
                return True
            else:
                print(f"❌ ExifTool错误: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            print(f"❌ ExifTool超时: {file_path}")
            return False
        except Exception as e:
            print(f"❌ ExifTool异常: {e}")
            return False

    def batch_reset_metadata(self, file_paths: List[str], batch_size: int = 50, log_callback=None, i18n=None) -> Dict[str, int]:
        """
        批量重置元数据（强制清除所有EXIF评分字段）

        Args:
            file_paths: 文件路径列表
            batch_size: 每批处理的文件数量（默认50，避免命令行过长）
            log_callback: 日志回调函数（可选，用于UI显示）
            i18n: I18n instance for internationalization (optional)

        Returns:
            统计结果 {'success': 成功数, 'failed': 失败数}
        """
        def log(msg):
            """统一日志输出"""
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        stats = {'success': 0, 'failed': 0}
        total = len(file_paths)

        if i18n:
            log(i18n.t("logs.batch_reset_start", total=total))
        else:
            log(f"📦 开始重置 {total} 个文件的EXIF元数据...")
            log(f"   强制清除所有评分字段\n")

        # 分批处理（避免命令行参数过长）
        for batch_start in range(0, total, batch_size):
            batch_end = min(batch_start + batch_size, total)
            batch_files = file_paths[batch_start:batch_end]

            # 过滤不存在的文件
            valid_files = [f for f in batch_files if os.path.exists(f)]
            stats['failed'] += len(batch_files) - len(valid_files)

            if not valid_files:
                continue

            # 构建ExifTool命令（移除-if条件，强制重置）
            cmd = [
                self.exiftool_path,
                '-Rating=',
                '-XMP:Pick=',
                '-XMP:Label=',
                '-IPTC:City=',
                '-IPTC:Country-PrimaryLocationName=',
                '-IPTC:Province-State=',
                '-overwrite_original'
            ] + valid_files

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120
                )

                if result.returncode == 0:
                    # 所有文件都被处理
                    stats['success'] += len(valid_files)
                    if i18n:
                        log(i18n.t("logs.batch_progress", start=batch_start+1, end=batch_end, success=len(valid_files)))
                    else:
                        log(f"  ✅ 批次 {batch_start+1}-{batch_end}: {len(valid_files)} 个文件已处理")
                else:
                    stats['failed'] += len(valid_files)
                    if i18n:
                        log(f"  ❌ {i18n.t('logs.batch_failed', start=batch_start+1, end=batch_end, error=result.stderr.strip())}")
                    else:
                        log(f"  ❌ 批次 {batch_start+1}-{batch_end} 失败: {result.stderr.strip()}")

            except subprocess.TimeoutExpired:
                stats['failed'] += len(valid_files)
                if i18n:
                    log(f"  ⏱️  {i18n.t('logs.batch_timeout', start=batch_start+1, end=batch_end)}")
                else:
                    log(f"  ⏱️  批次 {batch_start+1}-{batch_end} 超时")
            except Exception as e:
                stats['failed'] += len(valid_files)
                if i18n:
                    log(f"  ❌ {i18n.t('logs.batch_error', start=batch_start+1, end=batch_end, error=str(e))}")
                else:
                    log(f"  ❌ 批次 {batch_start+1}-{batch_end} 错误: {e}")

        if i18n:
            log(f"\n{i18n.t('logs.batch_complete', success=stats['success'], failed=stats['failed'])}")
        else:
            log(f"\n✅ 批量重置完成: {stats['success']} 成功, {stats['failed']} 失败")
        return stats

    def restore_files_from_manifest(self, dir_path: str, log_callback=None) -> Dict[str, int]:
        """
        V3.3: 根据 manifest 将文件恢复到原始位置
        
        Args:
            dir_path: str, 原始目录路径
            log_callback: callable, 日志回调函数
        
        Returns:
            dict: {'restored': int, 'failed': int, 'not_found': int}
        """
        import json
        import shutil
        
        def log(msg):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)
        
        manifest_path = os.path.join(dir_path, "_superpicky_manifest.json")
        
        if not os.path.exists(manifest_path):
            log("ℹ️  未找到 manifest 文件，跳过文件恢复")
            return {'restored': 0, 'failed': 0, 'not_found': 0}
        
        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
        except Exception as e:
            log(f"⚠️  读取 manifest 失败: {e}")
            return {'restored': 0, 'failed': 0, 'not_found': 0}
        
        stats = {'restored': 0, 'failed': 0, 'not_found': 0}
        folders_to_check = set()
        
        files = manifest.get('files', [])
        if not files:
            log("ℹ️  manifest 中没有文件记录")
            return stats
        
        log(f"\n📂 恢复 {len(files)} 个文件到原始位置...")
        
        # 移动文件回原位置
        for file_info in files:
            filename = file_info['filename']
            folder = file_info['folder']
            
            src_path = os.path.join(dir_path, folder, filename)
            dst_path = os.path.join(dir_path, filename)
            
            folders_to_check.add(os.path.join(dir_path, folder))
            
            if not os.path.exists(src_path):
                stats['not_found'] += 1
                log(f"  ⚠️  文件不存在: {folder}/{filename}")
                continue
            
            # 检查目标位置是否已有同名文件
            if os.path.exists(dst_path):
                stats['failed'] += 1
                log(f"  ⚠️  目标已存在，跳过: {filename}")
                continue
            
            try:
                shutil.move(src_path, dst_path)
                stats['restored'] += 1
            except Exception as e:
                stats['failed'] += 1
                log(f"  ❌ 恢复失败: {filename} - {e}")
        
        # 删除空的分类文件夹
        for folder_path in folders_to_check:
            if os.path.exists(folder_path):
                try:
                    # 检查文件夹是否为空
                    if not os.listdir(folder_path):
                        os.rmdir(folder_path)
                        folder_name = os.path.basename(folder_path)
                        log(f"  🗑️  删除空文件夹: {folder_name}/")
                except Exception as e:
                    log(f"  ⚠️  删除文件夹失败: {e}")
        
        # 删除 manifest 文件
        try:
            os.remove(manifest_path)
            log("  🗑️  已删除 manifest 文件")
        except Exception as e:
            log(f"  ⚠️  删除 manifest 失败: {e}")
        
        log(f"✅ 文件恢复完成: 已恢复 {stats['restored']} 张")
        if stats['not_found'] > 0:
            log(f"⚠️  {stats['not_found']} 张文件未找到")
        if stats['failed'] > 0:
            log(f"❌ {stats['failed']} 张恢复失败")
        
        return stats


# 全局实例
exiftool_manager = None


def get_exiftool_manager() -> ExifToolManager:
    """获取ExifTool管理器单例"""
    global exiftool_manager
    if exiftool_manager is None:
        exiftool_manager = ExifToolManager()
    return exiftool_manager


# 便捷函数
def set_photo_metadata(file_path: str, rating: int, pick: int = 0, sharpness: float = None,
                      nima_score: float = None, brisque_score: float = None) -> bool:
    """设置照片元数据的便捷函数"""
    manager = get_exiftool_manager()
    return manager.set_rating_and_pick(file_path, rating, pick, sharpness, nima_score, brisque_score)


if __name__ == "__main__":
    # 测试代码
    print("=== ExifTool管理器测试 ===\n")

    # 初始化管理器
    manager = ExifToolManager()

    print("✅ ExifTool管理器初始化完成")

    # 如果提供了测试文件路径，执行实际测试
    test_files = [
        "/Volumes/990PRO4TB/2025/2025-08-19/_Z9W6782.NEF",
        "/Volumes/990PRO4TB/2025/2025-08-19/_Z9W6783.NEF",
        "/Volumes/990PRO4TB/2025/2025-08-19/_Z9W6784.NEF"
    ]

    # 检查测试文件是否存在
    available_files = [f for f in test_files if os.path.exists(f)]

    if available_files:
        print(f"\n🧪 发现 {len(available_files)} 个测试文件，执行实际测试...")

        # 0️⃣ 先重置所有测试文件
        print("\n0️⃣ 重置测试文件元数据:")
        reset_stats = manager.batch_reset_metadata(available_files)
        print(f"   结果: {reset_stats}\n")

        # 单个文件测试 - 优秀照片
        print("\n1️⃣ 单个文件测试 - 优秀照片 (3星 + 精选旗标):")
        success = manager.set_rating_and_pick(
            available_files[0],
            rating=3,
            pick=1
        )
        print(f"   结果: {'✅ 成功' if success else '❌ 失败'}")

        # 批量测试
        if len(available_files) >= 2:
            print("\n2️⃣ 批量处理测试:")
            batch_data = [
                {'file': available_files[0], 'rating': 3, 'pick': 1},
                {'file': available_files[1], 'rating': 2, 'pick': 0},
            ]
            if len(available_files) >= 3:
                batch_data.append(
                    {'file': available_files[2], 'rating': -1, 'pick': -1}
                )

            stats = manager.batch_set_metadata(batch_data)
            print(f"   结果: {stats}")

        # 读取元数据验证
        print("\n3️⃣ 读取元数据验证:")
        for i, file_path in enumerate(available_files, 1):
            metadata = manager.read_metadata(file_path)
            filename = os.path.basename(file_path)
            if metadata:
                print(f"   {filename}:")
                print(f"      Rating: {metadata.get('Rating', 'N/A')}")
                print(f"      Pick: {metadata.get('Pick', 'N/A')}")
                print(f"      Label: {metadata.get('Label', 'N/A')}")
    else:
        print("\n⚠️  未找到测试文件，跳过实际测试")
