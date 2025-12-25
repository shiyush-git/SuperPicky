#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pose-CLI - 鸟类姿态检测命令行工具
用于测试和验证 YOLOv11-pose 模型

使用示例:
    # 检测单张图片
    python pose_cli.py detect image.jpg
    
    # 批量检测目录
    python pose_cli.py detect ./photos --output ./results
    
    # 显示模型信息
    python pose_cli.py info
    
    # 评估模型性能
    python pose_cli.py benchmark ./test_images
"""

import argparse
import sys
import os
from pathlib import Path
from datetime import datetime

# 版本信息
__version__ = "0.1.0"
__app_name__ = "Pose-CLI"


def create_parser() -> argparse.ArgumentParser:
    """创建命令行参数解析器"""
    parser = argparse.ArgumentParser(
        prog="pose-cli",
        description="🐦 Pose-CLI - 鸟类姿态检测命令行工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  pose-cli detect photo.jpg           # 检测单张图片
  pose-cli detect ./photos -o ./out   # 批量检测并输出结果
  pose-cli info                       # 显示模型信息
  pose-cli benchmark ./test           # 性能测试
        """
    )
    
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"{__app_name__} v{__version__}"
    )
    
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="显示详细输出"
    )
    
    # 子命令
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # ========== process 命令 (主功能) ==========
    process_parser = subparsers.add_parser(
        "process",
        help="处理照片目录 - AI检测、评分、EXIF写入"
    )
    process_parser.add_argument(
        "directory",
        type=str,
        help="照片目录路径"
    )
    process_parser.add_argument(
        "--sharpness",
        type=int,
        default=7500,
        help="锐度阈值 (默认: 7500)"
    )
    process_parser.add_argument(
        "--nima",
        type=float,
        default=4.8,
        help="NIMA美学阈值 (默认: 4.8)"
    )
    process_parser.add_argument(
        "--no-organize",
        action="store_true",
        help="不移动文件到分类文件夹"
    )
    process_parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="不删除临时JPG文件"
    )
    process_parser.add_argument(
        "--json",
        action="store_true",
        help="以JSON格式输出统计"
    )
    
    # ========== detect 命令 ==========
    detect_parser = subparsers.add_parser(
        "detect",
        help="检测图片中的鸟类姿态关键点"
    )
    detect_parser.add_argument(
        "input",
        type=str,
        help="输入图片路径或目录"
    )
    detect_parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="输出目录 (默认: 当前目录)"
    )
    detect_parser.add_argument(
        "-m", "--model",
        type=str,
        default=None,
        help="模型路径 (默认: 自动检测)"
    )
    detect_parser.add_argument(
        "--conf",
        type=float,
        default=0.25,
        help="置信度阈值 (默认: 0.25)"
    )
    detect_parser.add_argument(
        "--save-viz",
        action="store_true",
        help="保存可视化结果图片"
    )
    detect_parser.add_argument(
        "--json",
        action="store_true",
        help="以JSON格式输出结果"
    )
    
    # ========== info 命令 ==========
    info_parser = subparsers.add_parser(
        "info",
        help="显示模型和系统信息"
    )
    info_parser.add_argument(
        "-m", "--model",
        type=str,
        default=None,
        help="指定模型路径"
    )
    
    # ========== benchmark 命令 ==========
    bench_parser = subparsers.add_parser(
        "benchmark",
        help="模型性能测试"
    )
    bench_parser.add_argument(
        "input",
        type=str,
        help="测试图片目录"
    )
    bench_parser.add_argument(
        "-n", "--num",
        type=int,
        default=10,
        help="测试图片数量 (默认: 10)"
    )
    bench_parser.add_argument(
        "-m", "--model",
        type=str,
        default=None,
        help="模型路径"
    )
    
    # ========== train 命令 (预留) ==========
    train_parser = subparsers.add_parser(
        "train",
        help="训练或微调模型"
    )
    train_parser.add_argument(
        "dataset",
        type=str,
        help="训练数据集路径"
    )
    
    return parser


