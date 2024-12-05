import aiohttp
import logging
import asyncio
from typing import List, Tuple, Optional
import json
from pathlib import Path
import sys
import ssl
import time

class IPChecker:
    def __init__(self):
        self.timeout = aiohttp.ClientTimeout(total=3, connect=2)
        self.test_url = "/translate_a/single?client=gtx&sl=zh-CN&tl=en&dt=t&q=test"
        self.session = None
        self.ip_urls = {
            "ipv4": "https://ghp.ci/https://raw.githubusercontent.com/Ponderfly/GoogleTranslateIpCheck/master/src/GoogleTranslateIpCheck/GoogleTranslateIpCheck/ip.txt",
            "ipv6": "https://ghp.ci/https://raw.githubusercontent.com/Ponderfly/GoogleTranslateIpCheck/master/src/GoogleTranslateIpCheck/GoogleTranslateIpCheck/IPv6.txt"
        }
        
        # 使用程序所在目录
        if getattr(sys, 'frozen', False):
            base_dir = Path(sys.executable).parent
        else:
            base_dir = Path(__file__).parent.parent.parent
            
        self.cache_dir = base_dir / 'cache'
        self.cache_dir.mkdir(exist_ok=True)
        
        # 创建SSL上下文，禁用证书验证
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    async def _get_session(self):
        if self.session is None:
            conn = aiohttp.TCPConnector(
                ssl=self.ssl_context,
                limit=500,
                ttl_dns_cache=300,
                use_dns_cache=True,
                force_close=True,
                enable_cleanup_closed=True  # 自动清理关闭的连接
            )
            self.session = aiohttp.ClientSession(
                timeout=self.timeout,
                connector=conn,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
        return self.session

    async def test_ip_once(self, ip: str) -> Tuple[bool, float]:
        """测试单次IP延迟"""
        try:
            session = await self._get_session()
            start_time = time.time()
            url = f"https://{ip}{self.test_url}"
            async with session.get(
                url,
                headers={
                    "Host": "translate.googleapis.com",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                },
                verify_ssl=False,
                allow_redirects=False
            ) as response:
                elapsed = (time.time() - start_time) * 1000
                return response.status == 200, elapsed
        except Exception:
            return False, 0

    async def test_ip(self, ip: str, test_times: int = 5) -> Tuple[bool, float]:
        """
        异步测试IP是否可用，并返回延迟时间
        :param ip: 要测试的IP
        :param test_times: 测试次数，默认5次
        :return: (是否可用, 平均延迟)
        """
        if not ip:
            return False, 0
            
        # 并发执行多次测试
        tasks = [self.test_ip_once(ip) for _ in range(test_times)]
        results = await asyncio.gather(*tasks)
        
        # 收集成功的测试结果
        valid_delays = [delay for is_valid, delay in results if is_valid]
        
        if valid_delays:
            avg_delay = sum(valid_delays) / len(valid_delays)
            logging.info(f"测试IP {ip}: 可用, 平均延迟: {avg_delay:.0f}ms (测试{len(valid_delays)}次)")
            return True, avg_delay
        return False, 0

    async def _fetch_remote_ips(self, url: str, max_retries: int = 3) -> List[str]:
        """
        从远程获取IP列表
        :param url: 远程URL
        :param max_retries: 最大重试次数
        """
        # 使用更长的超时时间获取IP列表
        long_timeout = aiohttp.ClientTimeout(total=30, connect=10)
        
        for retry in range(max_retries):
            try:
                session = await self._get_session()
                async with session.get(
                    url,
                    verify_ssl=False,
                    timeout=long_timeout,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    }
                ) as response:
                    if response.status == 200:
                        content = await response.text()
                        ips = [ip.strip() for ip in content.splitlines() if ip.strip()]
                        if ips:
                            return ips
                        logging.warning(f"获取到的IP列表为空")
                    else:
                        logging.warning(f"取IP列表失败，HTTP状态码: {response.status}")
            except asyncio.TimeoutError:
                logging.warning(f"获取IP列表超时 (重试 {retry + 1}/{max_retries})")
            except Exception as e:
                logging.error(f"获取IP列表出错: {e} (重试 {retry + 1}/{max_retries})")
            
            if retry < max_retries - 1:
                # 使用指数退避策略
                await asyncio.sleep(2 ** retry)
        
        logging.error(f"获取IP列表失败，已重试{max_retries}次")
        return []

    async def _load_cached_ips(self) -> List[str]:
        """加载缓存的IP列表"""
        cache_file = self.cache_dir / "ip_cache.json"
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding='utf-8'))
                return data.get("ips", [])
            except Exception as e:
                logging.error(f"读取IP缓存失败: {e}")
        return []

    def _save_ips_to_cache(self, ips: List[str]):
        """保存IP列表到缓存"""
        cache_file = self.cache_dir / "ip_cache.json"
        try:
            cache_file.write_text(
                json.dumps({"ips": ips}, ensure_ascii=False), 
                encoding='utf-8'
            )
        except Exception as e:
            logging.error(f"保存IP缓存失败: {e}")

    async def get_available_ips(self, target_delay: float = None, skip_cache: bool = False, callback=None) -> List[str]:
        """获取可用的IP列表"""
        logging.info("开始获取可用IP列表")
        best_ip = None
        
        # 检查缓存（除非指定跳过）
        if not skip_cache:
            cached_ips = await self._load_cached_ips()
            if cached_ips:
                logging.info(f"从缓存加载了 {len(cached_ips)} 个IP")
                tasks = [self.test_ip(ip) for ip in cached_ips]
                results = await asyncio.gather(*tasks)
                
                valid_ips = [(ip, delay) for (ip, (is_valid, delay)) in zip(cached_ips, results) if is_valid]
                if valid_ips:
                    sorted_ips = sorted(valid_ips, key=lambda x: x[1])
                    logging.info("可用的缓存IP (按延迟排序):")
                    for ip, delay in sorted_ips:
                        logging.info(f"  IP: {ip}, 延迟: {delay:.0f}ms")
                        if target_delay and delay < target_delay:
                            logging.info(f"找到低延迟IP: {ip}, 延迟: {delay:.0f}ms")
                            if callback:
                                await callback(ip)
                            return [ip, delay]
                    
                    # 记录缓存中延迟最低的IP
                    best_ip = sorted_ips[0]
                    logging.info(f"缓存中延迟最低的IP: {best_ip[0]}, 延迟: {best_ip[1]:.0f}ms")

        # 如果没有找到满足条件的IP，从远程获取并测试
        if target_delay or not best_ip:
            logging.info("从远程获取IP列表")
            all_ips = []
            for ip_type, url in self.ip_urls.items():
                ips = await self._fetch_remote_ips(url)
                logging.info(f"远程获取到 {len(ips)} 个 {ip_type} 地址")
                all_ips.extend(ips)

            if all_ips:
                # 分批并发测试IP
                batch_size = 200
                valid_ips = []
                total_batches = (len(all_ips) + batch_size - 1) // batch_size

                for batch_num in range(total_batches):
                    start_idx = batch_num * batch_size
                    end_idx = min((batch_num + 1) * batch_size, len(all_ips))
                    batch_ips = all_ips[start_idx:end_idx]
                    
                    logging.info(f"测试第 {batch_num + 1}/{total_batches} 批IP ({len(batch_ips)}个)")
                    tasks = [self.test_ip(ip) for ip in batch_ips]
                    
                    try:
                        results = await asyncio.wait_for(
                            asyncio.gather(*tasks, return_exceptions=True),
                            timeout=30
                        )
                        
                        # 处理这批结果
                        for ip, result in zip(batch_ips, results):
                            if isinstance(result, tuple) and result[0]:
                                valid_ips.append((ip, result[1]))
                                # 如果找到满足条件的IP，记录下来
                                if target_delay and result[1] < target_delay:
                                    if not best_ip or result[1] < best_ip[1]:
                                        best_ip = (ip, result[1])

                    except asyncio.TimeoutError:
                        logging.warning(f"第 {batch_num + 1} 批测试超时")
                        continue

                # 保存所有可用的IP到缓存
                if valid_ips:
                    all_valid_ips = [ip for ip, _ in valid_ips]
                    self._save_ips_to_cache(all_valid_ips)
                    logging.info(f"已将 {len(all_valid_ips)} 个可用IP保存到缓存")

                    # 如果还没有找到最佳IP，从所有测试结果中选择
                    if not best_ip:
                        best_ip = min(valid_ips, key=lambda x: x[1])

        # 使用找到的最佳IP
        if best_ip:
            logging.info(f"使用延迟最低的IP: {best_ip[0]}, 延迟: {best_ip[1]:.0f}ms")
            if callback:
                await callback(best_ip[0])
            return [best_ip[0], best_ip[1]]

        logging.warning("未找到任何可用的IP")
        return []

    async def close(self):
        """关闭会话"""
        if self.session:
            await self.session.close() 

    async def get_best_available_ip(self) -> Optional[str]:
        """获取延迟最低的可用IP，不考虑延迟阈值"""
        # 先检查缓存
        cached_ips = await self._load_cached_ips()
        if cached_ips:
            tasks = [self.test_ip(ip) for ip in cached_ips]
            results = await asyncio.gather(*tasks)
            valid_ips = [(ip, delay) for (ip, (is_valid, delay)) in zip(cached_ips, results) if is_valid]
            if valid_ips:
                best_ip = min(valid_ips, key=lambda x: x[1])
                logging.info(f"从缓存中找到最优IP: {best_ip[0]}, 延迟: {best_ip[1]:.0f}ms")
                return best_ip[0]

        # 从远程获取并测试所有IP
        all_ips = []
        for ip_type, url in self.ip_urls.items():
            ips = await self._fetch_remote_ips(url)
            all_ips.extend(ips)

        if not all_ips:
            return None

        # 分批测试所有IP
        batch_size = 200
        valid_ips = []
        total_batches = (len(all_ips) + batch_size - 1) // batch_size

        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min((batch_num + 1) * batch_size, len(all_ips))
            batch_ips = all_ips[start_idx:end_idx]
            
            try:
                tasks = [self.test_ip(ip) for ip in batch_ips]
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=10
                )
                
                for ip, result in zip(batch_ips, results):
                    if isinstance(result, tuple) and result[0]:
                        valid_ips.append((ip, result[1]))
            except asyncio.TimeoutError:
                logging.warning(f"第 {batch_num + 1} 批测试超时")
                continue

        if valid_ips:
            best_ip = min(valid_ips, key=lambda x: x[1])
            logging.info(f"找到最优IP: {best_ip[0]}, 延迟: {best_ip[1]:.0f}ms")
            self._save_ips_to_cache([best_ip[0]])
            return best_ip[0]

        return None 