"""
conftest.py — pytest 根配置
将项目根目录加入 sys.path，使 src/、experiments/ 等包可直接导入。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