def print_banner():
    """打印程序横幅"""
    banner = """
╔═══════════════════════════════════════════════════════════╗
║                    🐦 Pose-CLI v{}                     ║
║           Bird Pose Detection Command Line Tool           ║
╚═══════════════════════════════════════════════════════════╝
    """.format(__version__)
    print(banner)


def cmd_process(args):
    """执行主处理命令 - 对标GUI完整功能"""
    from cli_processor import CLIProcessor
    import json as json_module
    
    print_banner()
    
    # 检查目录
    if not os.path.isdir(args.directory):
        print(f"❌ 错误: 目录不存在 - {args.directory}")
        return 1
    
    # 初始化处理器
    ui_settings = [50, args.sharpness, args.nima, False, 'log']
    processor = CLIProcessor(args.directory, ui_settings, verbose=True)
    
    # 执行处理
    stats = processor.process(
        organize_files=not args.no_organize,
        cleanup_temp=not args.no_cleanup
    )
    
    # JSON输出
    if args.json:
        print("\n" + json_module.dumps(stats, indent=2, ensure_ascii=False))
    
    return 0


def cmd_detect(args):
    """执行检测命令"""
    from pose_detector import PoseDetector
    
    print(f"🔍 检测目标: {args.input}")
    print(f"   置信度阈值: {args.conf}")
    
    # 初始化检测器
    detector = PoseDetector(model_path=args.model)
    
    input_path = Path(args.input)
    
    if input_path.is_file():
        # 单张图片
        results = detector.detect(str(input_path), conf=args.conf)
        _print_detection_results(input_path.name, results, args.json)
        
        if args.save_viz and args.output:
            detector.save_visualization(
                str(input_path), 
                results,
                output_dir=args.output
            )
    
    elif input_path.is_dir():
        # 批量处理目录
        image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp'}
        images = [f for f in input_path.iterdir() 
                  if f.suffix.lower() in image_extensions]
        
        print(f"📁 找到 {len(images)} 张图片")
        
        all_results = []
        for i, img_path in enumerate(images, 1):
            print(f"\n[{i}/{len(images)}] 处理: {img_path.name}")
            results = detector.detect(str(img_path), conf=args.conf)
            _print_detection_results(img_path.name, results, args.json)
            all_results.append({
                "file": img_path.name,
                "results": results
            })
            
            if args.save_viz and args.output:
                detector.save_visualization(
                    str(img_path), 
                    results,
                    output_dir=args.output
                )
        
        # 打印汇总
        _print_summary(all_results)
    else:
        print(f"❌ 错误: 路径不存在 - {args.input}")
        return 1
    
    return 0


def _print_detection_results(filename: str, results: dict, as_json: bool = False):
    """打印单张图片的检测结果"""
    if as_json:
        import json
        print(json.dumps({"file": filename, **results}, indent=2, ensure_ascii=False))
        return
    
    if not results.get("detections"):
        print(f"   ⚠️  未检测到鸟类")
        return
    
    for i, det in enumerate(results["detections"], 1):
        conf = det.get("confidence", 0)
        keypoints = det.get("keypoints", [])
        visible_kps = sum(1 for kp in keypoints if kp.get("visible", False))
        total_kps = len(keypoints)
        
        print(f"   🐦 鸟类 #{i}: 置信度={conf:.2%}, 关键点={visible_kps}/{total_kps}")


def _print_summary(all_results: list):
    """打印批量处理汇总"""
    total = len(all_results)
    detected = sum(1 for r in all_results if r["results"].get("detections"))
    
    print("\n" + "=" * 50)
    print(f"📊 处理完成汇总:")
    print(f"   总图片数: {total}")
    print(f"   检测到鸟类: {detected} ({detected/total:.1%})")
    print(f"   未检测到: {total - detected}")
    print("=" * 50)


