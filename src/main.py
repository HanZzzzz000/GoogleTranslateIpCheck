import asyncio
import sys
import logging
from pathlib import Path
import pystray
from PIL import Image
import winreg
import ctypes
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json

from core.ip_checker import IPChecker
from core.hosts_manager import HostsManager

class GoogleTranslateIPCheck:
    def __init__(self):
        self._setup_logging()
        logging.info("程序启动")
        
        self.ip_checker = IPChecker()
        self.hosts_manager = HostsManager()
        self.icon = None
        self.running = True
        self.check_interval = self._load_check_interval()
        self.last_check_time = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.loop = asyncio.new_event_loop()
        self.autostart_enabled = self._is_autostart_enabled()

    def _setup_logging(self):
        """设置日志"""
        if getattr(sys, 'frozen', False):
            base_dir = Path(sys.executable).parent
        else:
            base_dir = Path(__file__).parent.parent
        
        self.log_dir = base_dir / 'logs'
        self.log_file = self.log_dir / 'app.log'
        self.log_dir.mkdir(exist_ok=True)
        
        # 清理旧日志
        self._clean_old_logs()
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.log_file, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )

    def _clean_old_logs(self):
        """清理3天前的日志"""
        try:
            if self.log_file.exists():
                # 获取日志文件的修改时间
                mtime = datetime.fromtimestamp(self.log_file.stat().st_mtime)
                # 如果日志文件超过3天
                if datetime.now() - mtime > timedelta(days=3):
                    # 备份旧日志
                    backup_name = f"app_{mtime.strftime('%Y%m%d_%H%M%S')}.log"
                    backup_dir = self.log_dir / 'history'
                    backup_dir.mkdir(exist_ok=True)
                    
                    # 如果备份目录中的文件超过10个，删除最旧的
                    backup_files = sorted(backup_dir.glob('app_*.log'))
                    while len(backup_files) >= 10:
                        backup_files[0].unlink()
                        backup_files = backup_files[1:]
                    
                    # 移动当前日志到备份目录
                    self.log_file.rename(backup_dir / backup_name)
                    logging.info(f"已将旧日志备份为: {backup_name}")
        except Exception as e:
            print(f"清理日志文件失败: {e}")

    def _load_check_interval(self) -> int:
        """加载检查间隔时间"""
        config_file = self._get_config_path()
        try:
            if config_file.exists():
                data = json.loads(config_file.read_text(encoding='utf-8'))
                interval = data.get('check_interval', 3600)
                return max(300, min(interval, 86400))  # 限制在5分钟到24小时之间
        except Exception as e:
            logging.error(f"加载配置文件失败: {e}")
        return 3600  # 默认1小时

    def _save_check_interval(self, interval: int):
        """保存检查间隔时间"""
        config_file = self._get_config_path()
        try:
            data = {}
            if config_file.exists():
                data = json.loads(config_file.read_text(encoding='utf-8'))
            data['check_interval'] = interval
            config_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
            logging.info(f"已保存新的检查间隔: {interval}秒")
        except Exception as e:
            logging.error(f"保存配置文件失败: {e}")
            self.icon.notify("设置保存失败", "无法保存检查间隔设置")

    def _get_config_path(self) -> Path:
        """获取配置文件路径"""
        if getattr(sys, 'frozen', False):
            base_dir = Path(sys.executable).parent
        else:
            base_dir = Path(__file__).parent.parent
        
        # 确保配置目录存在
        config_dir = base_dir / 'config'
        config_dir.mkdir(exist_ok=True)
        
        return config_dir / 'settings.json'

    def create_tray_icon(self):
        icon_path = Path(__file__).parent / "assets" / "icon.png"
        image = Image.open(str(icon_path))
        
        # 创建检查间隔子菜单
        def create_interval_item(name: str, seconds: int):
            return pystray.MenuItem(
                name,
                lambda item: self.set_check_interval(seconds),
                checked=lambda item: self.check_interval == seconds
            )
        
        check_interval_menu = (
            create_interval_item("5分钟", 300),
            create_interval_item("15分钟", 900),
            create_interval_item("30分钟", 1800),
            create_interval_item("1小时", 3600),
            create_interval_item("2小时", 7200),
            create_interval_item("4小时", 14400),
            create_interval_item("8小时", 28800),
            create_interval_item("12小时", 43200),
            create_interval_item("24小时", 86400),
        )

        menu = (
            pystray.MenuItem("立即检查IP", self._run_check_now),
            pystray.MenuItem("检查间隔", pystray.Menu(*check_interval_menu)),
            pystray.MenuItem(
                "开机自启", 
                self.toggle_autostart, 
                checked=lambda item: self.autostart_enabled
            ),
            pystray.MenuItem("退出", self.quit_app)
        )
        self.icon = pystray.Icon("Google翻译IP检查", image, "Google翻译IP检查", menu)
        logging.info("系统托盘图标创建成功")

    def _is_autostart_enabled(self):
        """检查是否已启用开机自启"""
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ
            )
            winreg.QueryValueEx(key, "GoogleTranslateIPCheck")
            winreg.CloseKey(key)
            return True
        except WindowsError:
            return False

    async def check_ip_task(self):
        logging.info("开始IP检查任务")
        last_log_clean = datetime.now()
        
        try:
            # 启动时检查
            await self._check_ip(is_startup=True)
            logging.info("启动检查完成")
            self.last_check_time = datetime.now()  # 记录首次检查时间
        except Exception as e:
            logging.error(f"启动检查出错: {e}")
        
        while self.running:
            try:
                # 计算距离下次检查的时间
                if self.last_check_time:
                    next_check = self.last_check_time + timedelta(seconds=self.check_interval)
                    now = datetime.now()
                    if now < next_check:
                        wait_seconds = (next_check - now).total_seconds()
                        await asyncio.sleep(wait_seconds)
                    
                # 执行检查
                await self._check_ip()
                self.last_check_time = datetime.now()
                logging.info(f"下次检查时间: {(self.last_check_time + timedelta(seconds=self.check_interval)).strftime('%Y-%m-%d %H:%M:%S')}")

                # 检查是否需要清理日志
                if datetime.now() - last_log_clean > timedelta(days=3):
                    self._clean_old_logs()
                    last_log_clean = datetime.now()
            except Exception as e:
                logging.error(f"IP检查任务出错: {e}")
                await asyncio.sleep(60)  # 出错后等待1分钟再重试

    async def _check_ip(self, is_manual_check=False, is_startup=False):
        """检查IP"""
        try:
            if is_startup:
                logging.info("启动时检查IP")
                self.icon.notify("启动检查", "正在检查当前IP可用性...")
            else:
                logging.info(f"{'手动' if is_manual_check else '自动'}检查IP")
            
            current_ip = self.hosts_manager.get_current_ip()
            logging.info(f"当前hosts文件中的IP: {current_ip}")
            
            is_valid, delay = await self.ip_checker.test_ip(current_ip)
            if not is_valid or delay > 100:  # 如果IP不可用或延迟太高
                status = "不可用" if not is_valid else f"延迟较高({delay:.0f}ms)"
                logging.info(f"当前IP {status}, 尝试获取新IP")
                if is_startup:
                    self.icon.notify("IP检查", f"当前IP {status}，正在获取新IP...")
                
                # 定义回调函数
                async def update_ip(new_ip):
                    self.hosts_manager.update_hosts(new_ip)
                    logging.info(f"已更新hosts文件，新IP: {new_ip}")

                # 先从缓存中查找低延迟IP
                result = await self.ip_checker.get_available_ips(
                    target_delay=100,
                    skip_cache=False,
                    callback=update_ip
                )
                if result and len(result) == 2:
                    new_ip, new_delay = result
                    self.icon.notify("IP更新成功", f"已更新为新IP: {new_ip} (延迟: {new_delay:.0f}ms)")
                else:
                    logging.info("缓存中没有低延迟IP，尝试从远程获取")
                    if is_startup:
                        self.icon.notify("IP更新", "正在从远程获取新IP列表...")
                    result = await self.ip_checker.get_available_ips(
                        target_delay=100,
                        skip_cache=True,
                        callback=update_ip
                    )
                    if result:
                        new_ip, new_delay = result
                        self.icon.notify("IP更新成功", f"已更新为新IP: {new_ip} (延迟: {new_delay:.0f}ms)")

                    if not result:
                        # 如果没有找到低于100ms的IP，获取���有可用IP中延迟最低的
                        logging.info("未找到延迟低于100ms的IP，尝试获取最优IP")
                        if is_startup:
                            self.icon.notify("IP更新", "未找到低延迟IP，正在获取最优IP...")
                        best_ip = await self.ip_checker.get_best_available_ip()
                        if best_ip:
                            self.hosts_manager.update_hosts(best_ip)
                            logging.info(f"已更新为最优IP: {best_ip}")
                            self.icon.notify("IP更新", f"未找到低延迟IP，已更新为最优IP: {best_ip}")
                        else:
                            logging.warning("未找到任何可用的IP")
                            self.icon.notify("IP更新失败", "未找到可用的IP，请稍后重试")
            else:
                logging.info(f"当前IP可用，延迟: {delay:.0f}ms")
                if self.last_check_time is None or is_startup:
                    # 第一次检查或启动时显示通知
                    self.icon.notify("IP检查", f"当前IP可用，延迟: {delay:.0f}ms")
            
            self.last_check_time = datetime.now()
        except Exception as e:
            logging.error(f"检查IP出错: {e}")

    def _run_check_now(self, icon):
        logging.info("手动触发IP检查")
        icon.notify("正在检查", "开始检查IP可用性...")
        asyncio.run_coroutine_threadsafe(self._check_ip(is_manual_check=True), self.loop)

    def toggle_autostart(self, icon, item):
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_ALL_ACCESS
        )
        try:
            if self.autostart_enabled:
                winreg.DeleteValue(key, "GoogleTranslateIPCheck")
                logging.info("已关闭开机自启")
                icon.notify("设置更新", "已关闭开机自启")
                self.autostart_enabled = False
            else:
                exe_path = sys.argv[0] if getattr(sys, 'frozen', False) else sys.executable
                winreg.SetValueEx(key, "GoogleTranslateIPCheck", 0, winreg.REG_SZ, exe_path)
                logging.info("已开启开机自启")
                icon.notify("设置更新", "已开启开机自启")
                self.autostart_enabled = True
        except Exception as e:
            logging.error(f"修改开机自启设置失败: {e}")
            icon.notify("错", "修改开机自启设置失败")
        finally:
            winreg.CloseKey(key)

    def quit_app(self, icon):
        logging.info("程序正在退出...")
        self.running = False
        icon.stop()
        self.loop.stop()
        asyncio.run_coroutine_threadsafe(self.ip_checker.close(), self.loop)

    async def run_async(self):
        check_task = asyncio.create_task(self.check_ip_task())
        
        while self.running:
            await asyncio.sleep(1)
        
        check_task.cancel()
        try:
            await check_task
        except asyncio.CancelledError:
            pass
        logging.info("异步任务已清理完成")

    def run(self):
        if not ctypes.windll.shell32.IsUserAnAdmin():
            logging.warning("需要管理员权限，正在请求...")
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, " ".join(sys.argv), None, 1
            )
            sys.exit()

        self.create_tray_icon()
        
        def run_loop():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.run_async())
        
        self.executor.submit(run_loop)
        self.icon.run()

    def set_check_interval(self, interval: int):
        """设置检查间隔"""
        self.check_interval = interval
        self._save_check_interval(interval)
        
        # 重置定时器
        asyncio.run_coroutine_threadsafe(self._reset_check_timer(), self.loop)
        
        # 计算下次检查时间
        next_check = datetime.now() + timedelta(seconds=interval)
        self.icon.notify(
            "设置已更新",
            f"检查间隔已设置为: {interval//3600}小时{interval%3600//60}分钟\n"
            f"下次检查时间: {next_check.strftime('%H:%M:%S')}"
        )

    async def _reset_check_timer(self):
        """重置检查定时器"""
        self.last_check_time = datetime.now()
        logging.info(f"已重置检查定时器，下次检查时间: {(self.last_check_time + timedelta(seconds=self.check_interval)).strftime('%Y-%m-%d %H:%M:%S')}")

def main():
    app = GoogleTranslateIPCheck()
    app.run()

if __name__ == "__main__":
    main()