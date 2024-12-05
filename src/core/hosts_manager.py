import re
from pathlib import Path
import logging

class HostsManager:
    def __init__(self):
        self.hosts_path = Path(r"C:\Windows\System32\drivers\etc\hosts")
        self.domain = "translate.googleapis.com"

    def get_current_ip(self):
        """获取当前hosts文件中的IP"""
        if not self.hosts_path.exists():
            return None

        try:
            content = self.hosts_path.read_text(encoding='utf-8')
            pattern = rf"(\d+\.\d+\.\d+\.\d+)\s+{self.domain}"
            match = re.search(pattern, content)
            return match.group(1) if match else None
        except Exception as e:
            logging.error(f"读取hosts文件失败: {e}")
            return None

    def update_hosts(self, new_ip):
        """更新hosts文件"""
        if not self.hosts_path.exists():
            return False

        try:
            content = self.hosts_path.read_text(encoding='utf-8')
            pattern = rf".*{self.domain}.*\n?"
            
            # 删除旧的记录
            content = re.sub(pattern, "", content)
            
            # 添加新记录
            content += f"\n{new_ip} {self.domain}\n"
            
            # 写入文件
            self.hosts_path.write_text(content, encoding='utf-8')
            return True
        except Exception as e:
            logging.error(f"更新hosts文件失败: {e}")
            return False 