def cmd_info(args):
    """显示模型和系统信息"""
    import platform
    
    print_banner()
    
    print("📋 系统信息:")
    print(f"   Python: {platform.python_version()}")
    print(f"   系统: {platform.system()} {platform.release()}")
    print(f"   架构: {platform.machine()}")
    
    # 检查 PyTorch
    try:
        import torch
        print(f"\n🔧 PyTorch:")
        print(f"   版本: {torch.__version__}")
        print(f"   CUDA: {'可用' if torch.cuda.is_available() else '不可用'}")
        print(f"   MPS: {'可用' if torch.backends.mps.is_available() else '不可用'}")
    except ImportError:
        print("\n⚠️  PyTorch 未安装")
    
    # 检查 Ultralytics
    try:
        import ultralytics
        print(f"\n🚀 Ultralytics:")
        print(f"   版本: {ultralytics.__version__}")
    except ImportError:
        print("\n⚠️  Ultralytics 未安装")
    
    # 模型信息
    print("\n📦 模型信息:")
    model_path = args.model if args.model else _find_default_model()
    if model_path:
        print(f"   路径: {model_path}")
        if os.path.exists(model_path):
            size_mb = os.path.getsize(model_path) / (1024 * 1024)
            print(f"   大小: {size_mb:.1f} MB")
        else:
            print(f"   ⚠️  模型文件不存在")
    else:
        print(f"   ⚠️  未找到默认模型")
    
    return 0


def cmd_benchmark(args):
    """执行性能测试"""
    from pose_detector import PoseDetector
    import time
    
    print(f"⏱️  性能测试: {args.input}")
    print(f"   测试数量: {args.num}")
    
    # 收集测试图片
    input_path = Path(args.input)
    image_extensions = {'.jpg', '.jpeg', '.png', '.bmp'}
    images = [f for f in input_path.iterdir() 
              if f.suffix.lower() in image_extensions][:args.num]
    
    if not images:
        print("❌ 未找到测试图片")
        return 1
    
    # 初始化检测器
    detector = PoseDetector(model_path=args.model)
    
    # 预热
    print("\n🔥 预热中...")
    detector.detect(str(images[0]))
    
    # 正式测试
    print("📊 开始测试...")
    times = []
    for img_path in images:
        start = time.perf_counter()
        detector.detect(str(img_path))
        elapsed = time.perf_counter() - start
        times.append(elapsed)
        print(f"   {img_path.name}: {elapsed*1000:.1f}ms")
    
    # 统计
    avg_time = sum(times) / len(times)
    min_time = min(times)
    max_time = max(times)
    fps = 1.0 / avg_time
    
    print("\n" + "=" * 50)
    print(f"📈 性能统计:")
    print(f"   平均耗时: {avg_time*1000:.1f}ms")
    print(f"   最快: {min_time*1000:.1f}ms")
    print(f"   最慢: {max_time*1000:.1f}ms")
    print(f"   FPS: {fps:.1f}")
    print("=" * 50)
    
    return 0


def cmd_train(args):
    """训练命令 (待实现)"""
    print("🚧 训练功能开发中...")
    print(f"   数据集: {args.dataset}")
    return 0


def _find_default_model() -> str:
    """查找默认模型路径"""
    # 按优先级查找
    search_paths = [
        "./models/yolo11s-pose-bird.pt",
        "./models/yolo11s-pose.pt",
        "./models/best.pt",
        "~/runs/pose/train/weights/best.pt",
    ]
    
    for path in search_paths:
        expanded = os.path.expanduser(path)
        if os.path.exists(expanded):
            return expanded
    
    return None


def main():
    """主入口"""
    parser = create_parser()
    args = parser.parse_args()
    
    if args.verbose:
        print_banner()
    
    # 路由到对应命令
    if args.command == "process":
        return cmd_process(args)
    elif args.command == "detect":
        return cmd_detect(args)
    elif args.command == "info":
        return cmd_info(args)
    elif args.command == "benchmark":
        return cmd_benchmark(args)
    elif args.command == "train":
        return cmd_train(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
