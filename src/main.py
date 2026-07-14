"""
股票盯盘看板 - 主入口
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))


def run():
    """运行每日选股流程"""
    from src.logging_config import setup_logging
    setup_logging()
    from src.orchestrator import main
    main()


def serve():
    """启动 Web 服务"""
    from src.logging_config import setup_logging
    setup_logging()
    from src.web.routes import run_server
    run_server()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="价值投资选股看板")
    parser.add_argument("command", nargs="?", default="serve",
                        choices=["run", "serve", "update"],
                        help="run=运行选股, serve=启动Web服务(默认)")
    args = parser.parse_args()

    if args.command == "run" or args.command == "update":
        run()
    else:
        serve()
